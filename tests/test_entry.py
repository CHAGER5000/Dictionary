"""Tests for pipeline.entry (the word screen) and the demo files it writes.

They read data/english.sqlite, or a temporary copy built by tests/dbfixture.py.
Run from the project root: python3 -m unittest discover -s tests -v
"""
import json
import unittest

try:
    from dbfixture import english_paths          # python3 -m unittest discover -s tests
except ImportError:
    from tests.dbfixture import english_paths    # python3 -m unittest tests.test_...
from pipeline import context, entry
from pipeline.entry import DEMO_BANK_SENTENCE, word_entry

_STATE = {}
SOURCES = {"oewn2025", "soule1871", "roget1911"}


def setUpModule():
    _STATE["conn"] = context.open_english(english_paths()[0])


def tearDownModule():
    if "conn" in _STATE:
        _STATE["conn"].close()


def conn():
    return _STATE["conn"]


def unfit_words():
    return {r[0] for r in conn().execute("SELECT key FROM kid_filter WHERE kind = 'word'")}


class BankScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = word_entry(conn(), "bank", sentence=DEMO_BANK_SENTENCE, level="junior")

    def test_header(self):
        e = self.e
        self.assertEqual(e["headword"], "bank")
        self.assertEqual(e["part_of_speech"], "noun")
        self.assertTrue(e["found"])
        self.assertTrue(e["chosen_from_sentence"])
        self.assertEqual(e["sentence"], DEMO_BANK_SENTENCE)

    def test_the_meaning_that_fits_comes_first_with_clues(self):
        first = self.e["meanings"][0]
        self.assertEqual(first["synset_id"], "oewn-09236472-n")
        self.assertFalse(first["folded"])
        self.assertEqual(first["clues"], ["boat", "drifted"])
        self.assertEqual([l["clue"] for l in first["clue_links"]], first["clues"])
        self.assertIn({"clue": "boat", "matched": "water", "via": "definition, through the "
                       "clue's own meaning", "channel": "indirect"}, first["clue_links"])
        self.assertGreater(first["margin_over_next"], 0)
        self.assertEqual(first["example"], "they pulled the canoe up on the bank")

    def test_other_meanings_are_folded_in_wordnet_order(self):
        rest = self.e["meanings"][1:]
        self.assertTrue(rest)
        self.assertTrue(all(m["folded"] for m in rest))
        self.assertEqual(rest[0]["synset_id"], "oewn-08437235-n")   # the money bank, rank 2
        self.assertTrue(all("clues" not in m for m in rest))

    def test_junior_limits(self):
        self.assertLessEqual(len(self.e["meanings"]), entry.JUNIOR_MEANINGS)
        self.assertGreater(self.e["more_meanings"], 0)
        for m in self.e["meanings"]:
            self.assertLessEqual(len(m["synonyms"]), 6)
            for s in m["synonyms"]:
                self.assertLessEqual(len(s["word"].replace("-", " ").split()), 2)

    def test_related_words_when_synonyms_are_few(self):
        first = self.e["meanings"][0]
        self.assertIn("riverbank", [r["word"] for r in first["related"]])


class BrightScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = word_entry(conn(), "bright", level="explorer")

    def test_every_meaning_in_wordnet_order(self):
        e = self.e
        self.assertFalse(e["chosen_from_sentence"])
        self.assertEqual(e["more_meanings"], 0)
        self.assertEqual(e["meanings"][0]["synset_id"], "oewn-00279417-a")
        self.assertEqual(e["part_of_speech"], "adjective")
        self.assertEqual(e["meanings"][-1]["part_of_speech"], "adverb")
        self.assertGreaterEqual(len(e["meanings"]), 10)

    def test_synonyms_for_that_meaning_only(self):
        light = self.e["meanings"][0]
        words = [s["word"] for s in light["synonyms"]]
        self.assertLessEqual(len(words), 10)
        self.assertIn("radiant", words)
        self.assertNotIn("smart", words)          # belongs to "quick to learn"
        smart = next(m for m in self.e["meanings"] if m["synset_id"] == "oewn-01338411-s")
        self.assertIn("smart", [s["word"] for s in smart["synonyms"]])
        self.assertNotIn("radiant", [s["word"] for s in smart["synonyms"]])

    def test_antonyms(self):
        self.assertIn("dull", [a["word"] for a in self.e["meanings"][0]["antonyms"]])
        colour = next(m for m in self.e["meanings"] if "striking color" in m["definition"])
        self.assertIn("colourless", [a["word"] for a in colour["antonyms"]])   # British spelling

    def test_attribution_names_the_creators_and_princeton(self):
        text = self.e["sources"]["oewn2025"]
        for part in ("Open English WordNet team", "CC BY 4.0",
                     "https://creativecommons.org/licenses/by/4.0/",
                     "Princeton University WordNet database"):
            self.assertIn(part, text)

    def test_old_book_synonyms_are_used(self):
        used = {src for m in self.e["meanings"] for s in m["synonyms"] for src in s["sources"]}
        self.assertEqual(used, SOURCES)
        self.assertEqual(set(self.e["sources"]), SOURCES)


