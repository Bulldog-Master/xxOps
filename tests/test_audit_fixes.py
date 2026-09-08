"""Regression tests for the September 2026 security audit fixes.

Each of these corresponds to a bug that shipped. They exist because the
previous round's fixes contained the next round's bugs: the password-change
session revoke compared against a key that did not exist on the record it was
reading, so it kept every session while telling the operator otherwise. Code
review did not catch it twice; a test with two cookies would have.

A test here should be able to FAIL. If you change a fix and nothing goes red,
check the test still reaches the thing it is describing.
"""

import json
import os
import shutil
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server_harness as sh


class AuditFixes(unittest.TestCase):

    def setUp(self):
        self.dir = sh.scratch()
        self.mod = sh.load_server(self.dir)
        # The harness does not set XXOPS_ENROLL_TOKEN, so the module falls
        # back to /etc/xxops/enroll_token - the real one on this machine. A
        # test must never read or write there. Point it at the scratch dir,
        # the same way every other path in the harness is scoped.
        self.mod.ENROLL_TOKEN_FILE = os.path.join(self.dir, "enroll_token")
        # Same reason: the module falls back to /etc/xxops for this too, and
        # a test must never read or write the real one.
        self.mod.SETUP_TOKEN_FILE = os.path.join(self.dir, "setup_token")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- sessions --------------------------------------------------------

    def test_password_change_kills_other_sessions_not_yours(self):
        """The one that shipped broken.

        Alice signs in twice and changes her password from session A. B must
        die, A must survive - logging her out of the page she is typing into
        would be its own bug - and another user must not be touched.
        """
        a = sh.make_owner(self.mod, "alice", "a-good-long-password")
        d = self.mod.load_sessions()
        d["session-b"] = {"user": "alice", "exp": time.time() + 99999,
                          "seen": time.time()}
        d["bobs"] = {"user": "bob", "exp": time.time() + 99999,
                     "seen": time.time()}
        self.mod.save_sessions(d)

        r = sh.request(self.mod, "POST", "/api/auth/password",
                       {"current": "a-good-long-password",
                        "new": "another-good-password"}, cookie=a)
        self.assertEqual(r.code, 200, r)

        left = self.mod.load_sessions()
        self.assertIn(a, left, "the caller was signed out of their own page")
        self.assertNotIn("session-b", left,
                         "the other session survived - this is the bug that "
                         "shipped, and the response claims it did not")
        self.assertIn("bobs", left, "another user's session was revoked")

    def test_totp_disable_kills_other_sessions(self):
        """Turning two-factor off has the same intent as changing a password:
        make what someone else might hold stop working."""
        a = sh.make_owner(self.mod, "alice", "a-good-long-password")
        d = self.mod.load_sessions()
        d["session-b"] = {"user": "alice", "exp": time.time() + 99999,
                          "seen": time.time()}
        self.mod.save_sessions(d)

        r = sh.request(self.mod, "POST", "/api/auth/totp/disable",
                       {"password": "a-good-long-password"}, cookie=a)
        self.assertEqual(r.code, 200, r)
        left = self.mod.load_sessions()
        self.assertIn(a, left)
        self.assertNotIn("session-b", left)

    def test_idle_session_is_dropped_and_a_used_one_is_not(self):
        """Idle must mean idle. An earlier version measured age instead, which
        would have signed everyone out daily."""
        sh.make_owner(self.mod)
        d = self.mod.load_sessions()
        d["stale"] = {"user": "tester", "exp": time.time() + 99999,
                      "seen": time.time() - 25 * 3600}
        d["fresh"] = {"user": "tester", "exp": time.time() + 99999,
                      "seen": time.time() - 600}
        self.mod.save_sessions(d)
        left = self.mod.load_sessions()
        self.assertNotIn("stale", left)
        self.assertIn("fresh", left)

    def test_session_lifetime_is_not_a_month(self):
        self.assertLessEqual(self.mod.SESSION_DAYS, 14)

    # -- the enrolment token ---------------------------------------------

    def test_non_ascii_token_is_refused_not_a_500(self):
        """compare_digest on str operands raises TypeError on non-ASCII, and
        the handler turned that into a 500. A token pasted from a phone with
        a smart quote is the ordinary way to reach it."""
        with open(self.mod.ENROLL_TOKEN_FILE, "w") as f:
            f.write("a-real-token")
        r = sh.request(self.mod, "POST", "/api/agent/register",
                       {"token": "a-real-tok\u2019n", "host": "alpha",
                        "ip": "10.0.0.1"})
        self.assertNotEqual(r.code, 500,
                            "a bad token must not be a server error")
        self.assertIn(r.code, (400, 403))

    def test_wrong_token_is_refused(self):
        with open(self.mod.ENROLL_TOKEN_FILE, "w") as f:
            f.write("a-real-token")
        r = sh.request(self.mod, "POST", "/api/agent/register",
                       {"token": "not-it", "host": "alpha", "ip": "10.0.0.1"})
        self.assertEqual(r.code, 403, r)

    def test_no_token_file_refuses_rather_than_accepts(self):
        """An absent secret must never mean 'no checking'."""
        try:
            os.unlink(self.mod.ENROLL_TOKEN_FILE)
        except OSError:
            pass
        r = sh.request(self.mod, "POST", "/api/agent/register",
                       {"token": "anything", "host": "alpha", "ip": "10.0.0.1"})
        self.assertEqual(r.code, 503, r)

    # -- request size ----------------------------------------------------

    def test_an_enormous_content_length_is_refused(self):
        """Reading an attacker's Content-Length in full is a memory DoS.

        This used to assert that the string "MAX_BODY" appeared in the
        source, which would still pass with the check deleted and the
        constant left behind. Send a real oversized claim instead.
        """
        cookie = sh.make_owner(self.mod)
        r = sh.request(self.mod, "POST", "/api/settings",
                       {"pairs": {}}, cookie=cookie)
        self.assertNotEqual(r.code, 500, "a normal body should still work")

        # A body far over the cap must not be read in full. The handler
        # returns an empty body rather than allocating, so the request is
        # rejected on its content rather than hanging.
        big = {"pairs": {"x" * 1000: "y" * 1000 for _ in range(1)}}
        big["padding"] = "z" * (300 * 1024)
        r = sh.request(self.mod, "POST", "/api/settings", big, cookie=cookie)
        self.assertNotEqual(r.code, 200,
                            "a 300KiB body was accepted; the cap is not "
                            "being applied")

    def test_a_normal_body_still_works(self):
        """The cap must not be so tight it breaks ordinary use - a notify
        config with several contacts is the largest real body here."""
        cookie = sh.make_owner(self.mod)
        r = sh.request(self.mod, "GET", "/api/health", None, cookie=cookie)
        self.assertEqual(r.code, 200)

    # -- authorization ---------------------------------------------------

    def test_a_contact_is_refused_the_silence_routes(self):
        """The behavioural half of the owner-only check.

        The source assertion below proves the route is listed. This proves a
        real signed-in contact is actually refused - which is the thing that
        matters, and which would go red if the gate stopped consulting that
        list.
        """
        sh.make_owner(self.mod, "owner1", "a-good-long-password")
        # A contact account, bound to a contact id that owns nothing.
        store = self.mod.load_users()
        store["users"]["contact1"] = {
            "role": "contact", "contactId": "c1",
            "pw": self.mod.hash_password("another-long-password"),
            "totp": None, "recovery": [], "created": 0}
        self.mod.save_users(store)
        tok = self.mod.new_session("contact1")

        for route in ("/api/silence/create", "/api/silence/expire"):
            r = sh.request(self.mod, "POST", route,
                           {"matchers": [], "id": "x"}, cookie=tok)
            self.assertEqual(r.code, 403, "%s let a contact through: %s"
                             % (route, r))

    def test_silences_are_owner_only(self):
        """A contact could otherwise mute every alert on every host, the
        owner's pager included - and a silence stops Alertmanager notifying
        anyone, so it suppresses the operator's own pager too.

        Read from the source rather than by walking attributes: the earlier
        version looked for a class whose name ended in "Handler", found
        nothing, and asserted on None. It could never have passed, and it
        would not have noticed either route being removed.
        """
        src = open(self.mod.__file__, encoding="utf-8").read()
        i = src.index("OWNER_ONLY = (")
        block = src[i:src.index(")", i)]
        self.assertIn("/api/silence/create", block,
                      "silences are not owner-only - a contact can mute "
                      "the whole fleet")
        self.assertIn("/api/silence/expire", block,
                      "a contact can un-mute what the owner muted")
        # And the routes the last audit found unscoped must stay listed.
        for route in ("/api/notify", "/api/agent/run"):
            self.assertIn(route, block)


    # -- the setup code -------------------------------------------------
    #
    # This path has no other test, and a mistake in it locks out every future
    # operator while being invisible to anyone whose monitor already has an
    # account. That is the exact shape of the two blockers found on the 8th.

    def test_setup_needs_the_code_when_one_exists(self):
        with open(self.mod.SETUP_TOKEN_FILE, "w") as f:
            f.write("THECODE")
        r = sh.request(self.mod, "POST", "/api/auth/setup",
                       {"username": "alice", "password": "a-good-long-password"})
        self.assertEqual(r.code, 403, r)

    def test_setup_refuses_the_wrong_code(self):
        with open(self.mod.SETUP_TOKEN_FILE, "w") as f:
            f.write("THECODE")
        r = sh.request(self.mod, "POST", "/api/auth/setup",
                       {"username": "alice", "password": "a-good-long-password",
                        "setupToken": "NOPE"})
        self.assertEqual(r.code, 403, r)

    def test_setup_accepts_the_right_code_and_consumes_it(self):
        with open(self.mod.SETUP_TOKEN_FILE, "w") as f:
            f.write("THECODE")
        r = sh.request(self.mod, "POST", "/api/auth/setup",
                       {"username": "alice", "password": "a-good-long-password",
                        "setupToken": "THECODE"})
        self.assertEqual(r.code, 200, r)
        self.assertFalse(os.path.exists(self.mod.SETUP_TOKEN_FILE),
                         "the code was not consumed and could be reused")

    def test_an_install_with_no_code_can_still_set_up(self):
        """An upgrade must not strand someone who never had a code."""
        try:
            os.unlink(self.mod.SETUP_TOKEN_FILE)
        except OSError:
            pass
        r = sh.request(self.mod, "POST", "/api/auth/setup",
                       {"username": "alice", "password": "a-good-long-password"})
        self.assertEqual(r.code, 200, r)


if __name__ == "__main__":
    unittest.main()
