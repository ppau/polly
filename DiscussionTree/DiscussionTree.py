import tornado
from pymongo import DESCENDING, ASCENDING
from bson.code import Code
import motor
from datetime import datetime
import sys
import uuid

from AsyncException import AsyncException

#
#   A Sortable Discussion Tree stored in MongoDB
#    
#   Objectives:
#   1. Minimum response time for each user action.
#   2. Minimum communications overhead for each action.
#   3. Minimum database       overhead for each action.
#   4. Sortable by a variety of selected keys (time, size, author, priority, reputation etc.)
#      A default sort key may apply.
#   5. Sorting in a tree may apply differently to any specified level of the tree.
#   6. Sorting applies an individual sub-columns.
#
#   Consider the "Sample Tree Structure" shown below.
#   1. Elements (a,b,c,d) are the primary sub-column.
#   2. Elements (b.a, b.b, b.c) are a sub-column.
#   3. Elements (c.a, c.b, c.c) are a separate sub-column from (b.a, b.b, b.c) 
#        
#   Sample Tree Structure
#   a
#        a.a
#        a.b
#   b    
#        b.a
#        b.b
#        b.c 
#   c
#        c.a
#        c.b
#            c.b.a
#        c.c
#            c.c.a
#            c.c.b
#   d
#        d.a
#            d.a.a
#            d.a.b
#        d.b
#            d.b.a
#            d.b.b
#            d.b.c
#
#    Approach:
#    1. Store each entire sub-column in a single MongoDB Document.
#       The elements in-document will be stored in time order.
#       Inserts are therefore always to the end of the document, working to the MongoDB strength of in-place updates.
#    2. May eventually break out into archived sub-column segments to discard generally unwanted 
#       content on the primary sub-column at least. Unwanted because of age, but easily re-included on demand.
#    3. Sub-columns are actually the unit of user request. Each click-open of a tree branch shows a sub-column
#    4. Sort order may be selected separately per sub-column opened (time, size, author, priority, reputation etc.)
#    5. Cache large pre-sorted sub-columns at server
#    6. Deliver readable (fits to screen) chunks at a time to client by push
#    7. sub-column storage on disk is always in time-order inside document (working to the MongoDB strength of in-place updates.)
#    8. May pre-fetch first chunks of sub-columns below currently opened sub-column in client as optimization.
#
#
#    MongoDB PseudoReputation document format: 
#    {
#        'pseudo': 'Andrewd',
#        'repute': {
#            'a': {                        
#                'count': 4,              # 'a' reputation is 4
#                'b': {
#                      'count': 2,        # 'a.b' reputation is 2
#                }
#            }
#        }
#    }
#
#    MongoDB DiscussionTree document format: 
#    {
#        "_id"       : <uuid4>                  # Randomly generated uuid. When users add comments, the client request
#                                               # must specify this uuid of the comment they are replying to. This protects
#                                               # the tree against all sorts of hacks/bugs.  
#        "subtree_id": <DottedAlphaSubreeId>,   # Dotted tree hierarchy sub-tree reference as string. e.g.             "a.b.f"
#        "comments"  : [                        # Array position indicates next sub-tree level.       e.g. 0th entry is a.b.f.a
#            {
#                "child_id":  <uuid4>           # child_id is _id of child DiscussionTree document.
#                "time"    :  <Comment Time>    # Time the comment was written in UTC
#                "pseudo"  :  <UserPseudonym>   # Pseudonym of the user that wrote the comment.
#                "repute"  :  <reputation>      # The reputation of <UserPseudonym> at the time of the comment
#                                               # Incidentally, a more expensive option would be to allow sorting
#                                               # the comments by current reputation. Probably cached in memory.
#                "title"   :  <StringComment>   # The actual comment. Plain text only.
#                "text"    :  <StringComment>   # The actual comment. Allowing some simple (TBD) markup. Probably 'Markdown'.
#            },
#            ...
#        ],
#    }
#
#
def int_to_alpha(n):
    if n > 0:
        result = ''
        while n > 0:
            n,r = divmod(n, 26)
            result = chr(97+r) + result
    elif n == 0:
        result = 'a'
    else:
        raise Exception("int_to_alpha() doesn't do negative numbers")
    return result

def alpha_to_int(alpha):
    x = ''.join([chr(i) for i in range(97, 107)])
    n = 0
    for char in alpha:
        if char < 'a' or char > 'z':
            raise Exception("alpha_to_int() only expecta 'a' to 'z'")
        n = n*26 + (ord(char)-97)
    return n


