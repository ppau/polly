import tornado
from pymongo import DESCENDING, ASCENDING
from bson.code import Code
import motor
from datetime import datetime
import sys

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
#   1. Elements (1,2,3) are the primary sub-column.
#   2. Elements (1.1, 1.2, 1.3) are a sub-column.
#   3. Elements (2.1, 2.2, 2.3) are a separate sub-column from (1.1, 1.2, 1.3) 
#        
#   Sample Tree Structure
#   0
#        0.0
#        0.1
#   1    
#        1.0
#        1.1
#        1.2 
#   2
#        2.0
#        2.1
#            2.2.0
#        2.2
#            2.3.0
#            2.3.1
#   3
#        3.0
#            3.1.0
#            3.1.1
#        3.1
#            3.2.0
#            3.2.1
#            3.2.2
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
#    8. May pre-fetch first chunks of sub-columns below currently opened sub-column in client as optimisation.
#
#
#    MongoDB PollyReputation document format: 
#    {
#        "pseudo"    : <StringPseudonymName>,   # } Combined unique key of
#        "subtree_id": <StringPseudonymName>,   # } both pseudo and subtree.
#
#        "repute"    : <reputationPoints>       # Reputation points applicable to just this subtree.
#    }
#
#    MongoDB PollyDiscussionTree document format: 
#    {
#        "subtree_id": <DottedDecimalSubreeId>, # Dotted tree hierarchy sub-tree reference as string. e.g. "3.3.1"
#        "comments":    [                       # Array position indicates next sub-tree level. e.g. 0th entry is 3.3.1.0
#            {
#                "time":      <Comment Time>    # Time the comment was written in UTC
#                "pseudo":    <UserPseudonym>   # Pseudonym of the user that wrote the comment.
#                "repute":    <reputation>      # The reputation of <UserPseudonym> at the time of the comment
#                                               # Incidentally, a more expensive option would be to allow sorting
#                                               # the comments by current reputation. Probably cached in memory.
#                "text":      <StringComment>   # The actual comment. 
#                                               # Allowing some simple (TBD) markup. Probably Markdown.
#            },
#            ...
#        ],
#    }
#
#
class PollyException(Exception):
    def __init__(self, message, e=None):
        Exception.__init__(self, "PollyException(" + sys._getframe(1).f_code.co_name + "): "+message)
        self.e = e
    
    def dumpStr(self, depth=0):
        if self.e:
            if isinstance(self.e, PollyException):
                return " "*(depth*8) + self.message + "\n" + self.e.dumpStr(depth+1)
            else:
                return " "*(depth*8) + self.message + "\n" + " "*(depth*8+8) + str(self.e)
        else:
            return " "*(depth*8) + self.message   

