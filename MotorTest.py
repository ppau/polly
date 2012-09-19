'''
Created on 25/01/2012

@author: User
'''
import tornado
from tornado import gen
from tornado.testing import AsyncTestCase
from pymongo import DESCENDING, ASCENDING
from bson.code import Code

import motor
from datetime import datetime
import sys, time

class TestAsync:
    def __init__(self, db):
        self.posts = db.posts
        self.posts.drop()
        self.posts.ensure_index([("date", DESCENDING), ("author", ASCENDING)])
    
    def insertPost1(self):
        post1 = {"author": "Mike",
                "text"  : "My first blog post!",
                "tags"  : ["mongodb", "python", "pymongo"],
                "date"  : datetime.utcnow(),
                "count" : 1}
        self.posts.insert(post1, safe=True, callback=self.insertPost2)
    
    def insertPost2(self, post1, error):
        if error:
            print "post1 insert error", error
            sys.exit(1)
        post2and3 = [{"author": "Mike",
                      "text": "Another post!",
                      "tags": ["bulk", "insert"],
                      "date": datetime(2009, 11, 12, 11, 14),
                      "count" : 1},
                     {"author": "Eliot",
                      "title": "MongoDB is fun",
                      "text": "and pretty easy too!",
                      "date": datetime(2009, 11, 10, 10, 45),
                      "count" : 1}]
        self.posts.insert(post2and3, safe=True, callback=self.findPost)

    def findPost(self, post2and3, error):
        if error:
            print "post2and3 insert error", error
            sys.exit(1)
        self.posts.find_one({"author": "Mike"}, callback=self.foundMike)
            
    def foundMike(self, mike, error):
        if error:
            print "failed to find Mike:", error
            sys.exit(1)
        print "Mike Found:", mike
        self.cursor = self.posts.find()
        self.cursor.next(callback=self.foundOne)

    def foundOne(self, post, error):
        if error:
            print "error on cursor next:", error
            sys.exit(1)
        if post == None:
            print "No more posts"
            self.doYieldThing()
        else:
            print "Post Found:", post
            self.cursor.next(callback=self.foundOne)
            
    @gen.engine
    def doYieldThing(self):
        fail = yield motor.Op(self.posts.find_one, {'text': "zzzzzz"})
        print "fail = ", fail
        
        cursor = self.posts.find()
        while True:
            post = yield motor.Op(cursor.next)
            if post:
                print "Async Gen yielded:", post
            else:
                print "Async Gen Ended."
                break
        self.doFindSortedList()
    
    @gen.engine
    def doFindSortedList(self):
        listResult = yield motor.Op(self.posts.find().sort('author').to_list)
        for post in listResult:
            print "ToList returned:", post
        print "ToList END"
        self.doMapReduce()

    @gen.engine
    def doMapReduce(self):
        myMap = Code("""function () {
                          this.tags.forEach (
                              function(z) {
                                  emit(z, 1);
                              }
                          );
                      }"""  )
        myReduce = Code("""function (key, values) {
                             var total = 0;
                             for (var i = 0; i < values.length; i++) {
                                 total += values[i];
                             }
                             return total;
                         }""" )
        result = yield motor.Op(self.posts.map_reduce, myMap, myReduce, "myresults", query={"tags" : {"$exists": True}} )
        mr_cursor = result.find()
        while True:
            mr_result = yield motor.Op(mr_cursor.next)
            if mr_result:
                print "map_reduce() yielded:", mr_result
            else:
                print "map_reduce() Ended."
                break
        self.doGroup()
    
    @gen.engine
    def doGroup(self):
        key       = {}
        condition = {}    # Fail case: {"author": "Aaron"} 
        initial   = {"sum":0}
        reduce    = Code("""function (doc, out) { out.sum += doc.count;}""")
        result = yield motor.Op(self.posts.group, key, condition, initial, reduce)
        print "doGroup:", result
        self.doIncTest()
    
    @gen.engine
    def doIncTest(self):
        result = yield motor.Op(self.posts.find_and_modify, 
                                query={"author": "Aaron", "date": datetime(2009, 11, 12, 11, 14)},
                                update={"$inc": {"count":1}},
                                upsert=True, new=True)
        print result

class TestListKeys:
    def __init__(self, db):
        self.listkeys = db.listkeys
        self.listkeys.ensure_index("list", unique=True)
    
    @gen.engine
    def insertData(self):
        listkeys = [
                    {"user": "AndrewD",
                     "list": [
                        {
                            "pseudo": "Andrew1",
                            "repute": 1,
                        },
                        {
                            "pseudo": "Andrew2",
                            "repute": 2,
                        },
                        {
                            "pseudo": "Andrew3",
                            "repute": 3,
                        },
                        ]
                    },
                    
                    {"user": "BrendanM",
                     "list": [
                        {
                            "pseudo": "brendan1",
                            "repute": 1,
                        },
                        {
                            "pseudo": "brendan2",
                            "repute": 2,
                        },
                        {
                            "pseudo": "brendan3",
                            "repute": 3,
                        },
                        ]
                    }
                ]
        result = yield motor.Op(self.listkeys.insert, listkeys, safe=True)
        print result

    @gen.engine
    def findReputation(self, pseudo):
        print("before", pseudo)
        result = yield motor.Op(self.listkeys.find_one, {'list.pseudo': pseudo}, fields={"list": True, "_id": False} )
        print("after", pseudo)
        if result == None:
            print "Reputation of %s is Unknown" % pseudo
        else:
            repute = [reputeDict["repute"] for reputeDict in result["list"] if reputeDict["pseudo"] == pseudo][0]
            print "Reputation of %s is %d" % (pseudo, repute)
        
        
db = motor.MotorConnection('localhost', 27017).open_sync().test_database
loop = tornado.ioloop.IOLoop.instance()
#loop.add_timeout(time.time() + 0.1, DoTesting)

test = TestAsync(db)
test.insertPost1()

#test = TestListKeys(db)
#test.findReputation("brendan1")

print("Enter IOLOOP")
loop.start()















