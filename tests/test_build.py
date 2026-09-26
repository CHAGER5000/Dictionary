"""Tests for pipeline.build (data/english.sqlite and data/english_audit.sqlite).

The data checks read data/english.sqlite and its audit file. If either is
missing, tests/dbfixture.py builds both in a temporary directory first (about
40 seconds; needs data/oewn.sqlite or the WordNet XML, and the Soule and Roget
JSON lines or their books).
Run from the project root: python3 -m unittest discover -s tests -v
"""
import json
import sqlite3
import unittest
from pathlib import Path

try:
    from dbfixture import english_paths          # python3 -m unittest discover -s tests
except ImportError:
    from tests.dbfixture import english_paths    # python3 -m unittest tests.test_...
from pipeline import build, oewn

_STATE = {}
SOURCES = {"oewn2025", "soule1871", "roget1911"}
WORDNET_TABLES = ("synsets", "examples", "entries", "forms", "pronunciations", "senses",
                  "sense_relations", "synset_relations")


def setUpModule():
    path, audit = english_paths()
    _STATE["path"], _STATE["audit_path"] = path, audit
    _STATE["conn"] = oewn.open_db(path)
    _STATE["audit"] = oewn.open_db(audit)


def tearDownModule():
    for k in ("conn", "audit"):
        if k in _STATE:
            _STATE[k].close()


def conn():
    return _STATE["conn"]


def q(sql, *args):
    return conn().execute(sql, args).fetchall()


def one(sql, *args):
    return conn().execute(sql, args).fetchone()[0]


def audit(sql, *args):
    return _STATE["audit"].execute(sql, args).fetchall()


def soule_records():
    if "soule" not in _STATE:
        _STATE["soule"] = build._soule_records()
    return _STATE["soule"]


def wordnet():
    """The in-memory WordNet the build uses (loaded once, a few seconds)."""
    if "wn" not in _STATE:
        _STATE["wn"] = build.WordNet(conn())
    return _STATE["wn"]


class TablesTest(unittest.TestCase):
    def test_wordnet_tables_are_all_there(self):
        for t in WORDNET_TABLES + ("meta",):
            self.assertGreater(one(f"SELECT COUNT(*) FROM {t}"), 0, t)
        if build.OEWN_DB.exists():
            src = sqlite3.connect(build.OEWN_DB)
            for t in WORDNET_TABLES:
                self.assertEqual(one(f"SELECT COUNT(*) FROM {t}"),
                                 src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], t)
            src.close()

    def test_new_tables_have_sane_sizes(self):
        n = {t: one(f"SELECT COUNT(*) FROM {t}") for t in (
            "sense_synonyms", "thesaurus_alignments", "gloss_keys", "lemma_idf", "kid_filter")}
        self.assertGreater(n["sense_synonyms"], 230_000)
        self.assertGreater(n["thesaurus_alignments"], 15_000)
        self.assertEqual(n["gloss_keys"], one("SELECT COUNT(*) FROM synsets"))
        self.assertGreater(n["lemma_idf"], 20_000)
        self.assertGreater(n["kid_filter"], 500)
        by = dict(q("SELECT source, COUNT(*) FROM sense_synonyms GROUP BY source"))
        self.assertEqual(set(by), SOURCES)
        self.assertGreater(by["soule1871"], 30_000)
        self.assertGreater(by["roget1911"], 10_000)

    def test_raw_book_groups_are_only_in_the_audit_file(self):
        # the app's file must not carry the unfiltered book groups (slurs included)
        tables = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("thesaurus_unaligned", tables)
        self.assertNotIn("thesaurus_withheld", tables)
        self.assertGreater(audit("SELECT COUNT(*) FROM thesaurus_unaligned")[0][0], 20_000)
        self.assertGreater(audit("SELECT COUNT(*) FROM thesaurus_withheld")[0][0], 5_000)
        self.assertNotIn(b"gutenberg", Path(_STATE["audit_path"]).read_bytes().lower())

    def test_meta(self):
        meta = dict(q("SELECT key, value FROM meta"))
        self.assertIn("CC BY 4.0", meta["attribution"])
        self.assertIn("by the Open English WordNet team", meta["attribution"])
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", meta["attribution"])
        self.assertIn("Copyright (c) 2019-present, The Open English WordNet Team.",
                      meta["attribution"])
        self.assertIn("Princeton University WordNet database", meta["princeton_notice"])
        self.assertIn("WordNet 3.1 Copyright 2011 by Princeton University", meta["princeton_licence"])
        for change in ("joined with '; '", "example sentence was removed", "Soule (1871)",
                       "kid_filter", "blocklist", "British"):
            self.assertIn(change, meta["changes"])
        self.assertIn("public domain", meta["attribution_soule1871"])
        self.assertIn("public domain", meta["attribution_roget1911"])
        self.assertEqual(json.loads(meta["english_sources"]), ["oewn2025", "soule1871", "roget1911"])
        self.assertEqual(int(meta["idf_documents"]), one("SELECT COUNT(*) FROM synsets"))
        params = json.loads(meta["alignment_params"])
        self.assertEqual(params["MIN_SCORE"], build.MIN_SCORE)

    def test_file_never_mentions_gutenberg(self):
        for key in ("path", "audit_path"):
            self.assertNotIn(b"gutenberg", Path(_STATE[key]).read_bytes().lower())


