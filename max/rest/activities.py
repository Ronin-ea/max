# -*- coding: utf-8 -*-
from max.models import Activity
from max.rest import JSONResourceEntity
from max.rest import JSONResourceRoot
from max.rest import endpoint
from max.rest.sorting import sorted_query
from max.utils import searchParams
from max.security.permissions import add_activity
from max.security.permissions import delete_activity
from max.security.permissions import list_activities
from max.security.permissions import list_activities_unsubscribed
from max.security.permissions import view_activity

from pyramid.httpexceptions import HTTPGone
from pyramid.httpexceptions import HTTPNoContent
from pyramid.response import Response
from pyramid.security import ACLAllowed

from base64 import b64encode
from bson import ObjectId
from datetime import timedelta

import re
import requests


def visible_user_activities_query(user, request, filter_non_shared=True):
    """
        Returns a query to return all activities posted by a user,
        filtered on visibility based on who's requesting
    """
    query = {}
    or_queries = []
    shared_contexts_activity_query = {}
    non_shared_contexts_activity_query = {}

    common_query = {}
    common_query['actor.username'] = request.actor['username']
    common_query['verb'] = 'post'
    common_query['visible'] = {'$ne': False}

    # Get context filter from request if present
    filtered_context_hash = request.params.get('context', None)

    # Prepare query to search all timeline (non-context) activity
    non_context_activity_query = {}
    non_context_activity_query.update(common_query)
    non_context_activity_query['contexts'] = {'$exists': False}

    # Determine which contexts are not shared between users and so
    # it's activities won't be visible
    user_subscribed_contexts = set([context['hash'] for context in user['subscribedTo']])
    creator_subscribed_contexts = set([context['hash'] for context in request.creator['subscribedTo']])

    # Filter list of searchable contexts based on the filtering paramter
    # If filtering is on, only shared and public contexts will be included
    if filter_non_shared:
        shared_contexts = list(user_subscribed_contexts.intersection(creator_subscribed_contexts))
        non_shared_contexts = list(user_subscribed_contexts - creator_subscribed_contexts)
    else:
        shared_contexts = list(user_subscribed_contexts)
        non_shared_contexts = []

    # Reduce the list of contexts to the filtered one if present.
    if filtered_context_hash in shared_contexts:
        shared_contexts = [filtered_context_hash]
    if filtered_context_hash in non_shared_contexts:
        non_shared_contexts = [filtered_context_hash]

    # Prepare query to search for all shared context activity
    if shared_contexts:
        shared_contexts_activity_query.update(common_query)
        shared_contexts_activity_query['contexts.hash'] = {'$in': shared_contexts}

    # Prepare query to search for all non_shared context public activity
    if non_shared_contexts:
        public_contexts = request.db.contexts.search({'permissions.write': 'public'}, flatten=1)
        public_contexts_hashes = [a['hash'] for a in public_contexts]
        if public_contexts_hashes:
            non_shared_contexts_activity_query.update(common_query)
            non_shared_contexts_activity_query['contexts.hash'] = {'$in': public_contexts_hashes}
    else:
        # Apply single context filter when not filtering by sharing contexts
        common_query['visible'] = {'$ne': False}

    # Contextless queries are nonsense if request is filtered
    if not filtered_context_hash:
        or_queries.append(non_context_activity_query)

    if shared_contexts:
        or_queries.append(shared_contexts_activity_query)

    if non_shared_contexts:
        or_queries.append(non_shared_contexts_activity_query)

    query = {
        "$or": or_queries
    }

    return query


@endpoint(route_name='user_activities', request_method='GET', permission=list_activities)
def getUserActivities(user, request):
    """
        Get user activities

        Returns all post visible activities generated by a user in his timeline or contexts.
        Include only contexts shared between user and creator if different
    """

    can_list_activities_unsubscribed = isinstance(request.has_permission(list_activities_unsubscribed), ACLAllowed)
    query = visible_user_activities_query(user, request, filter_non_shared=not can_list_activities_unsubscribed)

    is_head = request.method == 'HEAD'
    activities = request.db.activity.search(query, keep_private_fields=False, flatten=1, count=is_head, **searchParams(request))
    handler = JSONResourceRoot(request, activities, stats=is_head)
    return handler.buildResponse()


