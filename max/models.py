# -*- coding: utf-8 -*-
from max.MADObjects import MADBase
from max.rest.utils import canWriteInContexts
import datetime
from MADMax import MADMaxDB, MADMaxCollection
from max.rest.utils import getUserIdFromTwitter, findKeywords, findHashtags
from max import DEFAULT_CONTEXT_PERMISSIONS


class Activity(MADBase):
    """
        An activitystrea.ms Activity object representation
    """
    collection = 'activity'
    unique = '_id'
    schema = {'_id':         dict(required=0),
              'objectType':  dict(required=0, default='activity'),
              'actor':       dict(required=1),
              'verb':        dict(required=1),
              'object':      dict(required=1),
              'published':   dict(required=0),
              'commented':   dict(required=0),
              'contexts':    dict(required=0),
              'replies':     dict(required=0, default={'items': [], 'totalItems': 0}),
              'generator':   dict(required=0, default=None),
              }

    def setDates(self):
        super(Activity, self).setDates()
        self['commented'] = datetime.datetime.utcnow()

    def buildObject(self):
        """
            Updates the dict content with the activity structure,
            with data parsed from the request
        """

        isPerson = isinstance(self.data['actor'], User)
        isContext = isinstance(self.data['actor'], Context)

        # XXX Assuming here we only support Person as user
        # XXX Assuming here we only support Uri as context
        actorType = isPerson and 'person' or 'uri'
        ob = {'actor': {'objectType': actorType,
                        'displayName': self.data['actor']['displayName'],
                        },
              'verb': self.data['verb'],
              'object': None,
              }
        if isPerson:
            ob['actor']['username'] = self.data['actor']['username']
        elif isContext:
            ob['actor']['hash'] = self.data['actor']['hash']
            ob['actor']['url'] = self.data['actor']['object']['url']

        wrapper = self.getObjectWrapper(self.data['object']['objectType'])
        subobject = wrapper(self.data['object'])
        ob['object'] = subobject

        #Append actor as username if object has keywords and actor is a Person
        if ob['object'].get('_keywords', None):
            if isPerson:
                ob['object']['_keywords'].append(self.data['actor']['username'])

        if 'contexts' in self.data:
            if isPerson:
                # When a person posts an activity it can be targeted
                # to multiple contexts. here we construct the basic info
                # of each context and store them in contexts key
                ob['contexts'] = []
                for cobject in self.data['contexts']:
                    #if hash is directly specified, use it to speed up process
                    if 'hash' in cobject.keys():
                        chash = cobject.get('hash')
                    else:
                        wrapper = self.getObjectWrapper(cobject['objectType'])
                        chash = wrapper(cobject).getHash()
                    subscription = dict(self.data['actor'].getSubscriptionByHash(chash))
                    # Clean unnecessary fields
                    del subscription['tags']
                    del subscription['published']
                    del subscription['permissions']
                    del subscription['id']
                    ob['contexts'].append(subscription)
            if isContext:
                # When a context posts an activity it can be posted only
                # to itself, so add it directly
                    ob['contexts'] = [self.data['actor'], ]
        self.update(ob)

        # Set defaults
        properties = {}
        for key, value in self.schema.items():
            default = value.get('default', None)
            # Value is in the request but not set yet, set it please.
            if key in self.data and key not in self:
                properties[key] = self.data[key]
            # Value is not in the request and we have a default, set it please
            elif 'default' in value.keys():
                properties[key] = default
        self.update(properties)

    def modifyActivity(self, properties):
        """Update the Activity object with the given properties"""

        self.updateFields(properties)
        self.save()

    def addComment(self, comment):
        """
            Adds a comment to an existing activity and updates refering activity keywords and hashtags
        """

        #Clean innecessary fields
        non_needed_actor_fields = ['subscribedTo', 'following', 'last_login', '_id', 'published', 'twitterUsername']
        for fieldname in non_needed_actor_fields:
            if fieldname in comment['actor']:
                del comment['actor'][fieldname]

        self.addToList('replies', comment, allow_duplicates=True)

        activity_keywords = self.object.setdefault('_keywords', [])
        activity_keywords.extend(comment.get('_keywords', []))
        self.object['_keywords'] = list(set(activity_keywords))

        activity_hashtags = self.object.setdefault('_hashtags', [])
        activity_hashtags.extend(comment.get('_hashtags', []))
        self.object['_hashtags'] = list(set(activity_hashtags))
        self.commented = comment['published']

        self.save()

    def _on_create_custom_validations(self):
        """
            Perform custom validations on the Activity Object

            * If the actor is a person, check wether can write in all contexts
            * If the actor is a context, check if the context is the same
        """
        contextsdb = MADMaxCollection(self.mdb_collection.database.contexts, query_key='hash')
        # If we are updating, we already have all data on the object, so we read self directly
        result = True
        if isinstance(self.data['actor'], User):
            wrapped_contexts = []
            for context in self.data.get('contexts', []):
                if 'hash' in context.keys():
                    wrapped = contextsdb[context['hash']]['object']
                else:
                    wrapped = self.getObjectWrapper(context['objectType'])(context)
                wrapped_contexts.append(wrapped)

            result = result and canWriteInContexts(self.data['actor'], wrapped_contexts)
        if self.data.get('contexts', None) and isinstance(self.data['actor'], Context):
            result = result and self.data['actor']['object']['url'] == self.data.get('contexts')[0]
        return result


