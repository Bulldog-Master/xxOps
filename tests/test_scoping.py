#!/usr/bin/env python3
"""
test_scoping.py — what a non-owner is allowed to see.

WHY THIS MATTERS MORE THAN IT LOOKS. The route authentication test records
/api/settings as a SCOPED_READ: it is owner-only to change, but any signed-in
user may GET it, because the handler filters the response through
allowed_instances(). That exemption is only safe if the filtering is correct,
and until now nothing checked it. So this file is the foundation the other
test's exemption rests on.

THE PROPERTY THAT MATTERS MOST is that it FAILS CLOSED. Every path where
something is missing, unreadable or unrecognised must produce an empty set -
see nothing - rather than None, which means no restriction at all. A single
wrong return in an error branch turns a contact into an owner.

allowed_instances() derives its answer from the contact record the account is
bound to, rather than a second list, so it cannot drift from alert routing.
These tests are written against that intent.
"""

import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server_harness as sh


class Scoping(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = sh.scratch()
        cls.mod = sh.load_server(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def write_state(self, contacts=None, pairs=None):
        """Put a notify file and a settings file where the server reads them."""
        with open(self.mod.NOTIFY, "w") as f:
            json.dump({"contacts": contacts or []}, f)
        with open(self.mod.SETTINGS, "w") as f:
            json.dump({"pairs": pairs or {}}, f)

    def contact(self, cid="c1", validators=None):
        return {"id": cid, "name": "someone", "validators": validators or []}

    # -- the owner -------------------------------------------------------

    def test_owner_is_unrestricted(self):
        """None means no filtering at all - only the owner may get it."""
        self.write_state()
        self.assertIsNone(self.mod.allowed_instances({"role": "owner"}))

    # -- failing closed --------------------------------------------------

    def test_missing_caller_sees_nothing_once_auth_is_on(self):
        """
        A missing caller must fail CLOSED. None would mean unrestricted, so
        returning it here would hand the whole fleet to a request that arrived
        with nobody attached.

        This used to return None. It was safe only because the GET gate
        refuses unauthenticated requests before this is reached - the safety
        lived in the caller's position rather than in this function, which is
        the same shape as the fault that once left four routes unauthenticated.
        """
        self.write_state()
        with open(self.mod.AUTH_FILE, "w") as f:
            json.dump({"users": {"someone": {"role": "owner"}}, "version": 1}, f)
        got = self.mod.allowed_instances(None)
        self.assertIsNotNone(got, "None means UNRESTRICTED")
        self.assertEqual(got, set())

    def test_missing_caller_before_setup_is_unrestricted(self):
        """
        The other half, and the reason this is not simply `return set()`.

        On a fresh install no account exists, authentication is off, and
        _whoami() returns None for everyone. Filtering to nothing there would
        show an empty app to someone who has not been given a way to sign in
        yet. There is also nothing to protect: no accounts means no contacts
        means no one to keep anything from.
        """
        self.write_state()
        try:
            os.unlink(self.mod.AUTH_FILE)
        except FileNotFoundError:
            pass
        self.assertIsNone(self.mod.allowed_instances(None))

    def test_contact_bound_to_nothing_sees_nothing(self):
        self.write_state()
        got = self.mod.allowed_instances({"role": "contact"})
        self.assertEqual(got, set())
        self.assertIsNotNone(got)

    def test_contact_id_that_matches_no_record_sees_nothing(self):
        self.write_state(contacts=[self.contact("c1", ["alpha"])])
        got = self.mod.allowed_instances({"role": "contact", "contactId": "c-gone"})
        self.assertEqual(got, set())

    def test_unreadable_state_sees_nothing(self):
        """Corrupt files must not widen access."""
        with open(self.mod.NOTIFY, "w") as f:
            f.write("{ this is not json")
        with open(self.mod.SETTINGS, "w") as f:
            f.write("{ nor is this")
        got = self.mod.allowed_instances({"role": "contact", "contactId": "c1"})
        self.assertIsNotNone(got, "unreadable state must not mean unrestricted")
        self.assertEqual(got, set())

    def test_contact_with_no_validators_sees_nothing(self):
        self.write_state(contacts=[self.contact("c1", [])])
        self.assertEqual(
            self.mod.allowed_instances({"role": "contact", "contactId": "c1"}),
            set())

    # -- what a contact does see -----------------------------------------

    def test_contact_sees_their_own_validators(self):
        self.write_state(contacts=[self.contact("c1", ["alpha", "bravo"])])
        got = self.mod.allowed_instances({"role": "contact", "contactId": "c1"})
        self.assertEqual(got, {"alpha", "bravo"})

    def test_paired_gateways_come_along(self):
        """A contact needs the gateway their node is paired to, and no others."""
        self.write_state(
            contacts=[self.contact("c1", ["alpha"])],
            pairs={"alpha": "alpha_gw", "charlie": "charlie_gw"})
        got = self.mod.allowed_instances({"role": "contact", "contactId": "c1"})
        self.assertEqual(got, {"alpha", "alpha_gw"})
        self.assertNotIn("charlie_gw", got,
                         "someone else's gateway leaked into the allowed set")

    def test_a_node_with_no_gateway_is_still_allowed(self):
        self.write_state(contacts=[self.contact("c1", ["alpha"])], pairs={})
        self.assertEqual(
            self.mod.allowed_instances({"role": "contact", "contactId": "c1"}),
            {"alpha"})

    def test_one_contact_does_not_see_another(self):
        self.write_state(
            contacts=[self.contact("c1", ["alpha"]),
                      self.contact("c2", ["bravo"])],
            pairs={"alpha": "alpha_gw", "bravo": "bravo_gw"})
        got = self.mod.allowed_instances({"role": "contact", "contactId": "c1"})
        self.assertEqual(got, {"alpha", "alpha_gw"})

    def test_empty_names_are_dropped(self):
        """A blank entry must not become a host that matches something."""
        self.write_state(contacts=[self.contact("c1", ["alpha", "", None])])
        got = self.mod.allowed_instances({"role": "contact", "contactId": "c1"})
        self.assertEqual(got, {"alpha"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
