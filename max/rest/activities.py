# -*- coding: utf-8 -*-
from max.MADMax import MADMaxDB
from max.decorators import MaxResponse
from max.decorators import requirePersonActor
from max.exceptions import Forbidden
from max.exceptions import ObjectNotFound
from max.exceptions import Unauthorized
from max.models import Activity
from max.oauth2 import oauth2
from max.rest.ResourceHandlers import JSONResourceEntity
from max.rest.ResourceHandlers import JSONResourceRoot
from max.rest.utils import searchParams
from max.rest.sorting import SORT_METHODS

from pyramid.httpexceptions import HTTPGone
from pyramid.httpexceptions import HTTPNoContent
from pyramid.httpexceptions import HTTPNotImplemented
from pyramid.response import Response
from pyramid.view import view_config

from base64 import b64encode
from bson import ObjectId

import re


@view_config(route_name='user_activities', request_method='GET')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def getUserActivities(context, request):
    """
         Return all activities generated by a user.

         :rest username The user that posted the activities
    """
    mmdb = MADMaxDB(context.db)
    query = {}
    query['actor.username'] = request.actor['username']
    query['verb'] = 'post'
    chash = request.params.get('context', None)
    if chash:
        query['contexts.hash'] = chash

    is_head = request.method == 'HEAD'
    activities = mmdb.activity.search(query, sort="_id", keep_private_fields=False, squash=['favorites', 'likes'], flatten=1, count=is_head, **searchParams(request))

    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@view_config(route_name='user_activities', request_method='POST')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def addUserActivity(context, request):
    """
        Adds a post to the user activities

        :rest username The username that will own the activity

        :query* {"object": {"objectType": "note", "content": ""}} The content of the activity
        :query {"contexts": {"objectType": "context", "url": ""}} The context of the activity
        :query {"generator": ""} The generator of the activity (i.e. "Twitter")
    """
    rest_params = {'actor': request.actor,
                   'verb': 'post'}

    # Initialize a Activity object from the request
    newactivity = Activity()
    newactivity.fromRequest(request, rest_params=rest_params)

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


@view_config(route_name='context_activities', request_method='GET')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def getActivities(context, request):
    """
         Returns all the activities posted on a context

         :rest hash The hash of the context url where the activties where posted
    """
    chash = request.matchdict.get('hash', None)
    mmdb = MADMaxDB(context.db)

    # subscribed Uri contexts with read permission
    subscribed_uris = [ctxt['url'] for ctxt in request.actor.subscribedTo if 'read' in ctxt.get('permissions', []) and ctxt['objectType'] == 'context']

    # get the defined read context
    result_contexts = mmdb.contexts.getItemsByhash(chash)
    if result_contexts:
        rcontext = result_contexts[0]
    else:
        raise ObjectNotFound("Context with hash %s not found inside contexts" % (chash))
    url = rcontext['url']

    # regex query to find all contexts within url
    escaped = re.escape(url)
    url_regex = {'$regex': '^%s' % escaped}

    # search all contexts with public read permissions within url
    query = {'permissions.read': 'public', 'url': url_regex}
    public = [result.url for result in mmdb.contexts.search(query, show_fields=['url'])]

    query = {}                                                     # Search
    query.update({'verb': 'post'})                                 # 'post' activities
    query.update({'contexts.url': url_regex})                      # equal or child of url

    contexts_query = []
    if subscribed_uris:
        subscribed_query = {'contexts.url': {'$in': subscribed_uris}}  # that are subscribed contexts
        contexts_query.append(subscribed_query)                    # with read permission

    if public:                                                     # OR
        public_query = {'contexts.url': {'$in': public}}
        contexts_query.append(public_query)                        # pubic contexts

    is_head = request.method == 'HEAD'

    if contexts_query:
        query.update({'$or': contexts_query})

        sort_type = request.params.get('sortBy', 'activities')
        sort_method = SORT_METHODS[sort_type]
        activities = sort_method(request, mmdb, query, is_head)

    else:
        # we have no public contexts and we are not subscribed to any context, so we
        # won't get anything
        raise Forbidden("You don't have permission to see anyting in this context and it's child")

    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@view_config(route_name='activity', request_method='GET')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def getActivity(context, request):
    """
         Returns a single activity

         :rest activity The id of the activity
    """

    mmdb = MADMaxDB(context.db)
    activity_oid = request.matchdict['activity']
    activity = mmdb.activity[activity_oid].flatten()

    handler = JSONResourceEntity(activity)
    return handler.buildResponse()