class User(MADBase):
    """
        An activitystrea.ms User object representation
    """
    collection = 'users'
    unique = 'username'
    schema = {'_id':          dict(),
              'objectType':  dict(required=0, default='person'),
              'username':     dict(required=1),
              'displayName':  dict(user_mutable=1),
              'last_login':   dict(),
              'following':    dict(default={'items': [], 'totalItems': 0}),
              'subscribedTo': dict(default={'items': [], 'totalItems': 0}),
              'published':    dict(),
              'twitterUsername':    dict(user_mutable=1,
                                         formatters=['stripTwitterUsername'],
                                         validators=['isValidTwitterUsername']
                                         )
              }

    def buildObject(self):
        """
            Updates the dict content with the user structure,
            with data from the request
        """
        ob = {'last_login': datetime.datetime.utcnow()}

        # Update properties from request data if defined in schema
        # Also create properties with a default value defined
        properties = {}
        for key, value in self.schema.items():
            default = value.get('default', None)
            if key in self.data:
                properties[key] = self.data[key]
            elif 'default' in value.keys():
                properties[key] = default

        ob.update(properties)
        ob['displayName'] = ob.get('displayName', ob.get('username', 'nobody'))
        self.update(ob)

    def addFollower(self, person):
        """
            Adds a follower to the list
        """
        self.addToList('following', person)

    def addSubscription(self, context):
        """
            Adds a comment to an existing activity
        """
        subscription = context.prepareUserSubscription()
        self.addToList('subscribedTo', subscription, safe=False)

    def removeSubscription(self, chash):
        """
            Adds a comment to an existing activity
        """
        self.deleteFromList('subscribedTo', {'hash': chash})

    def modifyUser(self, properties):
        """Update the user object with the given properties"""

        self.updateFields(properties)
        self.save()

    def grantPermission(self, subscription, permission):
        """
        """
        criteria = {}
        criteria.update({'subscribedTo.items.hash': subscription['hash']})   # update object from "items" that matches hash
        criteria.update({'_id': self._id})                 # of collection entry with _id

         # Add permission to permissions array, of matched object of "items"
        what = {'$addToSet': {'subscribedTo.items.$.permissions': permission}}

        self.mdb_collection.update(criteria, what)

    def revokePermission(self, subscription, permission):
        """
        """
        criteria = {}
        criteria.update({'subscribedTo.items.hash': subscription['hash']})   # update object from "items" that matches hash
        criteria.update({'_id': self._id})                 # of collection entry with _id

         # deletes permission from permissions array, of matched object of "items"
        what = {'$pull': {'subscribedTo.items.$.permissions': permission}}

        self.mdb_collection.update(criteria, what)

    def getSubscriptionByHash(self, chash):
        """
        """
        context_map = {context['hash']: context for context in self.subscribedTo['items']}
        return context_map.get(chash)


