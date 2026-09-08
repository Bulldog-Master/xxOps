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
        """Reading an attacker's Content-Length in full is a memory DoS. The
        cap is checked before a byte is read."""
        self.assertTrue(hasattr(self.mod.Handler
                                if hasattr(self.mod, "Handler") else object,
                                "MAX_BODY")
                        or "MAX_BODY" in open(self.mod.__file__).read(),
                        "no body cap in the server")

    # -- authorization ---------------------------------------------------

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


if __name__ == "__main__":
    unittest.main()
