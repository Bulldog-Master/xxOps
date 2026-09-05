#!/usr/bin/env python3
"""
test_catalogue.py — the action catalogue and who is allowed to run what.

THE BUG THIS DESCENDS FROM. "Which actions change things" once lived in three
places: the agent's catalogue, a hardcoded list in the CLI, and another in the
app. Two drifted, and the CLI's stale list left a fleet-wide destructive
command PERMITTED - `all stop-chain` ran without complaint.

That shape is now gone. The CLI derives the answer from the agent's own
descriptions, so it cannot hold a different opinion. What remains is a
different risk, and it is the one these tests are aimed at:

    an action that changes things, filed in the read-only dict.

Nothing would catch that. It would be advertised without the prefix, the CLI
would treat it as safe, --yes would not be required, and it would be permitted
fleet-wide. The classification is structural - CATALOGUE means read-only,
CHANGES means mutating - so the tests check the structure rather than a list.
"""

import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, relpath):
    path = os.path.join(REPO, relpath)
    if not os.path.isfile(path):
        raise unittest.SkipTest("%s not found" % relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# things that change a machine, whatever dict they are filed under
MUTATING_VERBS = ("start", "stop", "restart", "reload", "enable", "disable",
                  "reboot", "shutdown", "kill", "rm", "install", "upgrade")


class Catalogue(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.agent = load("xxops_agent", "agent/xxops-agent.py")
        cls.cli = load("xxops_cmd", "agent/xxops-cmd.py")

    def flat(self, cmd):
        """A command as one lowercase string, whatever shape it is in."""
        if isinstance(cmd, (list, tuple)):
            return " ".join(str(c) for c in cmd).lower()
        return str(cmd).lower()

    # -- the two dicts are a classification, so they must not overlap -----

    def test_no_action_is_in_both_dicts(self):
        both = set(self.agent.CATALOGUE) & set(self.agent.CHANGES)
        self.assertEqual(both, set(),
                         "an action cannot be both read-only and mutating: %s"
                         % sorted(both))

    def test_every_changing_action_is_advertised_as_changing(self):
        adv = self.agent.applicable()
        for name in self.agent.CHANGES:
            if name not in adv:
                continue          # not applicable to this host, fine
            self.assertTrue(adv[name].startswith("CHANGES THINGS"),
                            "%s changes things but is advertised as safe" % name)

    def test_no_read_only_action_claims_to_change_things(self):
        adv = self.agent.applicable()
        for name in self.agent.CATALOGUE:
            if name in adv:
                self.assertFalse(adv[name].startswith("CHANGES THINGS"),
                                 "%s is read-only but warns like an action" % name)

    def test_nothing_mutating_is_hiding_in_the_read_only_dict(self):
        """
        THE ONE THIS FILE EXISTS FOR.

        An action filed in CATALOGUE is advertised without the prefix, so the
        CLI treats it as safe: no --yes required, and permitted fleet-wide.
        A systemctl restart filed there would be exactly the earlier bug in a
        new form.
        """
        offenders = []
        for name, (_needs, how, _desc) in self.agent.CATALOGUE.items():
            if callable(how):
                continue          # a python reader, not a command line
            text = self.flat(how)
            for verb in MUTATING_VERBS:
                if (" %s " % verb) in (" %s " % text):
                    offenders.append("%s runs '%s'" % (name, verb))
        self.assertEqual(offenders, [],
                         "these are filed as read-only but change things:\n  "
                         + "\n  ".join(offenders))

    # -- the CLI must agree with the agent, not hold its own opinion -----

    def test_the_cli_agrees_with_the_agent_about_every_action(self):
        adv = self.agent.applicable()
        for name, desc in adv.items():
            expected = name in self.agent.CHANGES
            self.assertEqual(
                self.cli.is_changing(desc), expected,
                "the CLI and the agent disagree about %s - this is the exact "
                "shape of the drift that once left a destructive command "
                "permitted" % name)

    def test_the_cli_treats_an_unknown_description_as_safe_not_dangerous(self):
        """
        Worth pinning down. Treating unknown as dangerous would be safer in
        isolation, but it would also mean every read-only action started
        demanding confirmation the moment a description changed.
        """
        self.assertFalse(self.cli.is_changing("just some words"))
        self.assertFalse(self.cli.is_changing(""))
        self.assertFalse(self.cli.is_changing(None))

    def test_the_prefix_match_is_not_fooled_by_case_or_space(self):
        self.assertTrue(self.cli.is_changing("  changes things - whatever"))
        self.assertTrue(self.cli.is_changing("CHANGES THINGS - whatever"))

    # -- how the commands themselves are built ---------------------------

    def test_every_changing_command_is_a_fixed_argument_list(self):
        """
        sudoers permits exactly these argument lists. A string would be
        shell-interpreted, and the sudoers entry would no longer describe what
        can actually run.
        """
        for name, (_needs, cmd, _desc) in self.agent.CHANGES.items():
            self.assertIsInstance(cmd, (list, tuple),
                                  "%s is not a fixed argument list" % name)
            for part in cmd:
                self.assertIsInstance(part, str, "%s has a non-string arg" % name)

    def test_every_changing_command_uses_an_absolute_path(self):
        """
        These run through sudo. A relative program name would be resolved
        against PATH, and whoever controls PATH would control what runs.
        """
        for name, (_needs, cmd, _desc) in self.agent.CHANGES.items():
            self.assertTrue(cmd and cmd[0].startswith("/"),
                            "%s runs %r, which is not an absolute path"
                            % (name, cmd[0] if cmd else None))

    def test_no_changing_command_contains_shell_metacharacters(self):
        """Nothing here goes through a shell, so these would be literal."""
        for name, (_needs, cmd, _desc) in self.agent.CHANGES.items():
            joined = " ".join(cmd)
            for ch in (";", "|", "&", "$(", "`", ">", "<"):
                self.assertNotIn(ch, joined,
                                 "%s contains %r, which suggests it was "
                                 "written expecting a shell" % (name, ch))

    def test_every_action_has_a_description_worth_reading(self):
        """
        The description is what the operator sees before confirming, and for
        changing actions it is the only warning they get.
        """
        for src in (self.agent.CATALOGUE, self.agent.CHANGES):
            for name, entry in src.items():
                desc = entry[2]
                self.assertTrue(desc and len(desc) > 8,
                                "%s has no useful description" % name)
                self.assertFalse(desc.startswith("CHANGES THINGS"),
                                 "%s hardcodes the prefix - it is added by "
                                 "applicable() and would be doubled" % name)

    def test_the_destructive_ones_say_what_they_cost(self):
        """
        Stopping cMix or rebooting costs earnings. If the description does not
        say so, the confirmation prompt is not really informed consent.
        """
        costly = [n for n in self.agent.CHANGES
                  if n.startswith("stop-") or "reboot" in n]
        self.assertTrue(costly, "expected some costly actions to check")
        for name in costly:
            desc = self.agent.CHANGES[name][2].lower()
            self.assertTrue(
                any(w in desc for w in ("earn", "network", "consensus",
                                        "deaf", "until")),
                "%s is destructive but its description does not say what it "
                "costs: %r" % (name, self.agent.CHANGES[name][2]))

    # -- role gates ------------------------------------------------------

    def test_gateway_actions_are_gated_on_a_gateway_file(self):
        for name, (needs, _cmd, _desc) in self.agent.CHANGES.items():
            if "gateway" in name:
                self.assertIsNotNone(
                    needs, "%s would run on a node as well as a gateway" % name)

    def test_cmix_actions_are_gated_on_a_node_file(self):
        for name, (needs, _cmd, _desc) in self.agent.CHANGES.items():
            if "cmix" in name or name == "update-node-reboot":
                self.assertIsNotNone(
                    needs, "%s would run on a gateway as well as a node" % name)


    # -- the app's row actions --------------------------------------------

    def row_actions(self):
        """
        The (action, label, host expression, warning) tuples the validator row
        offers. Parsed from the source rather than imported, because they are
        built inline in a template and there is nothing to import.
        """
        import re
        views = os.path.join(REPO, "app", "xxops-views.js")
        if not os.path.isfile(views):
            self.skipTest("no xxops-views.js in this repo")
        with open(views, encoding="utf-8") as f:
            src = f.read()
        return re.findall(
            r'opts\.push\(\["([a-z-]+)",\s*"([^"]*)",\s*([^,]+),\s*"([^"]*)"\]\)',
            src)

    def test_every_row_action_exists_in_the_agents_catalogue(self):
        """
        THE ONE THIS EXISTS FOR. Rename an action in the agent and this list
        keeps offering the old name - which fails only when somebody picks it,
        during an incident, because that is when anyone uses it.
        """
        acts = self.row_actions()
        self.assertTrue(acts, "found no row actions - the source parse is "
                              "wrong, and this test proves nothing")
        missing = [a for a, _l, _h, _w in acts if a not in self.agent.CHANGES]
        self.assertEqual(
            missing, [],
            "the validator row offers actions the agent does not have: %s\n"
            "Either the agent renamed them or the row was never updated."
            % missing)

    def test_no_row_action_is_actually_read_only(self):
        """An entry under 'Actions' that changes nothing is a confusing lie."""
        acts = self.row_actions()
        wrong = [a for a, _l, _h, _w in acts if a in self.agent.CATALOGUE]
        self.assertEqual(wrong, [], "offered as actions but read-only: %s"
                         % wrong)

    def test_anything_that_stops_something_says_what_it_costs(self):
        """
        The warning text IS the confirmation prompt - it is the whole of what
        an operator reads before agreeing. An empty one on a destructive
        action means consent without information.
        """
        acts = self.row_actions()
        silent = [a for a, _l, _h, w in acts
                  if a.startswith("stop-") and not w.strip()]
        self.assertEqual(silent, [],
                         "these stop something and warn about nothing: %s"
                         % silent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
