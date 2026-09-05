#!/usr/bin/env python3
"""
test_resolutions.py — the fixes store: bundled entries, operator overrides
and tombstones.

WHAT MAKES THIS WORTH TESTING. Three sources of truth have to agree without a
merge conflict: what ships with the software, what the operator wrote, and
what the operator explicitly did not want. Every failure mode here is quiet -
an entry silently disappears, or one they deleted silently comes back after a
restart. Nothing errors.

THE SUBTLE ONE, and the reason this file exists, is the tombstone carry
forward. Suppressing a bundled entry writes a tombstone. Tombstones are never
shown, so they are never in the list a caller hands back to save. If save did
not carry them across from the previous file, then deleting ANY unrelated
entry would drop every tombstone and resurrect every bundled fix the operator
had ever suppressed. test_deleting_something_else_does_not_resurrect_the_dead
is that scenario.

THE OTHER INVARIANT is that the bundled file is read-only. If the operator's
copies were ever written back into it, an update could no longer refresh them
and the operator's edits would be silently frozen.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server_harness as sh


class Resolutions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = sh.scratch()
        cls.mod = sh.load_server(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        # a bundled file of our own, so the real one is never touched
        self.bundled_path = os.path.join(self.dir, "bundled-test.json")
        self.mod.BUNDLED_FIXES = self.bundled_path
        self.write_bundled([])
        self.write_local([])

    # -- helpers ---------------------------------------------------------

    def entry(self, eid, title=None):
        return {"id": eid, "title": title or ("fix " + eid), "created": 1}

    def write_bundled(self, entries):
        with open(self.bundled_path, "w") as f:
            json.dump(entries, f)

    def write_local(self, entries):
        with open(self.mod.RESOLUTIONS, "w") as f:
            json.dump(entries, f)

    def read_local(self):
        with open(self.mod.RESOLUTIONS) as f:
            return json.load(f)

    def ids(self, entries):
        return sorted(e["id"] for e in entries)

    # -- the merge -------------------------------------------------------

    def test_a_fresh_install_still_has_the_bundled_fixes(self):
        """The whole point of shipping them: a new install is not an empty box."""
        self.write_bundled([self.entry("b1"), self.entry("b2")])
        os.unlink(self.mod.RESOLUTIONS)
        self.assertEqual(self.ids(self.mod.load_resolutions()), ["b1", "b2"])

    def test_bundled_entries_are_marked_as_such(self):
        """The app needs to tell them apart to warn before an edit."""
        self.write_bundled([self.entry("b1")])
        got = self.mod.load_resolutions()[0]
        self.assertTrue(got.get("bundled"))

    def test_the_operators_own_entries_are_not_marked_bundled(self):
        self.write_local([self.entry("mine")])
        got = [e for e in self.mod.load_resolutions() if e["id"] == "mine"][0]
        self.assertFalse(got.get("bundled"))

    def test_an_operator_entry_wins_over_a_bundled_one_with_the_same_id(self):
        """Editing a shipped fix has to survive an update that changes it."""
        self.write_bundled([self.entry("b1", "the shipped wording")])
        self.write_local([self.entry("b1", "my wording")])
        got = self.mod.load_resolutions()
        self.assertEqual(len(got), 1, "the same id appeared twice")
        self.assertEqual(got[0]["title"], "my wording")
        self.assertFalse(got[0].get("bundled"),
                         "an edited entry is the operator's, not the bundle's")

    def test_a_bundled_entry_without_an_id_is_ignored(self):
        """Nothing can override or suppress what cannot be named."""
        self.write_bundled([{"title": "no id here"}, self.entry("b1")])
        self.assertEqual(self.ids(self.mod.load_resolutions()), ["b1"])

    def test_a_missing_bundle_file_is_not_an_error(self):
        self.mod.BUNDLED_FIXES = os.path.join(self.dir, "not-there.json")
        self.write_local([self.entry("mine")])
        self.assertEqual(self.ids(self.mod.load_resolutions()), ["mine"])

    def test_an_unreadable_bundle_file_is_not_an_error(self):
        with open(self.bundled_path, "w") as f:
            f.write("{ not json at all")
        self.write_local([self.entry("mine")])
        self.assertEqual(self.ids(self.mod.load_resolutions()), ["mine"])

    # -- tombstones ------------------------------------------------------

    def test_a_tombstone_suppresses_a_bundled_entry(self):
        self.write_bundled([self.entry("b1"), self.entry("b2")])
        self.write_local([{"id": "b1", "deleted": True}])
        self.assertEqual(self.ids(self.mod.load_resolutions()), ["b2"])

    def test_a_tombstone_is_never_shown(self):
        """It is bookkeeping, not an entry. It must not reach the app."""
        self.write_bundled([self.entry("b1")])
        self.write_local([{"id": "b1", "deleted": True}])
        for e in self.mod.load_resolutions():
            self.assertFalse(e.get("deleted"))

    def test_deleting_something_else_does_not_resurrect_the_dead(self):
        """
        THE ONE THIS FILE EXISTS FOR.

        Suppress a bundled entry, then delete an unrelated entry of your own.
        The caller's list never contained the tombstone, because tombstones are
        never shown - so save has to carry it forward from the previous file.
        If it does not, every bundled fix ever suppressed comes back.
        """
        self.write_bundled([self.entry("b1"), self.entry("b2")])
        self.write_local([{"id": "b1", "deleted": True},
                          self.entry("mine"), self.entry("other")])

        # what the app would hold, having deleted "other"
        remaining = [e for e in self.mod.load_resolutions()
                     if e["id"] != "other"]
        self.mod.save_resolutions(remaining)

        after = self.ids(self.mod.load_resolutions())
        self.assertNotIn("b1", after, "a suppressed bundled fix came back")
        self.assertNotIn("other", after, "the deletion did not stick")
        self.assertEqual(after, ["b2", "mine"])

    def test_a_tombstone_gives_way_if_the_id_is_written_again(self):
        """
        Re-creating an entry with a suppressed id must not leave both a
        tombstone and a live entry, or which one wins becomes a coin toss.
        """
        self.write_local([{"id": "b1", "deleted": True}])
        self.mod.save_resolutions([self.entry("b1", "back again")])
        raw = self.read_local()
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["title"], "back again")
        self.assertFalse(raw[0].get("deleted"))

    # -- what must never be written --------------------------------------

    def test_bundled_entries_are_never_written_to_the_operators_file(self):
        """
        If they were, they would become the operator's copies and an update
        could no longer refresh them.
        """
        self.write_bundled([self.entry("b1")])
        self.mod.save_resolutions(self.mod.load_resolutions())
        raw = self.read_local()
        self.assertEqual([e for e in raw if e.get("id") == "b1"], [],
                         "a bundled entry was written into the operator's file")

    def test_the_bundled_file_itself_is_never_modified(self):
        self.write_bundled([self.entry("b1")])
        with open(self.bundled_path) as f:
            before = f.read()
        self.mod.save_resolutions(self.mod.load_resolutions() +
                                  [self.entry("mine")])
        with open(self.bundled_path) as f:
            self.assertEqual(f.read(), before,
                             "the shipped file was written to")

    def test_saving_is_atomic(self):
        """
        The write goes to a temp file and is renamed, so an interrupted save
        cannot leave a half-written file where the fixes used to be.
        """
        self.mod.save_resolutions([self.entry("mine")])
        leftovers = [f for f in os.listdir(os.path.dirname(self.mod.RESOLUTIONS))
                     if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], "a temp file was left behind")
        self.assertEqual(self.ids(self.read_local()), ["mine"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