class Context(MADBase):
    """
        A max Context object representation
    """
    collection = 'contexts'
    unique = 'hash'
    schema = {'_id':                dict(),
              'objectType':         dict(required=0, default='context'),
              'tags':               dict(default=[]),
              'object':             dict(required=1,
                                         operations_mutable=1),
              'hash':               dict(),
              'displayName':        dict(operations_mutable=1),
              'published':          dict(),
              'twitterHashtag':     dict(operations_mutable=1,
                                         formatters=['stripHash'],
                                         validators=['isValidHashtag'],
                                         ),
              'twitterUsername':    dict(operations_mutable=1,
                                         formatters=['stripTwitterUsername'],
                                         validators=['isValidTwitterUsername'],
                                         ),
              'twitterUsernameId':  dict(operations_mutable=1),
              'permissions':        dict(default={'read': DEFAULT_CONTEXT_PERMISSIONS['read'],
                                                  'write': DEFAULT_CONTEXT_PERMISSIONS['write'],
                                                  'subscribe': DEFAULT_CONTEXT_PERMISSIONS['subscribe'],
                                                  'invite': DEFAULT_CONTEXT_PERMISSIONS['invite']
                                                  }
                                         ),
              }

    def buildObject(self):
        """
            Updates the dict content with the context structure,
            with data from the request
        """

        # Update properties from request data if defined in schema
        # Also create properties with a default value defined
        ob = {}
        properties = {}
        for key, value in self.schema.items():
            default = value.get('default', None)
            if key in self.data:
                properties[key] = self.data[key]
            elif 'default' in value.keys():
                properties[key] = default
        ob.update(properties)

        # If creating with the twitterUsername, get its Twitter ID
        if self.data.get('twitterUsername', None):
            ob['twitterUsernameId'] = getUserIdFromTwitter(self.data['twitterUsername'])
        dataobject = self.data.get('object', {'objectType': 'uri'})
        wrapper = self.getObjectWrapper(dataobject.get('objectType', 'uri'))
        subobject = wrapper(dataobject)
        ob['object'] = subobject

        ob['hash'] = subobject.getHash()
        #Set displayName only if it's not specified
        ob['displayName'] = ob.get('displayName', subobject.getDisplayName())

        self.update(ob)

    def modifyContext(self, properties):
        """Update the user object with the given properties"""
        # If updating the twitterUsername, get its Twitter ID
        if properties.get('twitterUsername', None):
            properties['twitterUsernameId'] = getUserIdFromTwitter(properties['twitterUsername'])

        self.updateFields(properties)

        if self.get('twitterUsername', None) is None and self.get('twitterUsernameId', None) is not None:
            del self['twitterUsernameId']

        self.save()

    def subscribedUsers(self):
        """
        """
        criteria = {'subscribedTo.items.hash': self.hash}
        subscribed_users = self.mdb_collection.database.users.find(criteria)
        return [user for user in subscribed_users]

    def prepareUserSubscription(self):
        """
        """
        subscription = self.flatten()
        permissions = subscription['permissions']

        #If we are subscribing the user, read permission is granted
        user_permissions = ['read']

        #Set other permissions based on context defaults
        if permissions.get('write', DEFAULT_CONTEXT_PERMISSIONS['write']) in ['subscribed', 'public']:
            user_permissions.append('write')
        if permissions.get('invite', DEFAULT_CONTEXT_PERMISSIONS['invite']) in ['subscribed']:
            user_permissions.append('invite')
        if permissions.get('subscribe', DEFAULT_CONTEXT_PERMISSIONS['subscribe']) in ['public']:
            user_permissions.append('unsubscribe')

        #Assign permissions to the subscription object before adding it
        subscription['permissions'] = user_permissions
        return subscription

    def updateUsersSubscriptions(self):
        """
        """
        # XXX TODO For now only updates displayName
        ids = [user['_id'] for user in self.subscribedUsers()]

        for obid in ids:
            criteria = {'_id': obid, 'subscribedTo.items.hash': self.hash}

                 # deletes context from subcription list
            what = {'$set': {'subscribedTo.items.$.displayName': self.displayName}}
            self.mdb_collection.database.users.update(criteria, what)

    def removeUserSubscriptions(self, users_to_delete=[]):
        """
            Removes all users subscribed to the context, or only specifiyed
            user if userd_to_delete is provided
        """
        usersdb = MADMaxCollection(self.mdb_collection.database.users)
        criteria = {'subscribedTo.items.hash': self.hash}
        users = usersdb.search(criteria)
        for user in users:
            if users_to_delete == [] or user.username in users_to_delete:
                user.removeSubscription(self.hash)

    def removeActivities(self, logical=False):
        """
            Removes all activity posted to a context. If logical is set to True
            Activities are not actually deleted, only marked as not visible
        """
        activitydb = MADMaxCollection(self.mdb_collection.database.activity)
        which_to_delete = {
            'contexts.hash': self.hash
        }
        activitydb.remove(which_to_delete, logical=logical)


class Security(MADBase):
    """
        The Security object representation
    """
    collection = 'security'
    unique = '_id'
    schema = {'_id':         dict(required=0),
              'roles':        dict(required=0)}