class ProvenanceTest(unittest.TestCase):
    def test_every_row_has_a_known_source(self):
        for t in ("sense_synonyms", "thesaurus_alignments"):
            bad = one(f"SELECT COUNT(*) FROM {t} WHERE source NOT IN ('oewn2025', 'soule1871', "
                      "'roget1911') OR source IS NULL")
            self.assertEqual(bad, 0, t)
        for t in ("gloss_keys", "lemma_idf"):
            self.assertEqual(one(f"SELECT COUNT(*) FROM {t} WHERE source != 'oewn2025'"), 0, t)
        self.assertEqual(one("SELECT COUNT(*) FROM kid_filter WHERE source NOT IN "
                             "('oewn2025', 'build')"), 0)
        for t in ("thesaurus_unaligned", "thesaurus_withheld"):
            self.assertEqual(audit(f"SELECT COUNT(*) FROM {t} WHERE source NOT IN "
                                   "('soule1871', 'roget1911')")[0][0], 0, t)

    def test_wordnet_rows_are_synset_members(self):
        missing = one("SELECT COUNT(*) FROM sense_synonyms x LEFT JOIN senses s "
                      "ON s.id = x.ref AND s.synset_id = x.synset_id "
                      "WHERE x.source = 'oewn2025' AND s.id IS NULL")
        self.assertEqual(missing, 0)

    def test_old_book_rows_come_from_an_alignment(self):
        orphans = one(
            "SELECT COUNT(*) FROM sense_synonyms x WHERE x.source != 'oewn2025' AND NOT EXISTS "
            "(SELECT 1 FROM thesaurus_alignments a WHERE a.source = x.source AND a.ref = x.ref "
            "AND a.synset_id = x.synset_id)")
        self.assertEqual(orphans, 0)
        self.assertEqual(one("SELECT COUNT(*) FROM sense_synonyms WHERE source != 'oewn2025' "
                             "AND score < ?", build.MIN_SCORE), 0)
        self.assertEqual(one("SELECT COUNT(*) FROM sense_synonyms x LEFT JOIN synsets y "
                             "ON y.id = x.synset_id WHERE y.id IS NULL"), 0)

    def test_refs_point_back_into_the_books(self):
        soule = q("SELECT ref FROM thesaurus_alignments WHERE source = 'soule1871' LIMIT 200")
        self.assertTrue(all(r[0].split("#")[0].isdigit() and r[0].split("#")[1].isdigit()
                            for r in soule))
        roget = q("SELECT ref FROM thesaurus_alignments WHERE source = 'roget1911' LIMIT 200")
        self.assertTrue(all(":" in r[0] for r in roget))


