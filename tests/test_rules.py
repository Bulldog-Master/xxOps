#!/usr/bin/env python3
"""
test_rules.py — run the alerting rules through promtool.

Two things happen here. The rules are checked for syntax, and then
tests/rules_test.yml is run against them: synthetic series in, expected alerts
out, no Prometheus instance and no waiting. A scenario that takes half an hour
to occur on real hardware takes a millisecond here.

WHY THIS REACHES SOMETHING THE PYTHON TESTS CANNOT. Alert expressions are a
language of their own, evaluated by Prometheus, and no amount of unit testing
around them says anything about whether `and on()` composes the way its author
believed. The only honest check is to hand them to the real evaluator.

SKIPPED WHERE PROMTOOL IS MISSING, so the suite still runs on a machine that
is not the monitor. That is a real gap rather than a tidy one: on such a
machine these rules are simply not covered.
"""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RULES = os.path.join(REPO, "alerting", "xxops-rules.yml")
FIXTURE = os.path.join(HERE, "rules_test.yml")


class Rules(unittest.TestCase):

    def setUp(self):
        if not shutil.which("promtool"):
            self.skipTest("promtool not installed here")
        if not os.path.isfile(RULES):
            self.skipTest("no rules file in this repo")

    def run_promtool(self, *args):
        return subprocess.run(["promtool"] + list(args),
                              capture_output=True, text=True, cwd=HERE)

    def test_the_rules_parse(self):
        r = self.run_promtool("check", "rules", RULES)
        self.assertEqual(r.returncode, 0,
                         "promtool rejected the rules:\n%s\n%s"
                         % (r.stdout, r.stderr))

    def test_the_scenarios_behave_as_expected(self):
        """
        The fixture carries its own explanation of what each scenario is for.
        The one that matters most: a stalled gateway must still alert when
        nothing else is wrong. Remove `or vector(0)` from the count guard and
        this fails with an empty result - GatewayDead stops firing entirely,
        rather than firing less often.
        """
        self.assertTrue(os.path.isfile(FIXTURE), "rules_test.yml is missing")
        r = self.run_promtool("test", "rules", FIXTURE)
        self.assertEqual(r.returncode, 0,
                         "a rule did not behave as expected:\n%s\n%s"
                         % (r.stdout, r.stderr))

    def test_every_alert_says_who_should_care(self):
        """
        Severity decides routing. An alert without one falls through to the
        fallback receiver, which is how something important ends up somewhere
        nobody reads.
        """
        import re
        with open(RULES, encoding="utf-8") as f:
            src = f.read()
        blocks = re.split(r"^      - alert: ", src, flags=re.M)[1:]
        missing = [b.splitlines()[0].strip() for b in blocks
                   if "severity:" not in b]
        self.assertEqual(missing, [], "no severity on: %s" % missing)

    def test_every_alert_has_a_summary(self):
        """The summary is what arrives on a phone at three in the morning."""
        import re
        with open(RULES, encoding="utf-8") as f:
            src = f.read()
        blocks = re.split(r"^      - alert: ", src, flags=re.M)[1:]
        missing = [b.splitlines()[0].strip() for b in blocks
                   if "summary:" not in b]
        self.assertEqual(missing, [], "no summary on: %s" % missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
