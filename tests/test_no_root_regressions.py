"""Tripwires for the two root bugs found in the producer.

Both are fixed. These exist so they cannot come back quietly.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCER = os.path.join(ROOT, "producer", "xxops-textfile.sh")
INSTALLER = os.path.join(ROOT, "fixes", "xxops-host-install.sh")


def code_lines(path):
    """Lines with whole-line comments dropped, so prose cannot trip us."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return [l for l in lines if not l.strip().startswith("#")]


class NoRootRegressions(unittest.TestCase):

    def test_the_collector_unit_runs_as_alloy(self):
        """The account is why those bugs were critical, not the parsing.

        The same mistake as alloy costs an unprivileged account; as root it
        costs the host and the validator keys on it.
        """
        with open(INSTALLER, encoding="utf-8") as fh:
            text = fh.read()
        i = text.index("xxops-textfile.service")
        self.assertIn("User=alloy", text[i:i + 800],
                      "the collector unit no longer sets User=alloy")

    def test_the_producer_has_no_bash_c(self):
        """A gateway.yaml value reaching bash -c was root RCE every 60s."""
        bad = [l for l in code_lines(PRODUCER) if "bash -c" in l]
        self.assertEqual(bad, [], "bash -c is back: %r" % bad)

    def test_the_producer_has_no_eval(self):
        """Removed with the injection fix. It only ever stripped spaces."""
        bad = [l for l in code_lines(PRODUCER)
               if l.strip().startswith("eval ") or " eval " in l]
        self.assertEqual(bad, [], "eval is back: %r" % bad)

    def test_pythonpath_is_only_set_under_runuser(self):
        """The other root path: importing a service user's writable
        site-packages as root. Setting PYTHONPATH is fine; doing it without
        dropping to that user first is the bug."""
        bad = [l for l in code_lines(PRODUCER)
               if "PYTHONPATH=" in l and "runuser" not in l]
        self.assertEqual(bad, [], "PYTHONPATH set without runuser: %r" % bad)


if __name__ == "__main__":
    unittest.main()
