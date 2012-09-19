import tornado.ioloop
import tornado.web

from tornado.web import HTTPError, RequestHandler, Application
from tornadio2 import SocketConnection, TornadioRouter, SocketServer, event


class BackboneConnection(SocketConnection):
    @event
    def sync(self, url, method, model, options):
        print(method, url, model, options)
        self.emit(model)

    def on_create(self, model, options):
        raise NotImplementedError

    def on_read(self, model, options):
        raise NotImplementedError

    def on_update(self, model, options):
        raise NotImplementedError

    def on_delete(self, model, options):
        raise NotImplementedError


class IndexHandler(RequestHandler):
    def get(self):
        self.render("static/test.html")


class SocketIOHandler(RequestHandler):
    def get(self):
        self.render("static/socket.io.js")


SyncRouter = TornadioRouter(BackboneConnection)


sio_application = Application(
    SyncRouter.apply_routes([
        (r"/socket.io.js", SocketIOHandler)
    ]),
    socket_io_port = 8001
)

web_application = Application([
    (r"/", IndexHandler),
    (r"/(.*)", tornado.web.StaticFileHandler, {"path": "static"})
])

if __name__ == "__main__":
    import logging
    logging.getLogger().setLevel(logging.DEBUG)

    SocketServer(sio_application, auto_start=False)
    web_application.listen(8888)
   
    tornado.ioloop.IOLoop.instance().start()
