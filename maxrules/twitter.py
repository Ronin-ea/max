#!/usr/bin/env python

import sys
import optparse
#from getpass import getpass
from textwrap import TextWrapper
import pymongo
from max.MADMax import MADMaxCollection
import tweepy

# CONFIG
twitter_generator_name = 'Twitter'


def main(argv=sys.argv, quiet=False):
    command = MaxTwitterRulesRunner(argv, quiet)
    return command.run()


class StreamWatcherListener(tweepy.StreamListener):

    status_wrapper = TextWrapper(width=60, initial_indent='    ', subsequent_indent='    ')

    def on_status(self, status):
        try:
            print self.status_wrapper.fill(status.text)
            print '\n %s  %s  via %s\n' % (status.author.screen_name, status.created_at, status.source)
            # Insert the new data in MAX
            from maxrules.tasks import processTweet
            processTweet.delay(status.author.screen_name, status.text)
        except:
            # Catch any unicode errors while printing to console
            # and just ignore them to avoid breaking application.
            pass

    def on_error(self, status_code):
        print 'An error has occured! Status code = %s' % status_code
        return True  # keep stream alive

    def on_timeout(self):
        print 'Snoozing Zzzzzz'


class MaxTwitterRulesRunner(object):
    verbosity = 1  # required
    description = "Max rules runner."
    usage = "usage: %prog [options]"
    parser = optparse.OptionParser(usage, description=description)
    parser.add_option('-u', '--twitter-username',
                      dest='username',
                      type='string',
                      action='append',
                      help=("Twitter username"))
    parser.add_option('-p', '--twitter-password',
                      dest='password',
                      type='string',
                      action='append',
                      help=('Twitter password'))
    parser.add_option('-d', '--mongodb-url',
                      dest='mongodb_url',
                      type='string',
                      action='append',
                      help=('Twitter password'))
    parser.add_option('-n', '--mongodb-name',
                      dest='mongodb_db_name',
                      type='string',
                      action='append',
                      help=('Twitter password'))

    def __init__(self, argv, quiet=False):
        self.quiet = quiet
        self.options, self.args = self.parser.parse_args(argv[1:])

    def run(self):
        if not self.options.username or not self.options.password:
            self.out('You must provide a valid username and password.')
            return 2
        # Prompt for login credentials and setup stream object
        #username = raw_input('Twitter username: ')
        #password = getpass('Twitter password: ')
        auth = tweepy.auth.BasicAuthHandler(self.options.username[0], self.options.password[0])
        stream = tweepy.Stream(auth, StreamWatcherListener(), timeout=None)

        #follow_list = raw_input('Users to follow (comma separated): ').strip()
        #track_list = raw_input('Keywords to track (comma seperated):').strip()

        # Querying the BBDD for users to follow.
        conn = pymongo.Connection(self.options.mongodb_url[0])
        db = conn[self.options.mongodb_db_name[0]]
        contexts_with_twitter_username = db.contexts.find({"twitterUsernameId": {"$exists": True}})
        follow_list = [users_to_follow.get('twitterUsernameId') for users_to_follow in contexts_with_twitter_username]
        contexts_with_twitter_username.rewind()
        readable_follow_list = [users_to_follow.get('twitterUsername') for users_to_follow in contexts_with_twitter_username]

        # Hardcoded global hashtag(s)
        track_list = ['#upc', ]

        print "Listening to this Twitter hashtags: %s" % str(track_list)
        print "Listening to this Twitter userIds: %s" % str(readable_follow_list)

        stream.filter(follow=follow_list, track=track_list)

    def out(self, msg):  # pragma: no cover
        if not self.quiet:
            print(msg)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print '\nGoodbye!'
