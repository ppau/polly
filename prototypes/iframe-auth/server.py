import tornado.ioloop
import tornado.web

from tornado.web import HTTPError, RequestHandler, Application
from tornadio2 import SocketConnection, TornadioRouter, SocketServer, event


class SocketIOConnection(SocketConnection):
    def on_open(self, request):
        token = None
        try:
            token = request.cookies.get('id').value
        except:
            pass
        
        session = Router._sessions.get(token)
        if session is not None:
            self.emit("authed", {
                "success": True
            })


class IndexHandler(RequestHandler):
    def get(self):
        self.render("index.html")


class LoginPageHandler(RequestHandler):
    def get(self):
        self.render("login.html")
    
    def post(self):
        token = self.get_argument('id', default=None)
        session = Router._sessions.get(token)
        session.conn.emit("authed", {
            "success": True
        })
        self.set_cookie('id', token, httponly=True)
        self.write("Done!\n")


class SocketIOHandler(RequestHandler):
    def get(self):
        self.render("socket.io.js")


Router = TornadioRouter(SocketIOConnection)

application = Application(
    Router.apply_routes([
        (r"/socket.io.js", SocketIOHandler),
        (r"/login", LoginPageHandler),
        (r"/", IndexHandler)
    ]),
    socket_io_port = 8888
)

if __name__ == "__main__":
    import logging
    logging.getLogger().setLevel(logging.DEBUG)

    SocketServer(application)