class RulesTest(unittest.TestCase):
    def test_provenance_everywhere(self):
        for e in (word_entry(conn(), "bank", DEMO_BANK_SENTENCE),
                  word_entry(conn(), "bright", level="explorer")):
            for m in e["meanings"]:
                self.assertEqual(m["source"], "oewn2025")
                if m["example"] is not None:
                    self.assertEqual(m["example_source"], "oewn2025")
                for s in m["synonyms"]:
                    self.assertTrue(s["sources"] and set(s["sources"]) <= SOURCES)
                for a in m["antonyms"] + m.get("related", []):
                    self.assertEqual(a["source"], "oewn2025")

    def test_headword_is_never_its_own_synonym(self):
        for word in ("bank", "bright", "run", "light"):
            e = word_entry(conn(), word, level="explorer")
            for m in e["meanings"]:
                self.assertNotIn(m["word"].lower(), [s["word"].lower() for s in m["synonyms"]])

    def test_no_unfit_words(self):
        bad = unfit_words()
        for word in ("pig", "cow", "bitch", "fool"):
            e = word_entry(conn(), word, level="explorer")
            for m in e["meanings"]:
                self.assertFalse({s["word"].lower() for s in m["synonyms"]} & bad, word)

    def test_inflected_tap_shows_the_dictionary_form(self):
        e = word_entry(conn(), "ducks", sentence=DEMO_BANK_SENTENCE)
        self.assertEqual(e["headword"], "duck")
        self.assertEqual(e["tapped"], "ducks")
        self.assertIn("swimming bird", e["meanings"][0]["definition"])

    def test_no_explicit_synonyms(self):
        # phrases built on blocked words ("fuckup", "jack off") never reach a screen
        bad = {"fuckup", "fuck off", "jack off", "jerk off", "she-bop", "wank"}
        for word in ("botch", "bungle", "blunder", "masturbate"):
            for level in ("junior", "explorer"):
                for m in word_entry(conn(), word, level=level)["meanings"]:
                    self.assertFalse({s["word"] for s in m["synonyms"]} & bad, (word, level))

    def test_missing_kid_filter_is_an_error(self):
        import sqlite3
        bare = sqlite3.connect(":memory:")
        with self.assertRaises(RuntimeError):
            entry._unfit(bare)

    def test_tap_position(self):
        sentence = "The bat flew out of the cave as Tom swung his cricket bat."
        e = word_entry(conn(), "bat", sentence=sentence, at=sentence.rindex("bat"))
        self.assertIn("cricket", e["meanings"][0]["definition"])

    def test_unknown_word(self):
        e = word_entry(conn(), "zzzqx")
        self.assertFalse(e["found"])
        self.assertEqual(e["meanings"], [])

    def test_bad_level(self):
        with self.assertRaises(ValueError):
            word_entry(conn(), "bank", level="expert")


class DemoFilesTest(unittest.TestCase):
    def test_demo_files_match_the_code(self):
        for name, args in (("demo_bank.json", ("bank", DEMO_BANK_SENTENCE, "junior")),
                           ("demo_bright.json", ("bright", None, "explorer"))):
            path = entry.DATA / name
            self.assertTrue(path.exists(), f"{path} missing; run python3 -m pipeline.entry")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("gutenberg", text.lower())
            self.assertEqual(json.loads(text), word_entry(conn(), *args),
                             f"{name} is stale; run python3 -m pipeline.entry")


if __name__ == "__main__":
    unittest.main()
