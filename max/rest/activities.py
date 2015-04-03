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


@endpoint(route_name='user_activities', request_method='GET', requires_actor=True, permission=list_activities)
def getUserActivities(user, request):
    """
        Get user activities

        Returns all post activities generated by a user in his timeline or contexts.
    """
    query = {}
    query['actor.username'] = request.actor['username']
    query['verb'] = 'post'
    chash = request.params.get('context', None)
    if chash:
        query['contexts.hash'] = chash

    is_head = request.method == 'HEAD'
    activities = request.db.activity.search(query, keep_private_fields=False, flatten=1, count=is_head, **searchParams(request))

    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@endpoint(route_name='activities', request_method='GET', requires_actor=True, permission=list_activities)
def getGlobalActivities(context, request):
    """
        Get global activities

        Returns all post activities generated in the system by anyone.
    """
    is_head = request.method == 'HEAD'
    activities = request.db.activity.search({'verb': 'post'}, flatten=1, count=is_head, **searchParams(request))
    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@endpoint(route_name='context_activities', request_method='GET', requires_actor=True, permission=list_activities)
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

    contexts_query = []

    # Check if we have permission to unrestrictely view activities from recursive contexts:
    can_list_activities_unsubscribed = isinstance(request.has_permission(list_activities_unsubscribed), ACLAllowed)

    # If we can't view unsubcribed contexts, filter from which contexts we get activities by listing
    # the contexts that the user has read permission on his subscriptions. Public contexts
    # will be added if this condition is met, as if we're unrestricted, main query already includes them all

    if not can_list_activities_unsubscribed:

        def get_valid_subscriptions():
            subscriptions = []
            for subscription in request.actor.subscribedTo:
                if 'read' in subscription.get('permissions', []) \
                   and subscription['objectType'] == 'context'\
                   and subscription['url'].startswith(url):
                    subscriptions.append(subscription['url'])
            return subscriptions

        # XXX Filter subscriptions by url prefix PLEASE:
        subscribed_query = {'contexts.url': {'$in': get_valid_subscriptions()}}
        contexts_query.append(subscribed_query)

        # We'll include also all contexts that are public whitin the url
        public_query = {'permissions.read': 'public', 'url': url_regex}
        public_contexts = [result.url for result in request.db.contexts.search(public_query, show_fields=['url'])]

        if public_contexts:
            contexts_query.append({'contexts.url': {'$in': public_contexts}})

    if contexts_query:
        query.update({'$or': contexts_query})

    activities = sorted_query(request, request.db.activity, query, flatten=1)

    if contexts_query or can_list_activities_unsubscribed:
        activities = sorted_query(request, request.db.activity, query, flatten=1)

    is_head = request.method == 'HEAD'
    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@endpoint(route_name='context_activities', request_method='POST', requires_actor=True, permission=add_activity)
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
        'object.content': newactivity['object']['content'],
        'published': {'$gt': newactivity.published - timedelta(minutes=1)},
        'contexts.hash': context.hash
    }

    duplicated = request.db.activity.search(query)

    if duplicated:
        code = 200
        newactivity = duplicated[0]
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

    handler = JSONResourceEntity(newactivity.flatten(squash=['keywords']), status_code=code)
    return handler.buildResponse()


@endpoint(route_name='user_activities', request_method='POST', requires_actor=True, permission=add_activity)
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
        'actor.username': request.actor.username,
        'object.content': newactivity['object'].get('content', ''),
        'published': {'$gt': newactivity.published - timedelta(minutes=1)},
        'contexts': {'$exists': False}
    }

    duplicated = request.db.activity.search(query)

    if duplicated:
        code = 200
        newactivity = duplicated[0]
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

    handler = JSONResourceEntity(newactivity.flatten(squash=['keywords']), status_code=code)
    return handler.buildResponse()


@endpoint(route_name='activity', request_method='GET', requires_actor=True, permission=view_activity)
def getActivity(activity, request):
    """
        Get an activity

         :rest activity The id of the activity
    """
    handler = JSONResourceEntity(activity.flatten())
    return handler.buildResponse()


@endpoint(route_name='activity', request_method='DELETE', requires_actor=True, permission=delete_activity)
def deleteActivity(activity, request):
    """
        Delete an activity

         :rest activity The id of the activity
    """
    activity.delete()
    return HTTPNoContent()


@endpoint(route_name='activity_image', request_method='GET', requires_actor=True, permission=view_activity)
@endpoint(route_name='activity_image_sizes', request_method='GET', requires_actor=True, permission=view_activity)
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


@endpoint(route_name='activity_file_download', request_method='GET', requires_actor=True, permission=view_activity)
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
