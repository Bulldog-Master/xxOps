#!/usr/bin/env python3
"""
test_amconfig.py — the Alertmanager config generator.

WHY THIS ONE MATTERS. This module decides who gets told when something breaks.
A mistake here is silent: the config is still valid YAML, amtool still accepts
it, the app still says saved - and an alert simply never reaches anyone. There
is no error to notice. The only way to find out is an incident nobody hears
about.

That is a different risk from most code, and it is why several tests below
check for ABSENCE rather than presence.

WHAT IS DELIBERATELY NOT TESTED. validate() and apply_config() stayed in the
server because they run amtool and write to disk. This module reads nothing,
writes nothing and knows no paths, which is exactly what makes it testable.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import xxops_amconfig as am


def notify(**over):
    """A settings dict with both channels available unless overridden."""
    n = {"smtp": {"host": "mail.example.net", "port": 587,
                  "from": "xxops@example.net", "username": "u",
                  "password": "p"},
         "telegram": {"bot_token": "123:abc"},
         "contacts": [],
         "fallback": {}}
    n.update(over)
    return n


class Quoting(unittest.TestCase):
    """yq is the only thing standing between a password and broken YAML."""

    def test_plain_scalar_is_quoted(self):
        self.assertEqual(am.yq("hello"), "'hello'")

    def test_an_apostrophe_is_doubled_not_dropped(self):
        """
        A password containing an apostrophe would otherwise close the quote
        early and produce a config that is either invalid or, worse, valid and
        wrong. YAML escapes it by doubling.
        """
        self.assertEqual(am.yq("it's"), "'it''s'")

    def test_a_password_with_a_quote_survives_the_whole_config(self):
        n = notify()
        n["smtp"]["password"] = "pa''ss'word"
        out = am.build_config(n, {})
        line = [l for l in out.splitlines() if "smtp_auth_password" in l][0]
        # every apostrophe inside the value must appear doubled
        value = line.split(": ", 1)[1]
        self.assertTrue(value.startswith("'") and value.endswith("'"))
        self.assertEqual(value[1:-1].count("'") % 2, 0,
                         "an odd number of quotes means one is unescaped")


class ChatIds(unittest.TestCase):

    def test_a_plain_number_is_accepted(self):
        self.assertEqual(am.chat_id("12345"), "12345")

    def test_a_negative_number_is_accepted_because_groups_use_them(self):
        self.assertEqual(am.chat_id("-1001234"), "-1001234")

    def test_surrounding_space_is_tolerated(self):
        self.assertEqual(am.chat_id("  99  "), "99")

    def test_anything_not_a_number_is_refused(self):
        for bad in ("@channelname", "abc", "12.5", "1 2", "", None, "12a"):
            self.assertIsNone(am.chat_id(bad), "accepted %r" % (bad,))


class Slugs(unittest.TestCase):

    def test_punctuation_becomes_underscores(self):
        self.assertEqual(am.slug("Bob Smith!"), "bob_smith")

    def test_it_never_returns_empty(self):
        """
        An empty receiver name would produce a route pointing at nothing, so
        there is a fallback. Worth checking, because the inputs are a contact
        id or a name and either can be missing.
        """
        for s in ("", "!!!", "   ", "---"):
            self.assertTrue(am.slug(s), "empty slug from %r" % (s,))
            self.assertEqual(am.slug(s), "contact")


class Channels(unittest.TestCase):

    def test_a_contact_with_nothing_gets_no_body(self):
        self.assertEqual(am.channels_for({}, notify()), [])

    def test_email_needs_a_mail_server(self):
        """An address with no smtp host cannot deliver, so it is not emitted."""
        n = notify(smtp={})
        self.assertEqual(am.channels_for({"emails": "a@example.com"}, n), [])

    def test_telegram_needs_a_bot_token(self):
        n = notify(telegram={})
        self.assertEqual(am.channels_for({"telegram_chat_id": "123"}, n), [])

    def test_semicolons_separate_addresses_too(self):
        body = am.channels_for({"emails": "a@example.com; c@example.net"}, notify())
        to = [l for l in body if "- to:" in l][0]
        self.assertIn("a@example.com", to)
        self.assertIn("c@example.net", to)

    def test_a_webhook_alone_counts_as_a_channel(self):
        body = am.channels_for({"webhook": "https://example.net/hook"}, notify())
        self.assertTrue(body)
        self.assertTrue(any("webhook_configs" in l for l in body))


class Routing(unittest.TestCase):
    """
    The routing shape, which was proven with amtool before it was written:
      1. amber goes to quiet FIRST and does not continue - amber never pages
      2. one route per contact, continue: true, so a validator with two
         owners reaches both
      3. anything unassigned falls through to fallback
    """

    def contact(self, cid, validators, chat="111"):
        return {"id": cid, "name": cid, "validators": validators,
                "telegram_chat_id": chat}

    def test_amber_is_routed_first_and_does_not_continue(self):
        n = notify(contacts=[self.contact("c1", ["alpha"])])
        lines = am.build_config(n, {}).splitlines()
        idx = [i for i, l in enumerate(lines) if 'severity="amber"' in l]
        self.assertEqual(len(idx), 1, "amber must be routed exactly once")
        first_contact = [i for i, l in enumerate(lines) if "instance=~" in l]
        self.assertLess(idx[0], first_contact[0],
                        "amber must be matched before any contact route, or a "
                        "warning would page someone")
        # the two lines after the amber matcher must not include continue
        after = lines[idx[0]:idx[0] + 3]
        self.assertNotIn("      continue: true", after,
                         "amber continuing would let warnings reach contacts")

    def test_each_contact_route_continues(self):
        n = notify(contacts=[self.contact("c1", ["alpha"]),
                             self.contact("c2", ["alpha"])])
        out = am.build_config(n, {})
        self.assertEqual(out.count("      continue: true"), 2)

    def test_a_validator_with_two_owners_reaches_both(self):
        """The reason continue: true exists. Worth asserting end to end."""
        n = notify(contacts=[self.contact("c1", ["alpha"]),
                             self.contact("c2", ["alpha"])])
        out = am.build_config(n, {})
        self.assertIn("receiver: contact_c1", out)
        self.assertIn("receiver: contact_c2", out)

    def test_the_paired_gateway_is_matched_too(self):
        n = notify(contacts=[self.contact("c1", ["alpha"])])
        out = am.build_config(n, {"alpha": "alpha_gw"})
        matcher = [l for l in out.splitlines() if "instance=~" in l][0]
        self.assertIn("alpha_gw", matcher)
        self.assertIn("alpha", matcher)

    def test_someone_elses_gateway_is_not_matched(self):
        n = notify(contacts=[self.contact("c1", ["alpha"])])
        out = am.build_config(n, {"alpha": "alpha_gw", "bravo": "bravo_gw"})
        matcher = [l for l in out.splitlines() if "instance=~" in l][0]
        self.assertNotIn("bravo", matcher)

    def test_a_contact_with_no_channel_gets_no_route(self):
        """
        Otherwise the config routes to a receiver with an empty body, which is
        valid YAML and delivers nothing - the silent failure this file exists
        to prevent.
        """
        n = notify(contacts=[{"id": "c1", "validators": ["alpha"]}])
        out = am.build_config(n, {})
        self.assertNotIn("contact_c1", out)

    def test_a_contact_with_no_validators_gets_no_route(self):
        n = notify(contacts=[self.contact("c1", [])])
        out = am.build_config(n, {})
        self.assertNotIn("contact_c1", out)

    def test_host_names_are_escaped_in_the_matcher(self):
        """
        instance=~ is a regex. A dot in a host name would otherwise match any
        character, so one contact's route could match another's machine.
        """
        n = notify(contacts=[self.contact("c1", ["node.one"])])
        matcher = [l for l in am.build_config(n, {}).splitlines()
                   if "instance=~" in l][0]
        self.assertIn(r"node\.one", matcher)

    def test_fallback_and_quiet_always_exist(self):
        """Every route points at one of these; a missing receiver is fatal."""
        out = am.build_config(notify(), {})
        self.assertIn("  - name: quiet", out)
        self.assertIn("  - name: fallback", out)


class Problems(unittest.TestCase):

    def test_a_working_setup_has_no_complaints(self):
        n = notify(contacts=[{"id": "c1", "telegram_chat_id": "123",
                              "validators": ["alpha"]}])
        self.assertEqual(am.problems(n), [])

    def test_a_non_numeric_chat_id_is_caught(self):
        n = notify(contacts=[{"id": "c1", "name": "Bob",
                              "telegram_chat_id": "@bobschannel"}])
        self.assertTrue(any("must be a number" in p for p in am.problems(n)))

    def test_a_malformed_address_is_caught(self):
        n = notify(contacts=[{"id": "c1", "name": "Bob", "emails": "bob@"}])
        self.assertTrue(any("email address" in p for p in am.problems(n)))

    def test_an_address_with_no_mail_server_is_caught(self):
        n = notify(smtp={}, contacts=[{"id": "c1", "name": "Bob",
                                       "emails": "bob@example.net"}])
        self.assertTrue(any("no mail server" in p for p in am.problems(n)))

    def test_a_mail_server_with_no_from_address_is_caught(self):
        n = notify(smtp={"host": "mail.example.net"},
                   contacts=[{"id": "c1", "telegram_chat_id": "1"}])
        self.assertTrue(any("'from' address" in p for p in am.problems(n)))

    def test_a_config_nobody_could_receive_is_caught(self):
        """
        THE MOST IMPORTANT ONE. A config where nothing can receive is valid
        YAML and amtool accepts it, so this check is the only thing standing
        between the operator and silence.
        """
        n = notify(contacts=[{"id": "c1", "validators": ["alpha"]}])
        self.assertTrue(any("Nothing would receive" in p for p in am.problems(n)),
                        "a config that reaches nobody was accepted")

    def test_a_fallback_alone_is_enough_to_receive(self):
        n = notify(fallback={"telegram_chat_id": "999"})
        self.assertFalse(any("Nothing would receive" in p for p in am.problems(n)))


class AmtoolAccepts(unittest.TestCase):
    """
    The strongest check available: hand the generated config to the real
    Alertmanager tooling. Skipped where amtool is not installed, so the suite
    still runs on a machine that is not the monitor.
    """

    def setUp(self):
        if not shutil.which("amtool"):
            self.skipTest("amtool not installed here")

    def check(self, text):
        d = tempfile.mkdtemp(prefix="xxops-am-")
        try:
            p = os.path.join(d, "alertmanager.yml")
            with open(p, "w") as f:
                f.write(text)
            r = subprocess.run(["amtool", "check-config", p],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             "amtool rejected the generated config:\n%s\n%s"
                             % (r.stdout, r.stderr))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_full_config_is_accepted(self):
        n = notify(contacts=[{"id": "c1", "name": "Bob",
                              "emails": "bob@example.net",
                              "telegram_chat_id": "-1001", "validators": ["alpha"]}],
                   fallback={"telegram_chat_id": "999"})
        self.check(am.build_config(n, {"alpha": "alpha_gw"}))

    def test_an_empty_config_is_still_accepted(self):
        """A fresh install has no contacts and must still start."""
        self.check(am.build_config(notify(), {}))

    def test_awkward_characters_do_not_break_it(self):
        n = notify(contacts=[{"id": "o'brien", "name": "O'Brien",
                              "telegram_chat_id": "1", "validators": ["node.one"]}])
        n["smtp"]["password"] = "it's a 'secret'"
        self.check(am.build_config(n, {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
