import tornado.ioloop
import tornado.web
import uuid

from tornado.web import HTTPError, RequestHandler, Application
from tornadio2 import SocketConnection, TornadioRouter, SocketServer, event

DEBUG = True


class SessionStore:
    def __init__(self, router):
        self._router = router
        self._sessions = {}

    def put(self, token, session):
        assert token is not None
        assert session is not None

        if not self._sessions.get(token):
            self._sessions[token] = set()
        self._sessions[token].add(session)
    
    def remove(self, token):
        if self._sessions.get(token):
            del self._sessions[token]

    def get_session(self, id):
        return self._router._sessions.get(id)

    def get_active(self, token):
        assert token is not None

        self.ensure_active(token)
        return self._sessions.get(token, set())

    def ensure_active(self, token):
        assert token is not None
        
        sessions = self._sessions.get(token)
        if sessions is None:
            return

        for s in sessions.copy():
            if self._router._sessions.get(s) is None:
                if DEBUG:
                    print("Removing inactive session: %s" % s)
                sessions.remove(s)
        
        if len(sessions) == 0:
            if DEBUG:
                print("No active sessions for token %s, deleting." % token)
            del self._sessions[token]
    
    def has_activity(self, token):
        assert token is not None

        return len(self.get_active(token)) > 0

    def clean_inactive(self):
        for token in self._sessions.key():
            if not has_activity(token):
                del self._sessions[token]


class SocketIOConnection(SocketConnection):
    def on_open(self, request):
        token = None
        try:
            token = request.cookies.get('id').value
        except:
            pass
        id = self.session.session_id

        # check for other active sessions
        if token is not None and session_store.has_activity(token):
            session_store.put(token, id)
            self.emit("authed", {
                "success": True
            })


class JQHandler(RequestHandler):
    def get(self):
        self.render("jquery-1.8.2.min.js")


class IndexHandler(RequestHandler):
    def get(self):
        self.render("index.html")


class LoginPageHandler(RequestHandler):
    def post(self):
        id = self.get_argument('id', default=None)
        username = self.get_argument('username', default=None)
        password = self.get_argument('password', default=None)

        if id is None:
            return

        session = session_store.get_session(id)
        if session is None:
            return
        
        if self.get_cookie('id') is not None:
            session_store.remove(self.get_cookie('id'))
        
        token = uuid.uuid4().hex
        session_store.put(token, id)
        self.set_cookie('id', token, httponly=True)
        
        session.conn.emit("authed", {
            "success": True
        })


class SocketIOHandler(RequestHandler):
    def get(self):
        self.render("socket.io.js")


Router = TornadioRouter(SocketIOConnection)
session_store = SessionStore(Router)

application = Application(
    Router.apply_routes([
        (r"/socket.io.js", SocketIOHandler),
        (r"/jquery-1.8.2.min.js", JQHandler),
        (r"/login", LoginPageHandler),
        (r"/", IndexHandler)
    ]),
    socket_io_port = 8888
)

if __name__ == "__main__":
    import logging
    logging.getLogger().setLevel(logging.DEBUG)

    SocketServer(application)