class DiscussionTree:
    def __init__(self, db):
        self.db = db
        self.discussion_tree_db   = db.DiscussionTree
        self.pseudo_reputation_db = db.PseudoReputation

    @tornado.gen.engine
    def setup_indexes(self, callback):
        try:
            a,b = yield [motor.Op(self.discussion_tree_db.ensure_index  , "subtree_id"),
                         motor.Op(self.pseudo_reputation_db.ensure_index, "pseudo")]
        except Exception as e:
            callback(None, AsyncException("Failed setting up indexes", e))
            return
        print("Indexes ensured: ", a, b)
        callback(True, None)

    @tornado.gen.engine
    def increment_reputation(self, pseudo, subtree_id, inc, callback):
        '''increment_reputation: Increment(by inc) the reputation of pseudo for subtree(subtree_id).
                                If no existing reputation, then create one with repute=inc. 
                                
                pseudo     - The Pseudonym of the user getting adjusted reputation.
                subtree_id - Identifier of the subtree where the repute applies.
                inc        - Amount to increment reputation by (may be negative).
                callback   - Standard motor callback

                returns: Success: The new reputation record.
                         Failure: AsyncException
        '''        
        try:
            pseudo_reputation = yield motor.Op(self.pseudo_reputation_db.find_and_modify, 
                                              {"pseudo" : pseudo},
                                              {"$inc"   : {subtree_id+".count": inc}}, 
                                              upsert=True, new=True)
        except Exception as e:
            callback(None, AsyncException("Couldn't increment reputation for pseudo(%s), subtree(%s)" % (pseudo, subtree_id), e))
            return
        callback(pseudo_reputation, None)
            
    @tornado.gen.engine
    def get_reputation(self, pseudo, subtree_id, callback):
        '''get_reputation: Accumulate the reputation of user(pseudo) for subtree(subtree_id)
                          and all superior subtree's. 
                          If no existing reputation, then zero. 
                                {
                                    'pseudo': 'Andrewd',
                                    'repute': {
                                        'a': {
                                            'count': 4,
                                            'b': {
                                                  'count': 2,
                                            }
                                        }
                                    }
                                }
                          get_reputation("Andrewd", "a") should return 6.
                          get_reputation("Andrewd", "a.b") should return 2.
                          
                pseudo     - The Pseudonym of the user to get reputation for.
                subtree_id - Identifier of the subtree where the repute is wanted for.
                callback   - Standard motor callback

                returns: Success: The aggregate reputation value.
                         Failure: AsyncException
        '''        
        try:   # to get the pseudo's current reputation and determine how it applies to a subtree.
            pseudo_reputation = yield motor.Op(self.pseudo_reputation_db.find_one, {"pseudo":pseudo},
                                            fields={subtree_id:True, "_id":False} )
        except Exception as e:
            callback(None, AsyncException("Couldn't find PseudoReputation(%s)" % (pseudo, ), e))
            return
        if pseudo_reputation == None:
            callback(0, None)      # No reputation
            return

        def addTreeCount(tree): # Add up all counts in tree of reputations retrieved.
            reputation = 0
            for name,value in tree.items():
                if name == 'count':
                    reputation += value
                else:
                    reputation += addTreeCount(value)
            return reputation
        reputation = addTreeCount(pseudo_reputation)
        callback(reputation, None)
            

    @tornado.gen.engine
    def get_subtree(self, subtree_id, callback):
        '''get_subtree: Returns the full subtree document for subtree_id.
                          
                subtree_id - Identifier of the subtree to return.
                callback   - Standard motor callback

                returns: Success: Subtree document.
                         Failure: AsyncException
        '''        
        try:
            subtree = yield motor.Op(self.discussion_tree_db.find_one, {"subtree_id": subtree_id})
        except Exception as e:
            callback(None, AsyncException("Couldn't find DiscussionTree subtree(%s)" % subtree_id, e))
            return
        callback(subtree, None)


    @tornado.gen.engine
    def add_root_comment(self, text, callback):
        '''add_root_comment: Only way to add comments at the 0.x level. This method will not be generally exposed 
                           to the web interface. Users can not add comments at this level. It's a bootstrapping thing.
                                
                text        - Text of the comment to be added.
                callback    - Standard motor callback

                returns: Success: Comment dictionary.
                         Failure: AsyncException
        '''
        # Try for atomic upsert(1st comment) or update(Nth comment)
        comment = {"child_id": uuid.uuid4(), "pseudo":'root', "text":text, "repute":0, "time":datetime.utcnow() }
        try:
            subtree = yield motor.Op(self.discussion_tree_db.find_and_modify, 
                                     query={"subtree_id": 'a'},
                                     update={"$push": {"comments": comment}},
                                     upsert=True, new=True)
        except Exception as e:
            callback(None, AsyncException("find_and_modify failed adding comment(%s) to root of DiscussionTree" 
                                          % (str(comment),), e))
            return
        callback(('a.'+int_to_alpha(len(subtree["comments"])-1), comment), None)  # Success
        

    @tornado.gen.engine
    def add_comment_to_subtree(self, parent_uuid, parent_subtree_id, text, pseudo, callback):
        '''add_comment_to_subtree: If subtree matching subtree_id does not exist, then it will be added.
                                The comment text will be added to the the subtree.
                                Reputation of the user adding the comment will be applied.
                                
                parent_uuid        - uuid of the parent comment. Must match in the same record with the parent_subtree_id.
                parent_subtree_id  - Dotted notation identifier of the subtree where the comment will be added.
                text               - Text of the comment to be added.
                pseudo             - The Pseudonym of the user adding the comment.
                callback           - Standard motor callback

                returns: Success: Comment dictionary with repute and time-stamp.
                         Failure: AsyncException
        '''
        try:   # to get the pseudo's current reputation and assess whether the subtree already exists, in parallel.
            reputation = yield motor.Op(self.get_reputation, pseudo, parent_subtree_id)
        except Exception as e:
            callback(None, AsyncException("Couldn't find PseudoReputation(%s,%s)" % (pseudo, parent_subtree_id), e))
            return
        
        # Try for atomic upsert(1st comment) or update(Nth comment)
        # If we were updating to add Nth comment, then the parent_uuid had to match.
        comment = {"child_id": uuid.uuid4(), "pseudo":pseudo, "text":text, "repute":reputation, "time":datetime.utcnow() }
        try:
            subtree = yield motor.Op(self.discussion_tree_db.find_and_modify, 
                                     query={"_id": parent_uuid, "subtree_id": parent_subtree_id},
                                     update={"$push": {"comments": comment}}, 
                                     upsert=True, new=True)
        except Exception as e:
            callback(None, AsyncException("Possible hack attempt: find_and_modify failed on DiscussionTree(%s, %s)/$push(%s)" 
                                          % (str(parent_uuid), parent_subtree_id, str(comment)), e))
            return

        # If we just upserted the 1st ever comment on this subtree, then validate that it was not bogus.
        if len(subtree["comments"]) == 1:
            error = None
            try:
                last_dot       = parent_subtree_id.rindex(".")
                parent_tree_id = parent_subtree_id[0:last_dot]
                parent_comment_idx = alpha_to_int(parent_subtree_id[last_dot+1:])
            except ValueError: # No '.' - Could be stupid parent_subtree_id or root of tree
                error = AsyncException("Possible hack attempt: Badly formed parent_subtree_id on DiscussionTree(%s), _id(%s) comment insert(%s)" 
                                       % (parent_subtree_id, str(parent_uuid), text), None)
            else:
                try:
                    parent_subtree = yield motor.Op(self.discussion_tree_db.find_one, {"subtree_id": parent_tree_id})
                except Exception as e:
                    error = AsyncException("find_one on DiscussionTree._id(%s,%s) failed" % (str(parent_uuid), parent_tree_id), e)
                else:
                    if parent_subtree is None:
                        error = AsyncException("Possible hack attempt: Nonexistent parent_subtree(%s) on DiscussionTree comment insert(%s)" 
                                               % (parent_subtree_id, text), None)
                    elif parent_subtree["comments"][parent_comment_idx]["child_id"] != parent_uuid:
                        error = AsyncException("Possible hack attempt: Unmatched parent_uuid(%s) on DiscussionTree(%s) comment insert(%s)" 
                                               % (str(parent_uuid), parent_subtree_id, text), None)

            if error is not None:
                # If there is any problem with the ancestry of the first comment added, then we have to remove it. 
                # This is a bit unpleasant, adding it first and then removing when there's an error, but we're coding 
                # for the majority case rather than the exception..
                yield motor.Op(self.discussion_tree_db.remove, {"_id": subtree["_id"]})
                callback(None, error)
                return
        callback( (parent_subtree_id+"."+int_to_alpha(len(subtree["comments"])-1), comment), None)   # Success

