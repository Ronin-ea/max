# -*- coding: utf-8 -*-
from max.MADObjects import MADBase
from max.rest.utils import canWriteInContexts
import datetime
from MADMax import MADMaxDB
from max.rest.utils import getUserIdFromTwitter, findKeywords, findHashtags
from max import DEFAULT_CONTEXT_PERMISSIONS


class Activity(MADBase):
    """
        An activitystrea.ms Activity object representation
    """
    collection = 'activity'
    unique = '_id'
    schema = {'_id':         dict(required=0),
              'actor':       dict(required=1),
              'verb':        dict(required=1),
              'object':      dict(required=1),
              'published':   dict(required=0),
              'contexts':    dict(required=0),
              'replies':     dict(required=0, default={'items': [], 'totalItems': 0}),
              'generator':   dict(required=0),
              }

    def buildObject(self):
        """
            Updates the dict content with the activity structure,
            with data parsed from the request
        """

        isPerson = isinstance(self.data['actor'], User)
        isContext = isinstance(self.data['actor'], Context)

        # XXX Assuming here we only support Person as actor
        # XXX Assuming here we only support Uri as context
        actorType = isPerson and 'person' or 'uri'
        ob = {'actor': {'objectType': actorType,
                        '_id': self.data['actor']['_id'],
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

        if 'generator' in self.data:
            ob['generator'] = self.data['generator']
        if 'contexts' in self.data:
            if isPerson:
                # When a person posts an activity it can be targeted
                # to multiple contexts. here we construct the basic info
                # of each context and store them in contexts key
                ob['contexts'] = []
                for cobject in self.data['contexts']:
                    wrapper = self.getObjectWrapper(cobject['objectType'])
                    chash = wrapper(cobject).getHash()
                    subscription = self.data['actor'].getSubscriptionByHash(chash)
                    #context = subscription.get('object')
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
            if key not in self.data and default:
                properties[key] = default
        self.update(properties)

    def addComment(self, comment):
        """
            Adds a comment to an existing activity and updates refering activity keywords and hashtags
        """
        self.addToList('replies', comment, allow_duplicates=True)

        activity_keywords = self.object.setdefault('_keywords', [])
        activity_keywords.extend(comment.get('_keywords', []))
        activity_keywords = list(set(activity_keywords))

        activity_hashtags = self.object.setdefault('_hashtags', [])
        activity_hashtags.extend(comment.get('_hashtags', []))
        activity_hashtags = list(set(activity_hashtags))

        self.mdb_collection.update({'_id': self['_id']},
                                   {'$set': {'object._keywords': activity_keywords,
                                             'object._hashtags': activity_hashtags}}
                                   )

    def _on_create_custom_validations(self):
        """
            Perform custom validations on the Activity Object

            * If the actor is a person, check wether can write in all contexts
            * If the actor is a context, check if the context is the same
        """
        # If we are updating, we already have all data on the object, so we read self directly
        result = True
        if isinstance(self.data['actor'], User):
            wrapped_contexts = [self.getObjectWrapper(context['objectType'])(context) for context in self.data.get('contexts', [])]
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
              'username':     dict(required=1),
              'displayName':  dict(user_mutable=1),
              'last_login':   dict(),
              'following':    dict(default={'items': [], 'totalItems': 0}),
              'subscribedTo': dict(default={'items': [], 'totalItems': 0}),
              'published':    dict(),
              'twitterUsername':    dict(user_mutable=1),
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
            elif default:
                properties[key] = default

        ob.update(properties)
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
        #XXX TODO Check authentication method, and if is oauth, check if user can auto join the context.
        subscription = context.prepareUserSubscription()
        self.addToList('subscribedTo', subscription, safe=False)

    def removeSubscription(self, url):
        """
            Adds a comment to an existing activity
        """
        self.deleteFromList('subscribedTo', url)

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
              'object':             dict(required=1),
              'hash':               dict(),
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
                                                  'join': DEFAULT_CONTEXT_PERMISSIONS['join'],
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
            elif default:
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

    def removeUserSubscriptions(self):
        """
        """
        # update object from "items" that matches hash
        criteria = {'subscribedTo.items.hash': self.hash}

         # deletes context from subcription list
        what = {'$pull': {'subscribedTo.items': {'hash': self.hash}}}

