from datetime import datetime
import sys
import traceback

class PollyException(Exception):
    '''
        PollyException is used in Tornado Asynchronous Subroutines.
        The context for this takes some explaining, so read on ...
        
        We are using a common asynchronous processing design, as recommended by Jesse Jiru (pyMongo/Motor author).
        Each asynchronous method has the @tornado.gen.engine decorator and has a 'callback' function parameter.
        The method may be invoked using:  
        
                  result = yield motor.Op(method, params...)
        
        and may in turn invoke other asynchronous methods the same way.
        
        Methods always return their results using the callback(result, exception), with either result or exception being 
        not None.
        Exceptions inside asynchronous methods invoked in this way may be caught by try: except: wrapped around the yield.
                  try:
                      result = yield motor.Op(method, params...)
                  except Exception, e:
                      callback(None, e)
                      return        # Must always return immediately after callback.

        Given this, we can write our asynchronous methods, almost as if they were synchronous, forming common subroutines
        and a functional call-tree style composition of code, albeit with slightly weird syntax.
        However, when an exception occurs, suddenly the whole illusion is blown. Call stacks always show just the current
        asynchronous callback from the tornado.ioloop and you get no indication of the more logically synchronous call tree
        that you are actually trying to debug.  

        PollyException tries to fix this situation by maintaining a stack of exceptions back to the original request that
        triggered the tree of asynchronous calls.
        
                  try:
                      result = yield motor.Op(method, params...)
                  except Exception, e:
                      callback(None, PollyException("Description of what we were trying to do", e))
                      return        # Must always return immediately after callback.
        
        PollyException captures the current stack frame information and constructs a stack of exception information from the
        specific failure point, all the way back up to the requesting layer.
        At that point, PollyException's view of the stack can be dumped using its dumpStr() method to produce output in exactly
        the same format as a regular stack track. IDE's like Eclipse will interpret this in a way that lets you click on links
        from this stack dump, to go directly to view the offending code.  
            
            def requestingMethod()
                try:
                      result = yield motor.Op(method, params...)
                except PollyException, e:
                    print(e.dumpStr())
    '''
    def __init__(self, message, e=None):
        if isinstance(e, PollyException):
            frame = sys._getframe(1)
            self.exception_description =  "PollyException: " + message
            self.exception_location    = 'File "'+frame.f_code.co_filename + '", line ' + str(frame.f_lineno) + ', method ' + frame.f_code.co_name
        else:
            etype, value, tb = sys.exc_info()
            self.exception_description =  etype.__name__+': '+str(value)
            self.exception_location    = 'File "'+tb.tb_frame.f_code.co_filename + '", line ' + str(tb.tb_lineno) + ', method ' + tb.tb_frame.f_code.co_name
        Exception.__init__(self, self.exception_description)
        self.e = e
    
    def pollyStackDump(self, file=sys.stderr):
        print >> file, "\nPolly Stack Trace:\n"
        print >> file, self.dumpStr(4)
            
    def dumpStr(self, indent):
        msg = " "*indent + self.exception_description + "\n"\
            + " "*indent + self.exception_location    + "\n\n"
        if self.e and isinstance(self.e, PollyException):
            msg += self.e.dumpStr(indent+4)
        return msg