@endpoint(route_name='activities', request_method='GET', permission=list_activities)
def getGlobalActivities(context, request):
    """
        Get global activities

        Returns all post activities generated in the system by anyone.
    """
    is_head = request.method == 'HEAD'
    activities = request.db.activity.search({'verb': 'post'}, flatten=1, count=is_head, **searchParams(request))
    handler = JSONResourceRoot(request, activities, stats=is_head)
    return handler.buildResponse()


@endpoint(route_name='context_activities', request_method='GET', permission=list_activities)
def getContextActivities(context, request):
    """
        Get context activities

        Returns all the activities posted on a context

         :rest hash The hash of the context url where the activties where posted
    """
    url = context['url']

    # regex query to find all contexts within url
    escaped = re.escape(url)
    url_regex = {'$regex': '^%s' % escaped}

    # Search posts associated with contexts that have this context's
    # url as prefix a.k.a "recursive contexts"
    query = {}                                                     # Search
    query.update({'verb': 'post'})                                 # 'post' activities
    query.update({'contexts.url': url_regex})                      # equal or child of url

    # Check if we have permission to unrestrictely view activities from recursive contexts:
    can_list_activities_unsubscribed = isinstance(request.has_permission(list_activities_unsubscribed), ACLAllowed)

    # If we can't view unsubcribed contexts, filter from which contexts we get activities by listing
    # the contexts that the user has read permission on his subscriptions. Public contexts are only searched here
    # because if we can list_activities_unsubscribed, main query already includes them.

    readable_contexts_urls = []
    if not can_list_activities_unsubscribed:
        # Include all urls from subscriptions to contexts whose url
        # is a child of of main context url,
        for subscription in request.actor['subscribedTo']:
            if 'read' in subscription.get('permissions', []) \
               and subscription['objectType'] == 'context'\
               and subscription['url'].startswith(url):
                readable_contexts_urls.append(subscription['url'])

        # We'll include also all contexts that are public whitin the url
        public_query = {'permissions.read': 'public', 'url': url_regex}
        for result in request.db.contexts.search(public_query, show_fields=['url']):
            readable_contexts_urls.append(result['url'])

    # if any url collected, include it on the query
    if readable_contexts_urls:
        query['contexts.url'] = {'$in': readable_contexts_urls}

    activities = []
    # Execute search only if we have read permision on some contexts or we have usubscribed access to activities.
    if readable_contexts_urls or can_list_activities_unsubscribed:
        activities = sorted_query(request, request.db.activity, query, flatten=1)

    is_head = request.method == 'HEAD'
    handler = JSONResourceRoot(request, activities, stats=is_head)
    return handler.buildResponse()


@endpoint(route_name='context_activities', request_method='POST', permission=add_activity)
def addContextActivity(context, request):
    """
         Add a context activity

         If an actor is found on the request body it will be taken as the ownership of the activity, either
         the actor being a Person or a Context. If no actor specified on json payload, the current authenticated
         user will be taken as request.actor.
    """
    rest_params = {
        'verb': 'post',
        'contexts': [
            context
        ]
    }
    # Initialize a Activity object from the request
    newactivity = Activity.from_request(request, rest_params=rest_params)

    # Search if there's any activity from the same user with
    # the same actor in the last minute

    actor_id_key = 'actor.{}'.format(request.actor.unique)
    actor_id_value = request.actor.get(request.actor.unique)

    query = {
        actor_id_key: actor_id_value,
        'published': {'$gt': newactivity['published'] - timedelta(minutes=1)},
        'contexts.hash': context['hash'],
        'verb': 'post'
    }

    possible_duplicates = request.db.activity.search(query)
    duplicated = False
    for candidate in possible_duplicates:
        if candidate['object']['content'] == newactivity['object'].get('content', ''):
            duplicated = candidate
            break

    if duplicated:
        code = 200
        newactivity = duplicated
    else:
        # New activity
        code = 201
        if newactivity['object']['objectType'] == u'image' or \
           newactivity['object']['objectType'] == u'file':
            # Extract the file before saving object
            activity_file = newactivity.extract_file_from_activity()
            activity_oid = newactivity.insert()
            newactivity['_id'] = ObjectId(activity_oid)
            newactivity.process_file(request, activity_file)
            newactivity.save()
        else:
            activity_oid = newactivity.insert()
            newactivity['_id'] = ObjectId(activity_oid)

            try:
                text_activity = newactivity['object']['content']
                if u'He afegit' in text_activity[0:15] or \
                   u'He añadido' in text_activity[0:15] or \
                   u'I\'ve added' in text_activity[0:15]:
                    notifymail = False
                else:
                    notifymail = True

                if notifymail:
                    # Notify activity by email
                    community_url = newactivity['contexts'][0]['url']
                    site_url = '/'.join(str(community_url).split('/')[:-1])
                    url = site_url + '/api/notifymail'

                    payload = {"community_url": community_url,
                               "community_name": newactivity['contexts'][0]['displayName'],
                               "actor_displayName": newactivity['actor']['displayName'],
                               "activity_content": newactivity['object']['content'],
                               "content_type": newactivity['objectType']}

                    headers={'X-Oauth-Username': request.auth_headers[1],
                             'X-Oauth-Token': request.auth_headers[0],
                             'X-Oauth-Scope': request.auth_headers[2]}

                    res = requests.post(url, headers=headers, data=payload, verify=False)
            except:
                pass

    handler = JSONResourceEntity(request, newactivity.flatten(squash=['keywords']), status_code=code)
    return handler.buildResponse()