class PollyDiscussionTree:
    def __init__(self, db):
        self.db = db
        self.pollyDiscussionTree = db.PollyDiscussionTree
        self.pollyReputation     = db.PollyReputation

    @tornado.gen.engine
    def setupIndexes(self, callback):
        try:
            a,b = yield [motor.Op(self.pollyDiscussionTree.ensure_index, "subtree_id"),
                         motor.Op(self.pollyReputation.ensure_index, [("pseudo"    , ASCENDING),
                                                                      ("subtree_id", ASCENDING)]) ]
        except Exception, e:
            callback(None, PollyException("Failed setting up indexes", e))
            return
        print "Indexes ensured: ", a, b
        callback(True, None)

    @tornado.gen.engine
    def incrementReputation(self, pseudo, subtree_id, inc, callback):
        '''incrementReputation: Increment(by inc) the reputation of pseudo for subtree(subtree_id).
                                If no existing reputation, then create one with repute=inc. 
                                
                pseudo     - The Pseudonym of the user getting adjusted reputation.
                subtree_id - Identifier of the subtree where the repute applies.
                inc        - Amount to increment reputation by (may be negative).
                callback   - Standard motor callback

                returns: Success: The new reputation record.
                         Failure: PollyException
        '''        
        try:
            polly_reputation = yield motor.Op(self.pollyReputation.find_and_modify, 
                                              {"pseudo" : pseudo, "subtree": subtree_id},
                                              {"$inc"   : {"repute": inc}}, 
                                              upsert=True, new=True)
        except Exception, e:
            callback(None, PollyException("Couldn't increment reputation for pseudo(%s), subtree(%s)" % (pseudo, subtree_id), e))
            return
        callback(polly_reputation, None)
            
    @tornado.gen.engine
    def getReputation(self, pseudo, subtree_id, callback):
        '''getReputation: Accumulate the reputation of user(pseudo) for subtree(subtree_id)
                          and all superior subtree's. 
                          If no existing reputation, then zero. 
                          e.g. {"pseudo":"X", "subtree":"1.1"  , "repute":4} 
                               {"pseudo":"X", "subtree":"1.1.2", "repute":4}
                          getReputation("X", "1.1.2.4") should return 8.
                          
                pseudo     - The Pseudonym of the user to get reputation for.
                subtree_id - Identifier of the subtree where the repute is wanted for.
                callback   - Standard motor callback

                returns: Success: The aggregate reputation value.
                         Failure: PollyException
        '''        
        key       = None   # Group over everything
        condition = {"pseudo": pseudo,
                     "$where": "this.subtree == \""+subtree_id+"\".substring(0, this.subtree.length)"""}
        initial   = {"repute": 0}
        reduce    = Code("""function (doc, out) { out.repute += doc.repute;}""")
        try:   # to get the pseudo's current reputation and determine how it applies to a subtree.
            out = yield motor.Op(self.pollyReputation.group, key, condition, initial, reduce)
        except Exception, e:
            callback(None, PollyException("Couldn't find pollyReputation(%s)" % (pseudo, ), e))
            return
        if out == []:
            callback(0, None)                   # Zero matches gives empty list.
        else:
            callback(int(out[0]["repute"]), None)  # JS sums as float.
            

    @tornado.gen.engine
    def getsubtree(self, subtree_id, callback):
        '''getsubtree: Returns the full subtree document for subtree_id.
                          
                subtree_id - Identifier of the subtree to return.
                callback   - Standard motor callback

                returns: Success: Subtree document.
                         Failure: PollyException
        '''        
        try:
            subtree = yield motor.Op(self.pollyDiscussionTree.find_one, {"subtree_id": subtree_id})
        except Exception, e:
            callback(None, PollyException("Couldn't find PollyDiscussionTree subtree(%s)" % subtree_id, e))
            return
        callback(subtree, None)

    @tornado.gen.engine
    def addCommentTosubtree(self, subtree_id, pseudo, text, callback):
        '''addCommentTosubtree: If subtree matching subtree_id does not exist, then it will be added.
                                The comment text will be added to the the subtree.
                                Reputation of the user adding the comment will be applied.
                                
                subtree_id - Identifier of the subtree where the comment will be added.
                pseudo     - The Pseudonym of the user adding the comment.
                text       - Text of the comment to be added.
                callback   - Standard motor callback

                returns: Success: Comment dictionary with repute and time-stamp.
                         Failure: PollyException
        '''
        try:   # to get the pseudo's current reputation and assess whether the subtree already exists, in parallel.
            subtree, reputation = yield [
                motor.Op(self.pollyDiscussionTree.find_one, {"subtree_id": subtree_id}),
                motor.Op(self.getReputation, pseudo, subtree_id) 
            ]
        except Exception, e:
            callback(None, PollyException("Couldn't find PollyDiscussionTree(%s) or Reputation(%s,%s)" % (subtree_id, pseudo, subtree_id), e))
            return
        
        comment = {"pseudo":pseudo, "text":text, "repute":reputation, "time":datetime.utcnow() }
        if subtree is None: # New subtree
            try:
                pdt = {"subtree_id": subtree_id, "comments": [comment]}
                result = yield motor.Op(self.pollyDiscussionTree.insert, pdt, safe=True)
            except Exception, e:
                callback(None, PollyException("Insert failed on PollyDiscussionTree(%s)" % (pdt,), e))
                return
        else:
            try:
                result = yield motor.Op(self.pollyDiscussionTree.find_and_modify, 
                                        query={"subtree_id": subtree_id},
                                        update={"$push": {"comments": comment}}, upsert=False)
            except Exception, e:
                callback(None, PollyException("find_and_modify failed on PollyDiscussionTree(%s)/$push(%s)" % (subtree_id, comment), e))
                return

        # Success!         
        callback(comment, None)

