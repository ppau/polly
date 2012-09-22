import tornado.ioloop
import tornado.web

from tornado.web import HTTPError, RequestHandler, Application
from tornadio2 import SocketConnection, TornadioRouter, SocketServer, event


class SocketIOConnection(SocketConnection):
	pass

class IndexHandler(RequestHandler):
    def get(self):
        self.render("index.html")

class LoginPageHandler(RequestHandler):
	def get(self):
		self.render("login.html")
	
	def post(self):
		token = self.get_argument('id', default=None)
		session = Router._sessions.get(token)
		session.conn.emit("authed", {"success": True})

class SocketIOHandler(RequestHandler):
    def get(self):
        self.render("socket.io.js")


Router = TornadioRouter(SocketIOConnection)

application = Application(
    Router.apply_routes([
    	(r"/", IndexHandler),
		(r"/login", LoginPageHandler),
        (r"/socket.io.js", SocketIOHandler)
	]),
    socket_io_port = 8888
)

if __name__ == "__main__":
    import logging
    logging.getLogger().setLevel(logging.DEBUG)

    SocketServer(application)
