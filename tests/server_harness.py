#!/usr/bin/env python3
"""
server_harness.py — load the real server and make real requests to it,
without a socket, a port, or touching live state.

WHY NOT JUST IMPORT THE FUNCTIONS. The bug this suite exists to prevent was
not a wrong function. It was a correct authentication gate placed BELOW the
routes it was supposed to guard - four POST routes ran with no authentication
at all, and every function involved was individually fine.

A test that imports and calls functions cannot see that. The only thing that
can is executing the real dispatch chain, in order, the way an HTTP request
does. So this drives the actual handler class through do_GET and do_POST with
a fake socket, and reads back whatever it wrote.

STATE. XXOPS_STATE_DIR and XXOPS_APP_DIR are both environment-overridable in
the server, so everything here runs against a temporary directory. Nothing
touches /var/lib/xxops.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server", "xxops-server.py")


class Reply:
    """What the server sent back."""

    def __init__(self, code, headers, body):
        self.code = code
        self.headers = headers
        self.body = body

    @property
    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None

    @property
    def message(self):
        d = self.json
        return (d or {}).get("message", "")

    def __repr__(self):
        return "<%s %s>" % (self.code, (self.message or self.body[:40]))


def load_server(state_dir):
    """Import xxops-server.py as a module, pointed at a scratch directory."""
    os.environ["XXOPS_STATE_DIR"] = state_dir
    os.environ["XXOPS_APP_DIR"] = os.path.join(REPO, "app")

    # Several state paths do NOT follow STATE_DIR - AUTH_FILE, SESSIONS_FILE
    # and RESOLUTIONS each have their own variable and hardcode
    # /var/lib/xxops. Without these, the harness reads and could write real
    # accounts and real recorded fixes. The check after import is what
    # actually enforces it; these are how it passes.
    os.environ["XXOPS_AUTH_FILE"] = os.path.join(state_dir, "auth.json")
    os.environ["XXOPS_SESSIONS_FILE"] = os.path.join(state_dir, "sessions.json")
    os.environ["XXOPS_RESOLUTIONS"] = os.path.join(state_dir, "resolutions.json")
    os.makedirs(state_dir, exist_ok=True)

    if not os.path.isfile(SERVER):
        raise SystemExit("cannot find %s - is this being run from the repo?"
                         % SERVER)

    spec = importlib.util.spec_from_file_location("xxops_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xxops_server"] = mod
    spec.loader.exec_module(mod)

    # Prove it: if any state path escaped the scratch directory, stop now
    # rather than run a test suite against live data.
    for name in ("AUTH_FILE", "SESSIONS_FILE", "SETTINGS", "NOTIFY",
                 "RESOLUTIONS"):
        path = getattr(mod, name, None)
        if path and not str(path).startswith(state_dir):
            raise SystemExit(
                "%s points at %s, outside the scratch directory. Refusing to "
                "run against live state." % (name, path))
    return mod


class Fake:
    """
    A handler instance with the socket parts replaced.

    Built without calling __init__, because BaseHTTPRequestHandler's
    constructor immediately tries to read a request off a real connection.
    Everything the handler actually touches is supplied here instead.
    """

    def __init__(self, mod, method, path, body=None, cookie=None):
        self.mod = mod
        H = mod.H
        h = H.__new__(H)
        h.path = path
        h.command = method
        h.client_address = ("127.0.0.1", 0)
        h.request_version = "HTTP/1.1"
        h.server = None

        raw = json.dumps(body or {}).encode() if body is not None else b""
        h.rfile = io.BytesIO(raw)
        h.wfile = io.BytesIO()

        import email.message
        msg = email.message.Message()
        if raw:
            msg["Content-Length"] = str(len(raw))
            msg["Content-Type"] = "application/json"
        if cookie:
            msg["Cookie"] = "xxops_session=%s" % cookie
        h.headers = msg

        # capture the response instead of writing it to a socket
        h._code = None
        h._hdrs = []
        h._out = bytearray()
        h.send_response = lambda c, m=None: setattr(h, "_code", c)
        h.send_header = lambda k, v: h._hdrs.append((k, v))
        h.send_response_only = lambda c, m=None: setattr(h, "_code", c)
        h.end_headers = lambda: None
        h.log_message = lambda *a, **k: None
        h.log_request = lambda *a, **k: None

        real_wfile_write = h.wfile.write

        def capture(b):
            h._out.extend(b)
            return real_wfile_write(b)

        h.wfile.write = capture
        self.h = h

    def go(self):
        h = self.h
        if h.command == "GET":
            h.do_GET()
        else:
            h.do_POST()
        body = bytes(h._out)
        try:
            body = body.decode()
        except UnicodeDecodeError:
            body = "<binary>"
        return Reply(h._code, dict(h._hdrs), body)


def request(mod, method, path, body=None, cookie=None):
    return Fake(mod, method, path, body, cookie).go()


def make_owner(mod, username="tester", password="a-good-long-password"):
    """
    Create the first account and return its session cookie.

    Both gates only engage once authentication is configured, so a test for
    "is this route gated" is meaningless until an account exists. This is
    therefore a precondition, not a convenience.
    """
    r = request(mod, "POST", "/api/auth/setup",
                {"username": username, "password": password})
    if r.code != 200:
        raise SystemExit("could not create the test account: %s" % r)
    for k, v in r.headers.items():
        if k.lower() == "set-cookie" and "xxops_session=" in v:
            return v.split("xxops_session=")[1].split(";")[0]
    raise SystemExit("setup succeeded but returned no session cookie")


def scratch():
    return tempfile.mkdtemp(prefix="xxops-test-")