if __name__ == "__main__":
    class TestPollyDiscussionTree:
        def __init__(self, db):
            # Initially clear PollyDiscussionTree and pollyReputation
            self.db = db
            self.pdt = PollyDiscussionTree(db)
    
            
        @tornado.gen.engine
        def destroyPollyDiscussionTree(self, callback):
            try:
                yield [
                    motor.Op(self.db.PollyReputation.drop),
                    motor.Op(self.db.PollyDiscussionTree.drop)
                ]
            except Exception, e:
                callback(None, PollyException("Failed destroying PollyDiscussionTree.", e))
                return
            callback(True, None)
            
    
        @tornado.gen.engine
        def createPollyDiscussionTree(self, callback):
            comments = [
                ("0"   , "0.0 - Root Comment"),
                ("0.0"  , "0.0.0 - Comment0."),
                ("0.0"  , "0.0.1 - Comment1."),
                ("0.0"  , "0.0.2 - Comment2."),
                ("0.0.0", "0.0.0.0 - Comment0."),
                ("0.0.0", "0.0.0.1 - Comment1."),
                ("0.0.0", "0.0.0.2 - Comment2."),
                ("0.0.1", "0.0.1.0 - Comment0."),
                ("0.0.1", "0.0.1.1 - Comment1."),
                ("0.0.1", "0.0.1.2 - Comment2."),
                ("0"   , "0.1 - Root Comment"),
                ("0.1"  , "0.1.0 - Comment0."),
                ("0.1"  , "0.1.1 - Comment1."),
                ("0.1"  , "0.1.2 - Comment2."),
                ("0.1.0", "0.1.0.0 - Comment0."),
                ("0.1.0", "0.1.0.1 - Comment1."),
                ("0.1.0", "0.1.0.2 - Comment2."),
                ("0.1.1", "0.1.1.0 - Comment0."),
                ("0.1.1", "0.1.1.1 - Comment1."),
                ("0.1.1", "0.1.1.2 - Comment2."),
             ]
            pseudo="AndrewD"
            for (subtree_id, comment) in comments:
                try:
                    full_comment = yield motor.Op(self.pdt.addCommentTosubtree, subtree_id, pseudo=pseudo, text=comment)
                except Exception, e:
                    callback(None, PollyException("Failed adding test comments %s/%s/%s" % (subtree_id, pseudo, comment), e))
                    return
            callback(True, None)
    
        @tornado.gen.engine
        def setupReputations(self, callback):
            reputations = [
               ("AndrewD", "0.0"    , 42),
               ("AndrewD", "0.1"    , 42),
               ("AndrewD", "0.1"    ,  1),
               ("AndrewD", "0.1.1.1",  1),
            ]
            for (pseudo, subtree_id, inc) in reputations:
                try:
                    full_comment = yield motor.Op(self.pdt.incrementReputation, pseudo, subtree_id, inc)
                except Exception, e:
                    callback(None, PollyException("Failed incrementing reputation of pseudo(%s) for subtree(%s) by %d" % (pseudo, subtree_id, inc), e))
                    return
            callback(True, None)
            
        @tornado.gen.engine
        def dumpTree(self, subtree_id, indent, callback):
            try:
                subtree = yield motor.Op(self.pdt.getsubtree, subtree_id)
            except Exception, e:
                callback(None, PollyException("Failed getting subtree %s" % (subtree_id, ), e))
                return
            if subtree is None:     # leaf node
                callback(False, None)
                return
    
            for n, comment in enumerate(subtree["comments"]):
                new_subtree_id = subtree_id + "." + str(n)
                print " "*indent, new_subtree_id+": time="+str(comment['time']), "pseudo="+comment['pseudo'], "repute="+str(comment['repute']), "comment="+comment['text'] 
                try:
                    yield motor.Op(self.dumpTree, new_subtree_id, indent+4)
                except Exception, e:
                    callback(None, PollyException("Failed recursing into subtree(%s)" % (new_subtree_id, ), e))
                    return
            callback(True, None)
        
    @tornado.gen.engine
    def doTest(db):
        testPolly = TestPollyDiscussionTree(db)
        try:
            yield motor.Op(testPolly.destroyPollyDiscussionTree) # This blats the whole DB to start from scratch every time.
            yield motor.Op(testPolly.pdt.setupIndexes)
            yield motor.Op(testPolly.setupReputations)           # Setup some reputations to apply to the discussion tree.
            yield motor.Op(testPolly.createPollyDiscussionTree)  # Create a discussion tree.
            yield motor.Op(testPolly.dumpTree, "0", 0)            # Do a recursive dump of the discussion tree, with reputations.
        except PollyException, e:
            print(e.dumpStr())
            sys.exit(1)
        print "Done."
        sys.exit(0)

    import logging
    logging.getLogger().setLevel(logging.DEBUG)
    db = motor.MotorConnection('localhost', 27017).open_sync().test_database
    doTest(db)

    print("Enter IOLOOP")
    tornado.ioloop.IOLoop.instance().start()






































