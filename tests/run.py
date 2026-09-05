#!/usr/bin/env python3
"""
run.py — the whole test runner.

    python3 tests/run.py            # run everything
    python3 tests/run.py route_auth # run one file

No framework, no configuration, no dependencies. Finds tests/test_*.py and
runs them with unittest. Exits non-zero if anything fails, so deploy.sh and
release.sh can simply refuse to continue.

WHY THERE IS NO FRAMEWORK. This project ships with no build step and no
third-party packages, and the test suite should not be the thing that
introduces either. unittest is in the standard library and is enough.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main():
    sys.path.insert(0, HERE)
    loader = unittest.TestLoader()
    if len(sys.argv) > 1:
        names = ["test_" + a.replace("test_", "") for a in sys.argv[1:]]
        suite = unittest.TestSuite(loader.loadTestsFromName(n) for n in names)
    else:
        suite = loader.discover(HERE, pattern="test_*.py")

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("\nall good")
        return 0
    print("\nSOMETHING IS WRONG - do not deploy until this is green")
    return 1


if __name__ == "__main__":
    sys.exit(main())
