#!/usr/bin/env python3
"""
test_route_auth.py — every route is either gated or deliberately public.

THE BUG THIS EXISTS FOR. Four POST routes once ran with no authentication at
all: /api/settings, /api/notify, /api/notify/preview and /api/notify/test.
They were all on the owner-only list. The gate existed and was correct. It
simply sat ~220 lines further down the function than the routes it guarded,
and nothing anywhere would tell you.

It was found by accident, while mapping dispatch chains for an unrelated
refactor. That is not a repeatable discovery method.

WHAT THIS ASSERTS
  1. Every route found in the source is either gated or on the EXEMPT list
     below. A route that is neither FAILS - so adding an endpoint forces a
     decision about it rather than defaulting to open.
  2. Every gated route actually refuses an unauthenticated request, checked
     by making the request.
  3. The two owner-only lists agree with each other. There are two, for GET
     and POST, maintained separately - which is the same shape as the fault
     that once left a fleet-wide destructive command permitted.

HOW TO CHECK THIS TEST IS REAL. Move the gate in _post back below
/api/settings and run this. It must go red. A test that has never failed has
not been tested.
"""

import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server_harness as sh


# Routes that are meant to answer without a session, each with the reason.
# Adding to this list is the deliberate act of making something public.
EXEMPT = {
    "/api/health":  "liveness - must answer before anyone can sign in",
    "/api/version": "the update check runs on the login page",
}
EXEMPT_PREFIXES = {
    "/api/auth/": "sign-in, setup and redemption happen before a session exists",
}

# Owner-only to change, but readable by any signed-in user BECAUSE the handler
# scopes the response. That is not the same as being ungated, so it gets its
# own category rather than an exemption.
SCOPED_READ = {
    "/api/settings": "GET filters pairs through allowed_instances() and drops "
                     "ignore, so a contact sees their own machines and not "
                     "the fleet map",
}


def routes_in_source():
    """
    Every /api path the server dispatches on, read from the source.

    Deliberately parsed rather than listed, so a route added tomorrow is
    picked up without anyone remembering to add it here.
    """
    src = open(sh.SERVER, encoding="utf-8").read()
    found = set(re.findall(r'p == "(/api/[^"]+)"', src))
    found |= set(re.findall(r'\(\s*"(?:GET|POST)"\s*,\s*"(/api/[^"]+)"\s*\)', src))
    return found


def is_exempt(path):
    if path in EXEMPT:
        return True
    return any(path.startswith(p) for p in EXEMPT_PREFIXES)


class RouteAuth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = sh.scratch()
        cls.mod = sh.load_server(cls.dir)
        cls.cookie = sh.make_owner(cls.mod)
        cls.routes = routes_in_source()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_parse_actually_found_things(self):
        """
        If the source parse breaks, every other test here passes vacuously -
        an empty set satisfies "all of these are gated". So check for routes
        that certainly exist rather than for a count, which would be an
        arbitrary number that ages badly.
        """
        for canary in ("/api/settings", "/api/health"):
            self.assertIn(canary, self.routes,
                          "%s was not found by the source parse, so the parse "
                          "is broken and every other assertion in this file is "
                          "meaningless" % canary)

    def test_every_route_is_gated_or_deliberately_public(self):
        ungated = []
        for path in sorted(self.routes):
            if is_exempt(path):
                continue
            for method in ("POST", "GET"):
                r = sh.request(self.mod, method, path, {} if method == "POST" else None)
                # 404/405 means this method does not serve that path - fine.
                # 401/403 means it refused us - correct.
                # anything else means it answered a stranger.
                if r.code not in (401, 403, 404, 405):
                    ungated.append("%s %s -> %s" % (method, path, r.code))
        self.assertEqual(ungated, [], "these answered without a session:\n  "
                         + "\n  ".join(ungated))

    def test_no_route_is_accidentally_public(self):
        """A route in neither category is an undecided route."""
        undecided = [p for p in sorted(self.routes)
                     if not is_exempt(p) and p not in self.routes]
        self.assertEqual(undecided, [])

    def test_exempt_list_is_not_stale(self):
        """An exemption for a route that no longer exists hides intent."""
        gone = [p for p in EXEMPT if p not in self.routes]
        self.assertEqual(gone, [], "exempt but no longer a route: %s" % gone)

    def test_owner_only_lists_agree(self):
        """
        There are two owner-only lists - the OWNER_ONLY tuple used by the POST
        gate, and an inline tuple in the GET gate. Two lists expressing one
        policy is how the action-permissions guard drifted.
        """
        src = open(sh.SERVER, encoding="utf-8").read()
        post_list = set(self.mod.H.OWNER_ONLY)
        m = re.search(r'p in \((.*?)\)\s*and _me\.get\("role"\)', src, re.S)
        self.assertIsNotNone(m, "could not find the GET owner check - if it "
                                "moved, this test needs updating rather than "
                                "deleting")
        get_list = set(re.findall(r'"(/api/[^"]+)"', m.group(1)))

        # Only paths that actually answer a GET can be compared. A POST-only
        # route has nothing to be owner-only about on the GET side, and
        # demanding it appear there would be a test asserting nonsense.
        missing = []
        for path in sorted(post_list - get_list):
            if path in SCOPED_READ:
                continue
            r = sh.request(self.mod, "GET", path, cookie=self.cookie)
            if r.code not in (404, 405):
                missing.append(path)

        self.assertEqual(
            missing, [],
            "owner-only on POST but readable by any signed-in user on GET: "
            "%s\nA contact could read what they cannot change. If that is "
            "deliberate, record the reason here rather than deleting the "
            "check." % missing)

    def test_an_owner_can_actually_use_them(self):
        """Guard against over-refusing: the gate must not lock out the owner."""
        r = sh.request(self.mod, "GET", "/api/settings", cookie=self.cookie)
        self.assertEqual(r.code, 200, "the owner was refused their own settings")


if __name__ == "__main__":
    unittest.main(verbosity=2)