@view_config(route_name='activity', request_method='DELETE')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def deleteActivity(context, request):
    """
         Deletes a single activity

         :rest activity The id of the activity
    """
    mmdb = MADMaxDB(context.db)
    activityid = request.matchdict.get('activity', None)
    try:
        found_activity = mmdb.activity[activityid]
    except:
        raise ObjectNotFound("There's no activity with id: %s" % activityid)

    # Check if the user can delete the activity
    if found_activity.deletable:
        found_activity.delete()
    else:
        raise Unauthorized("You're not the owner of this activity, so you can't delete it")

    return HTTPNoContent()


@view_config(route_name='flag', request_method='POST')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def flagActivity(context, request):
    """
         Sets the flagged mark on an activity
    """
    mmdb = MADMaxDB(context.db)
    activityid = request.matchdict.get('activity', None)
    try:
        found_activity = mmdb.activity[activityid]
    except:
        raise ObjectNotFound("There's no activity with id: %s" % activityid)

    # Check if the activity is flaggable by the actor
    if found_activity.get('contexts', []):
        ctxt = found_activity.contexts[0]
        subscription = request.actor.getSubscription(ctxt)
        if 'flag' not in subscription['permissions']:
            raise Unauthorized("You are not allowed to flag this activity.")
    else:
        raise Forbidden("Only context activities can be flagged.")

    found_activity.flag()
    found_activity.save()

    handler = JSONResourceEntity(found_activity.flatten())
    return handler.buildResponse()


@view_config(route_name='flag', request_method='DELETE')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def unflagActivity(context, request):
    """
         Unsets the flagged mark on an activity
    """
    mmdb = MADMaxDB(context.db)
    activityid = request.matchdict.get('activity', None)
    try:
        found_activity = mmdb.activity[activityid]
    except:
        raise ObjectNotFound("There's no activity with id: %s" % activityid)

    # Check if the activity is flaggable by the actor
    if found_activity.get('contexts', []):
        ctxt = found_activity.contexts[0]
        subscription = request.actor.getSubscription(ctxt)
        if 'flag' not in subscription['permissions']:
            raise Unauthorized("You are not allowed to unflag this activity.")
    else:
        raise Forbidden("Only context activities can be unflagged.")

    found_activity.unflag()
    found_activity.save()

    handler = JSONResourceEntity(found_activity.flatten())
    return handler.buildResponse()


@view_config(route_name='activity', request_method='PUT')
def modifyActivity(context, request):
    """
    """
    return HTTPNotImplemented()  # pragma: no cover


@view_config(route_name='activity_image', request_method='GET')
@view_config(route_name='activity_image_sizes', request_method='GET')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def getActivityImageAttachment(context, request):
    """
        Returns an image from the local repository.

        :rest activity The id of the activity
        :rest size The named size of the activity, defaults to large
    """

    activity_id = request.matchdict.get('activity', '')
    message = Activity()
    message.fromDatabase(ObjectId(activity_id))

    file_size = request.matchdict.get('size', 'full')
    image, mimetype = message.getImage(size=file_size)

    if image is not None:
        if request.headers.get('content-type', '') == 'application/base64':
            image = b64encode(image)
            mimetype = 'application/base64'

        response = Response(image, status_int=200)
        response.content_type = mimetype
    else:
        response = HTTPGone()

    return response


@view_config(route_name='activity_file_download', request_method='GET')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor
def getActivityFileAttachment(context, request):
    """
        Returns a file from the local repository.

        :rest activity The id of the activity
    """
    activity_id = request.matchdict.get('activity', '')
    activity = Activity()
    activity.fromDatabase(ObjectId(activity_id))

    file_data, mimetype = activity.getFile()

    if file_data is not None:
        response = Response(file_data, status_int=200)
        response.content_type = mimetype
        filename = activity['object'].get('filename', activity_id)
        response.headers.add('Content-Disposition', 'attachment; filename={}'.format(filename))
    else:
        response = HTTPGone()

    return response