class AlignmentTest(unittest.TestCase):
    """Alignments checked by hand against the books and WordNet."""

    def book_words(self, synset_id, source):
        return {r[0] for r in q("SELECT word FROM sense_synonyms WHERE synset_id = ? AND source = ?",
                                synset_id, source)}

    def test_soule_bright_sense_1_goes_to_the_light_meaning(self):
        # Soule, Bright a. 1: luminous, shining, resplendent, glowing ...
        words = self.book_words("oewn-00279417-a", "soule1871")
        self.assertTrue({"luminous", "shining", "resplendent", "glowing"} <= words, words)
        # ... and sense 3 (illustrious, glorious, famous) does not land there
        self.assertNotIn("illustrious", words)

    def test_soule_attorney(self):
        sid = one("SELECT synset_id FROM senses WHERE lemma = 'attorney' ORDER BY rank LIMIT 1")
        self.assertIn("solicitor", self.book_words(sid, "soule1871"))

    def test_roget_riddle_goes_to_difficult_problem_not_sieve(self):
        words = self.book_words("oewn-06798080-n", "roget1911")      # a difficult problem
        self.assertTrue({"enigma", "conundrum"} & words, words)
        self.assertNotIn("enigma", self.book_words("oewn-04095808-n", "roget1911"))  # sieve

    def test_weak_groups_are_left_unaligned(self):
        # Roget 342 Land lists "bank, lea": nothing ties "lea" to a sense of bank
        rows = audit("SELECT reason, words FROM thesaurus_unaligned WHERE source = 'roget1911' "
                     "AND headword = 'bank' AND ref = '342:8'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "below_threshold")
        self.assertEqual(json.loads(rows[0][1]), ["bank", "lea"])

    def test_unaligned_rows_are_well_formed(self):
        reasons = {r[0] for r in audit("SELECT DISTINCT reason FROM thesaurus_unaligned")}
        self.assertLessEqual(reasons, {"below_threshold", "tie", "long_group", "no_wordnet_entry",
                                       "unsupported_pos"})
        for words, reason, best in audit("SELECT words, reason, best_score FROM "
                                         "thesaurus_unaligned ORDER BY rowid LIMIT 500"):
            self.assertIsInstance(json.loads(words), list)
            if reason == "no_wordnet_entry":
                self.assertIsNone(best)

    def test_decide(self):
        self.assertEqual(build.decide([])[1], "no_wordnet_entry")
        self.assertEqual(build.decide([(0.5, "a"), (0.0, "b")])[1], "below_threshold")
        self.assertEqual(build.decide([(2.0, "a"), (1.9, "b")])[1], "tie")
        self.assertEqual(build.decide([(2.0, "a"), (1.0, "b")])[:2], ("a", "aligned"))
        # a Roget paragraph alone cannot align a group
        self.assertEqual(build.decide([(2.0, "a"), (0.0, "b")], {"a": 0.0, "b": 0.0})[1],
                         "below_threshold")
        # a rare sense must beat a commoner one by RATIO_UPSET, not just by MARGIN
        order = {"common": 0, "rare": 9}
        self.assertEqual(build.decide([(1.1, "rare"), (0.8, "common")], None, order)[1], "tie")
        self.assertEqual(build.decide([(1.3, "rare"), (0.8, "common")], None, order)[1], "aligned")
        self.assertEqual(build.decide([(1.1, "common"), (0.8, "rare")], None, order)[1], "aligned")

    def test_rare_sense_does_not_win_a_close_call(self):
        # Soule, Time n. 3: "Period, age, era, epoch, date, term." is not a prison term
        self.assertFalse(q("SELECT 1 FROM thesaurus_alignments WHERE source = 'soule1871' "
                           "AND headword = 'time' AND synset_id = 'oewn-15249488-n'"))

    def test_align_scores_members_highest(self):
        _, ranked = build.align(wordnet(), "bright", "a", ["luminous", "shining", "radiant"])
        self.assertEqual(ranked[0][1], "oewn-00279417-a")
        self.assertGreaterEqual(ranked[0][0], build.MIN_SCORE)

    def test_roget_lists_of_kinds_are_not_synonyms(self):
        # #271 Carrier lists kinds of horse ("horse, nag, palfrey, pony, filly, colt ...")
        colt = {r[0] for r in q("SELECT word FROM sense_synonyms WHERE synset_id = "
                                "'oewn-02379443-n' AND source = 'roget1911'")}
        self.assertFalse(colt & {"nag", "palfrey", "pony", "filly", "horse", "hack", "jade"}, colt)
        animal = {r[0] for r in q("SELECT word FROM sense_synonyms WHERE synset_id = "
                                  "'oewn-00015568-n' AND source = 'roget1911'")}
        self.assertFalse(animal & {"horse", "nag", "pony", "colt", "hack"}, animal)
        shoe_shop = {r[0] for r in q("SELECT word FROM sense_synonyms WHERE synset_id = "
                                     "'oewn-04207843-n' AND source = 'roget1911'")}
        self.assertFalse(shoe_shop & {"bookstore", "drugstore", "stationer"}, shoe_shop)
        # the group is kept for review instead: too long a list for its lone sense of "colt"
        rows = audit("SELECT reason FROM thesaurus_unaligned WHERE headword = 'colt' "
                     "AND ref = '271:5'")
        self.assertEqual([tuple(r) for r in rows], [("long_group",)])
        # words of an aligned group that WordNet does not link closely are withheld
        self.assertGreater(audit("SELECT COUNT(*) FROM thesaurus_withheld WHERE relation = "
                                 "'sibling'")[0][0], 500)

    def test_roget_relation_rule(self):
        wn = wordnet()
        light = "oewn-00279417-a"                          # bright: emitting light
        self.assertEqual(wn.relation(light, "bright"), "member")
        self.assertEqual(wn.relation("oewn-02379443-n", "foal"), "hypernym")
        self.assertIn(wn.relation("oewn-02379443-n", "pony"), ("sibling", "far"))
        self.assertEqual(wn.relation(light, "tongue of land"), "unknown")
        keep = build.roget_keeps(wn, "oewn-02379443-n", ["foal", "pony", "nag", "filly", "tit"])
        self.assertIsNone(keep["foal"])
        self.assertIsNotNone(keep["pony"])

    def test_unrecognised_roget_words_are_used(self):
        # "anaesthetize[obs3]" (#376): the mark only says a spelling checker did not know it
        self.assertTrue(q("SELECT 1 FROM sense_synonyms WHERE source = 'roget1911' "
                          "AND word = 'anaesthetize'"))

    def test_headword_words_do_not_count(self):
        # "lap dog" must not match "badger dog" through the shared word "dog"
        _, ranked = build.align(wordnet(), "badger dog", "n", ["lap dog", "house dog"])
        self.assertTrue(all(score < build.MIN_SCORE for score, _ in ranked), ranked)


class WordFilterTest(unittest.TestCase):
    def setUp(self):
        self.wf = build.WordFilter(wordnet(), set())

    def test_keeps_wordnet_lemmas_and_english_phrases(self):
        self.assertEqual(self.wf.clean("luminous"), "luminous")
        self.assertEqual(self.wf.clean("tongue of land"), "tongue of land")
        self.assertEqual(self.wf.clean("immerse one's self"), "immerse one's self")

    def test_drops_junk(self):
        for junk in ("4-1/2", "$", "", "tambreet", "sub dio", "a b c d e f"):
            self.assertIsNone(self.wf.clean(junk), junk)

    def test_drops_blocklisted_words(self):
        for w in ("bullshit", "fuckup", "fuck off", "dog shit", "jack off"):
            self.assertIsNone(self.wf.clean(w), w)

    def test_part_of_speech_must_fit(self):
        self.assertFalse(self.wf.fits_pos("gar", "v"))     # a fish, not a verb
        self.assertTrue(self.wf.fits_pos("make", "v"))
        self.assertTrue(self.wf.fits_pos("lay hold of", "v"))


class KidFilterTest(unittest.TestCase):
    def test_no_synonyms_on_unfit_meanings(self):
        self.assertEqual(one("SELECT COUNT(*) FROM sense_synonyms x JOIN kid_filter k "
                             "ON k.kind = 'synset' AND k.key = x.synset_id"), 0)

    def test_blocklisted_words_never_appear(self):
        keys = sorted(build.BLOCKLIST)
        qs = ",".join("?" * len(keys))
        self.assertEqual(one(f"SELECT COUNT(*) FROM sense_synonyms WHERE word_key IN ({qs})",
                             *keys), 0)
        # ... nor phrases and spellings built on them ("fuck off", "fuckup", "take a shit")
        found = [k for (k,) in q("SELECT DISTINCT word_key FROM sense_synonyms") if build.blocked(k)]
        self.assertEqual(found, [])
        words = {r[0] for r in q("SELECT key FROM kid_filter WHERE kind = 'word'")}
        self.assertTrue({"fuckup", "fuck off", "jack off", "take a shit", "dog shit"} <= words)

    def test_blocked_matches_phrases_but_not_innocent_compounds(self):
        for bad in ("fuckup", "fuck-up", "fuck off", "take a shit", "dog shit", "cock sucking",
                    "piss-up", "crap up", "jerk off", "son of a bitch", "shithead"):
            self.assertTrue(build.blocked(bad), bad)
        for ok in ("cock-a-doodle-doo", "bastard toadflax", "spotted dick", "blue tit",
                   "wankel engine", "horny layer", "cocktail", "assist", "scunner", "shittim",
                   "basic slag", "crap game", "title"):
            self.assertFalse(build.blocked(ok), ok)

    def test_soule_senses_labelled_low_or_vulgar_are_left_out(self):
        bad = {"low", "vulgar", "cant", "cant term", "cant word", "in contempt", "in derision"}
        refs = {f"{r['line']}#{s['n']}" for r in soule_records() for s in r["senses"]
                if {x.lower().rstrip(".") for x in s["labels"]} & bad}
        self.assertGreater(len(refs), 50)
        used = {r[0] for r in q("SELECT DISTINCT ref FROM sense_synonyms WHERE source = 'soule1871'")}
        self.assertEqual(refs & used, set())

    def test_offensive_and_slang_meanings_are_flagged(self):
        reasons = dict(q("SELECT reason, COUNT(*) FROM kid_filter WHERE kind = 'synset' "
                         "GROUP BY reason"))
        self.assertGreater(reasons.get("offensive", 0), 100)
        self.assertGreater(reasons.get("modern_slang", 0), 100)


class GlossKeysTest(unittest.TestCase):
    def test_bank_river_sense_keys(self):
        d, e = conn().execute("SELECT def_keys, ex_keys FROM gloss_keys WHERE synset_id = ?",
                              ("oewn-09236472-n",)).fetchone()
        self.assertTrue({"slope", "water", "land"} <= set(d.split()), d)
        self.assertIn("canoe", e.split())
        self.assertNotIn("the", d.split())

    def test_idf_orders_rare_above_common(self):
        idf = dict(q("SELECT key, idf FROM lemma_idf WHERE key IN ('use', 'water', 'canoe')"))
        self.assertLess(idf["use"], idf["water"])
        self.assertLess(idf["water"], idf["canoe"])


if __name__ == "__main__":
    unittest.main()
