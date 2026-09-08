#!/usr/bin/env python3
"""
test_app_js.py — run the app's JavaScript tests through a JS engine.

Everything else in this suite tests Python. The app's own logic had no
automated check at all until now: the state reclassification was verified by
opening the app and it looking right, on a fleet that happened to be healthy
at the time.

WHAT IS COVERED, in tests/app_tests.js: the nine classification branches and
their reason codes, the reason table in both directions, and the formatting
helpers - esc() in particular, which is the escaping applied to host names and
titles before they are put into HTML.

ENGINE. node is what is installed, but nothing here depends on node
specifically - qjs (QuickJS) is tried as well, so swapping the engine later is
a matter of installing the other one. If neither is present the tests skip,
and that is a genuine gap rather than a tidy one: on such a machine the app's
logic is simply not covered.
"""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.join(HERE, "app_tests.js")

ENGINES = ("node", "qjs")


def engine():
    for e in ENGINES:
        if shutil.which(e):
            return e
    return None


class AppJavaScript(unittest.TestCase):

    def setUp(self):
        self.engine = engine()
        if not self.engine:
            self.skipTest("no javascript engine (tried: %s)" % ", ".join(ENGINES))
        if not os.path.isfile(SUITE):
            self.skipTest("app_tests.js is missing")

    def test_the_app_javascript_behaves(self):
        r = subprocess.run([self.engine, SUITE],
                           capture_output=True, text=True, cwd=HERE)
        if r.returncode != 0:
            self.fail("%s\n%s" % (r.stdout.strip(), r.stderr.strip()))

    def test_every_app_file_parses(self):
        """
        A syntax error in any of the eight app files breaks the page silently -
        the browser stops executing that script and everything after it in the
        same file simply never runs.
        """
        app = os.path.join(os.path.dirname(HERE), "app")
        if not os.path.isdir(app):
            self.skipTest("no app directory")
        if self.engine != "node":
            self.skipTest("--check is a node feature")
        bad = []
        for name in sorted(os.listdir(app)):
            if not name.endswith(".js"):
                continue
            r = subprocess.run(["node", "--check", os.path.join(app, name)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                bad.append("%s: %s" % (name, r.stderr.strip().splitlines()[-1]))
        self.assertEqual(bad, [], "these do not parse:\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