@endpoint(route_name='user_activities', request_method='POST', permission=add_activity)
def addUserActivity(user, request):
    """
         Add a timeline activity

         Add activity posted as {username}. User in url will be taken as the actor that will own
         the activity. When url {username} and authenticated user don't match, user must have special
         permissions to be able to impersoate the activity.

    """
    rest_params = {'actor': request.actor,
                   'verb': 'post'}

    # Initialize a Activity object from the request
    newactivity = Activity.from_request(request, rest_params=rest_params)

    # Search if there's any activity from the same user with
    # the same actor and without context
    query = {
        'actor.username': request.actor['username'],
        'published': {'$gt': newactivity['published'] - timedelta(minutes=1)},
        'contexts': {'$exists': False},
        'verb': 'post'
    }

    possible_duplicates = request.db.activity.search(query)
    duplicated = None
    for candidate in possible_duplicates:
        if candidate['object']['content'] == newactivity['object'].get('content', ''):
            duplicated = candidate
            break

    if duplicated:
        code = 200
        newactivity = duplicated
    else:
        # New activity
        code = 201
        if newactivity['object']['objectType'] == u'image' or \
           newactivity['object']['objectType'] == u'file':
            # Extract the file before saving object
            activity_file = newactivity.extract_file_from_activity()
            activity_oid = newactivity.insert()
            newactivity['_id'] = ObjectId(activity_oid)
            newactivity.process_file(request, activity_file)
            newactivity.save()
        else:
            activity_oid = newactivity.insert()
            newactivity['_id'] = activity_oid

    handler = JSONResourceEntity(request, newactivity.flatten(squash=['keywords']), status_code=code)
    return handler.buildResponse()


@endpoint(route_name='activity', request_method='GET', permission=view_activity)
def getActivity(activity, request):
    """
        Get an activity

         :rest activity The id of the activity
    """
    handler = JSONResourceEntity(request, activity.flatten())
    return handler.buildResponse()


@endpoint(route_name='activity', request_method='DELETE', permission=delete_activity)
def deleteActivity(activity, request):
    """
        Delete an activity

         :rest activity The id of the activity
    """
    activity.delete()
    return HTTPNoContent()


@endpoint(route_name='activity_image', request_method='GET', permission=view_activity)
@endpoint(route_name='activity_image_sizes', request_method='GET', permission=view_activity)
def getActivityImageAttachment(activity, request):
    """
        Get an activity image

        :rest activity The id of the activity
        :rest size The named size of the activity, defaults to large
    """

    file_size = request.matchdict.get('size', 'full')
    image, mimetype = activity.getImage(size=file_size)

    if image is not None:
        if request.headers.get('content-type', '') == 'application/base64':
            image = b64encode(image)
            mimetype = 'application/base64'

        response = Response(image, status_int=200)
        response.content_type = mimetype
    else:
        response = HTTPGone()

    return response


@endpoint(route_name='activity_file_download', request_method='GET', permission=view_activity)
def getActivityFileAttachment(activity, request):
    """
        Get an activity file

        :rest activity The id of the activity
    """
    file_data, mimetype = activity.getFile()

    if file_data is not None:
        response = Response(file_data, status_int=200)
        response.content_type = mimetype
        filename = activity['object'].get('filename', activity['_id'])
        response.headers.add('Content-Disposition', 'attachment; filename={}'.format(filename))
    else:
        response = HTTPGone()

    return response