if __name__ == "__main__":
    class TestDiscussionTree:
        def __init__(self, db):
            # Initially clear DiscussionTree and pollyReputation
            self.db = db
            self.pdt = DiscussionTree(db)
            
        @tornado.gen.engine
        def destroy_discussion_tree(self, callback):
            try:
                yield [
                    motor.Op(self.db.PseudoReputation.drop),
                    motor.Op(self.db.DiscussionTree.drop)
                ]
            except Exception as e:
                callback(None, AsyncException("Failed destroying DiscussionTree.", e))
                return
            callback(True, None)
    
        @tornado.gen.engine
        def create_discussion_tree(self, callback):
            comments = {}

            # Add root comments to bootstrap tree.
            root_comments = ["a.a   - Root Comment", 
                             "a.b   - Root Comment", 
                             "a.c   - Root Comment"]
            try:
                for root_comment in root_comments:
                    (subtree_id, comment) = yield motor.Op(self.pdt.add_root_comment,
                                                           root_comment)
                    comments[subtree_id] = comment
            except Exception as e:
                callback(None, AsyncException("Failed adding test root comments(%s)" % (root_comment,), e))
                return
            
            # Add deeper comments as would be added by users.
            try:
                pseudo = "AndrewD"
                for depth in range(4):
                    new_comments = {}
                    for parent_subtree_id, comment in comments.items():
                        for n in range(4):
                            parent_uuid = comments[parent_subtree_id]["child_id"]
                            (subtree_id, comment) = yield motor.Op(self.pdt.add_comment_to_subtree,
                                                                   parent_uuid, parent_subtree_id, parent_subtree_id+" - Comment %s"%int_to_alpha(n), pseudo)
                            new_comments[subtree_id] = comment
                    comments = new_comments
            except Exception as e:
                callback(None, AsyncException("Failed adding test user comments(%s)" % (parent_subtree_id,), e))
                return

            callback(True, None)
    
        @tornado.gen.engine
        def setup_reputations(self, callback):
            reputations = [
               ("AndrewD", "a.a"    , 42),
               ("AndrewD", "a.b"    , 42),
               ("AndrewD", "a.b"    ,  1),
               ("AndrewD", "a.b.b.b",  1),
            ]
            for (pseudo, subtree_id, inc) in reputations:
                try:
                    full_comment = yield motor.Op(self.pdt.increment_reputation, pseudo, subtree_id, inc)
                except Exception as e:
                    callback(None, AsyncException("Failed incrementing reputation of pseudo(%s) for subtree(%s) by %d" % (pseudo, subtree_id, inc), e))
                    return
            callback(True, None)
            
        @tornado.gen.engine
        def dump_tree(self, subtree_id, indent, callback):
            try:
                subtree = yield motor.Op(self.pdt.get_subtree, subtree_id)
            except Exception as e:
                callback(None, AsyncException("Failed getting subtree %s" % (subtree_id, ), e))
                return
            if subtree is None:     # leaf node
                callback(False, None)
                return
    
            for n, comment in enumerate(subtree["comments"]):
                new_subtree_id = subtree_id + "." + int_to_alpha(n)
                print(" "*indent, new_subtree_id+": time="+str(comment['time']), "pseudo="+comment['pseudo'], "repute="+str(comment['repute']), "comment="+comment['text']) 
                try:
                    yield motor.Op(self.dump_tree, new_subtree_id, indent+4)
                except Exception as e:
                    callback(None, AsyncException("Failed recursing into subtree(%s)" % (new_subtree_id, ), e))
                    return
            callback(True, None)
        
    @tornado.gen.engine
    def doTest(db):
        test_discussion_tree = TestDiscussionTree(db)
        try:
            yield motor.Op(test_discussion_tree.destroy_discussion_tree) # This blats the whole DB to start from scratch every time.
            yield motor.Op(test_discussion_tree.pdt.setup_indexes)       # Ensure indexes on DiscussionTree related collections 
            yield motor.Op(test_discussion_tree.setup_reputations)       # Setup some reputations to apply to the discussion tree.
            yield motor.Op(test_discussion_tree.create_discussion_tree)  # Create a discussion tree.
            yield motor.Op(test_discussion_tree.dump_tree, "a", 0)       # Do a recursive dump of the discussion tree, with reputations.
        except AsyncException as e:
            e.stack_trace()
            sys.exit(1)
        print("Done.")
        sys.exit(0)

    import logging
    logging.getLogger().setLevel(logging.DEBUG)
    db = motor.MotorClient('localhost', 27017).open_sync().test_database
    doTest(db)

    print("Enter IOLOOP")
    tornado.ioloop.IOLoop.instance().start()

