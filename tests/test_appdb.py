"""The app database's reference picker and sound keys (the Swift core mirrors both;
see SwiftTests/DictionaryCoreTests, which uses the same fixture and cases)."""
import re
import sqlite3
import unittest
from pathlib import Path

from pipeline.appdb import OUT, app_pick, sound_key

SWIFT_TESTS = Path(__file__).resolve().parent.parent / "SwiftTests" / "DictionaryCoreTests" / "DictionaryCoreTests.swift"


def swift_fixture() -> sqlite3.Connection:
    """The SQL fixture from the Swift tests, so both languages test the same data."""
    src = SWIFT_TESTS.read_text()
    sql = src[src.index('try db.execute("""') + len('try db.execute("""'):src.index('""")')]
    db = sqlite3.connect(":memory:")
    db.executescript(sql)
    return db


class SoundKeyTests(unittest.TestCase):
    def test_same_cases_as_swift(self):
        src = SWIFT_TESTS.read_text()
        block = src[src.index("func testSoundKeyMatchesDataBuild"):]
        block = block[:block.index("for (word, key) in cases")]
        cases = re.findall(r'\("([^"]*)", "([^"]*)"\)', block)
        self.assertGreater(len(cases), 20)
        for word, key in cases:
            self.assertEqual(sound_key(word), key, word)


class AppPickTests(unittest.TestCase):
    def setUp(self):
        self.db = swift_fixture()

    def test_river_bank(self):
        r = app_pick(self.db, "The little boat drifted slowly towards the bank, where the ducks were waiting.", "bank")
        self.assertEqual(r[0]["synset_id"], "river-bank")
        self.assertEqual(r[0]["clues"], ["boat", "ducks"])

    def test_money_bank(self):
        r = app_pick(self.db, "I paid the money into my bank.", "bank")
        self.assertEqual(r[0]["synset_id"], "money-bank")
        self.assertEqual(r[0]["clues"], ["money", "paid"])

    def test_no_clues_first_meaning(self):
        r = app_pick(self.db, "Look at the bank.", "bank")
        self.assertEqual(r[0]["synset_id"], "money-bank")
        self.assertEqual(r[0]["clues"], [])

    def test_verb_after_modal(self):
        r = app_pick(self.db, "We can bank the plane.", "bank")
        self.assertEqual((r[0]["synset_id"], r[0]["clues"], r[0]["score"]), ("bank-tilt", ["plane"], 13.692))

    def test_clue_through_its_own_meaning(self):
        r = app_pick(self.db, "The pond is by the bank.", "bank")
        self.assertEqual((r[0]["synset_id"], r[0]["clues"], r[0]["score"]), ("river-bank", ["pond"], 6.786))

    def test_adjective_after_degree_word(self):
        r = app_pick(self.db, "The torch was so bright.", "bright")
        self.assertEqual((r[0]["synset_id"], r[0]["clues"], r[0]["score"]), ("bright-light", ["torch"], 6.972))

    def test_scores_match_swift(self):
        r = app_pick(self.db, "The little boat drifted slowly towards the bank, where the ducks were waiting.", "bank")
        self.assertEqual([x["synset_id"] for x in r], ["river-bank", "money-bank", "bank-tilt"])
        self.assertEqual([x["score"] for x in r[:2]], [13.477, 6.0])

    def test_unknown_word(self):
        self.assertEqual(app_pick(self.db, "A zebra ran.", "zebra"), [])


@unittest.skipUnless(OUT.exists(), "data/app_en.sqlite not built (python3 -m pipeline.appdb)")
class BuiltDatabaseTests(unittest.TestCase):
    def test_real_bank_sentence(self):
        db = sqlite3.connect(OUT)
        r = app_pick(db, "The little boat drifted slowly towards the bank, where the ducks were waiting.", "bank")
        self.assertIn("slope", r[0]["definition"])
        self.assertIn("boat", r[0]["clues"])

    def test_test_set_accuracy(self):
        from pipeline.appdb import evaluate
        self.assertGreaterEqual(evaluate()["test"]["accuracy"], 0.6)

    def test_no_blocked_words(self):
        db = sqlite3.connect(OUT)
        tables = {n for n, in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"entry", "meaning", "sig", "kw", "tok", "syn", "sound", "meta"} <= tables)
        self.assertIsNotNone(db.execute("SELECT value FROM meta WHERE key = 'attribution'").fetchone())


if __name__ == "__main__":
    unittest.main()
