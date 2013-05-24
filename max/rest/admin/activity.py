# -*- coding: utf-8 -*-
from pyramid.view import view_config
from pyramid.httpexceptions import HTTPNoContent

from max.models import Activity
from max.oauth2 import oauth2
from max.decorators import MaxResponse, requirePersonActor, requireContextActor
from max.MADMax import MADMaxDB
from max.rest.ResourceHandlers import JSONResourceEntity
from max.rest.ResourceHandlers import JSONResourceRoot
from max.exceptions import ObjectNotFound
from max.rest.utils import searchParams


@view_config(route_name='user_activities', request_method='GET', restricted=['Manager'])
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor(force_own=False)
def getUserActivities(context, request):
    """
         /people/{username}/activities

         Return all activities generated by a user.
    """
    mmdb = MADMaxDB(context.db)
    query = {}
    query['actor.username'] = request.actor['username']
    query['verb'] = 'post'
    chash = request.params.get('context', None)
    if chash:
        query['contexts.hash'] = chash

    is_head = request.method == 'HEAD'
    activities = mmdb.activity.search(query, sort="_id", keep_private_fields=False, flatten=1, count=is_head, **searchParams(request))

    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@view_config(route_name='user_activities', request_method='POST', restricted='Manager')
@MaxResponse
@oauth2(['widgetcli'])
@requirePersonActor(force_own=False)
def addAdminUserActivity(context, request):
    """
         /people|contexts/{username|hash}/activities

         Add activity impersonated as a valid MAX user or context
    """
    rest_params = {'actor': request.actor,
                   'verb': 'post'}

    # Initialize a Activity object from the request
    newactivity = Activity()
    newactivity.fromRequest(request, rest_params=rest_params)

    # New User
    code = 201
    activity_oid = newactivity.insert()
    newactivity['_id'] = activity_oid

    handler = JSONResourceEntity(newactivity.flatten(), status_code=code)
    return handler.buildResponse()


@view_config(route_name='context_activities', request_method='POST', restricted='Manager')
@MaxResponse
@oauth2(['widgetcli'])
@requireContextActor
def addAdminContextActivity(context, request):
    """
         /people|contexts/{username|hash}/activities

         Add activity impersonated as a valid MAX user or context
    """
    rest_params = {'actor': request.actor,
                   'verb': 'post'}

    # Initialize a Activity object from the request
    newactivity = Activity()
    newactivity.fromRequest(request, rest_params=rest_params)

    code = 201
    activity_oid = newactivity.insert()
    newactivity['_id'] = activity_oid

    handler = JSONResourceEntity(newactivity.flatten(), status_code=code)
    return handler.buildResponse()


@view_config(route_name='activities', request_method='GET', restricted='Manager')
@MaxResponse
@oauth2(['widgetcli'])
def getActivities(context, request):
    """
    """
    mmdb = MADMaxDB(context.db)
    is_head = request.method == 'HEAD'
    activities = mmdb.activity.search({'verb': 'post'}, flatten=1, count=is_head, **searchParams(request))
    handler = JSONResourceRoot(activities, stats=is_head)
    return handler.buildResponse()


@view_config(route_name='activity', request_method='DELETE', restricted='Manager')
@MaxResponse
@oauth2(['widgetcli'])
def deleteActivity(context, request):
    """
    """
    mmdb = MADMaxDB(context.db)
    activityid = request.matchdict.get('activity', None)
    try:
        found_activity = mmdb.activity[activityid]
    except:
        raise ObjectNotFound("There's no activity with id: %s" % activityid)

    found_activity.delete()
    return HTTPNoContent()
