"""Tests for pipeline.oewn (Open English WordNet 2025 -> data/oewn.sqlite).

The real-data checks use data/oewn.sqlite; if it is missing they build a copy in
a temporary directory first (about 10 seconds). Expected senses were checked by
hand against sources/english-wordnet-2025.xml.gz.
Run from the project root: python3 -m unittest discover -s tests -v
"""
import gzip
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from pipeline import oewn
from pipeline.oewn import (antonyms, lemmatize, members, open_db, related, senses_for,
                           synset)

_STATE = {}


def setUpModule():
    path = oewn.OUT
    if not path.exists():
        if not oewn.XML.exists():
            raise unittest.SkipTest(f"{oewn.XML} missing")
        _STATE["tmp"] = tempfile.mkdtemp(prefix="oewn-test-")
        path = Path(_STATE["tmp"]) / "oewn.sqlite"
        oewn.build(oewn.XML, path)
    _STATE["path"] = path
    _STATE["conn"] = open_db(path)


def tearDownModule():
    if "conn" in _STATE:
        _STATE["conn"].close()
    if "tmp" in _STATE:
        shutil.rmtree(_STATE["tmp"], ignore_errors=True)


def conn():
    return _STATE["conn"]


def lemmas_of(word):
    return lemmatize(conn(), word)


class BankTest(unittest.TestCase):
    def test_a_bank_noun_sense_is_about_a_river_or_slope(self):
        defs = [s["definition"] for s in senses_for(conn(), "bank", "n")]
        self.assertTrue(any("river" in d or "slope" in d for d in defs), defs)

    def test_first_bank_noun_sense_by_rank(self):
        senses = senses_for(conn(), "bank", "n")
        first = senses[0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["synset_id"], "oewn-09236472-n")
        self.assertIn("sloping land", first["definition"])
        self.assertEqual(first["examples"][0], "they pulled the canoe up on the bank")
        # the financial institution is sense 2
        self.assertEqual(senses[1]["rank"], 2)
        self.assertIn("financial institution", senses[1]["definition"])
        self.assertIn("banking company", senses[1]["synonyms"])
        self.assertNotIn("bank", senses[1]["synonyms"])
        self.assertIn("bank", senses[1]["members"])

    def test_ranks_run_1_to_n_within_each_entry(self):
        by_entry = {}
        for s in senses_for(conn(), "bank"):
            by_entry.setdefault(s["entry_id"], []).append(s["rank"])
        self.assertEqual(set(by_entry), {"oewn-bank-n", "oewn-bank-v"})
        for ranks in by_entry.values():
            self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_nouns_before_verbs_when_no_pos_given(self):
        poss = [s["pos"] for s in senses_for(conn(), "bank")]
        self.assertEqual(poss, sorted(poss, key=lambda p: "nvar".index(p)))
        self.assertIn("v", poss)

    def test_lookup_is_case_insensitive(self):
        a = [s["sense_id"] for s in senses_for(conn(), "bank", "n")]
        self.assertEqual(a, [s["sense_id"] for s in senses_for(conn(), "BANK", "noun")])
        self.assertEqual(a, [s["sense_id"] for s in senses_for(conn(), " Bank ", "n")])

    def test_homograph_entries_stay_grouped(self):
        entries = [s["entry_id"] for s in senses_for(conn(), "bass", "n")]
        self.assertEqual(entries, sorted(entries))
        self.assertEqual(set(entries), {"oewn-bass-n-1", "oewn-bass-n-2"})

    def test_exact_case_entry_first(self):
        self.assertEqual(senses_for(conn(), "march", "n")[0]["lemma"], "march")
        self.assertIn("March", {s["lemma"] for s in senses_for(conn(), "march", "n")})

    def test_unknown_word(self):
        self.assertEqual(senses_for(conn(), "xyzzyplugh"), [])
        with self.assertRaises(ValueError):
            senses_for(conn(), "bank", "noun phrase")


class BrightTest(unittest.TestCase):
    def setUp(self):
        self.senses = senses_for(conn(), "bright", "adj")

    def test_at_least_three_adjective_senses(self):
        self.assertGreaterEqual(len(self.senses), 3)
        self.assertTrue(all(s["pos"] == "a" for s in self.senses))
        self.assertEqual({s["synset_pos"] for s in self.senses}, {"a", "s"})
        self.assertEqual(self.senses, senses_for(conn(), "bright", "a"))
        self.assertEqual(self.senses, senses_for(conn(), "bright", "s"))

    def test_a_sense_about_light(self):
        self.assertTrue(any("light" in s["definition"] for s in self.senses))
        self.assertIn("light", self.senses[0]["definition"])

    def test_a_sense_about_intelligence(self):
        # OEWN words it "quickness and ease in learning"; it is a satellite of
        # the head synset "intelligent" and has the synonym "smart".
        clever = [s for s in self.senses
                  if "learning" in s["definition"] or "intelligen" in s["definition"]]
        self.assertTrue(clever, [s["definition"] for s in self.senses])
        s = clever[0]
        self.assertIn("smart", s["synonyms"])
        heads = related(conn(), s["synset_id"], "similar")
        self.assertIn("intelligent", [m for h in heads for m in h["members"]])

    def test_synonyms_belong_to_one_meaning_only(self):
        light = self.senses[0]
        clever = next(s for s in self.senses if "learning" in s["definition"])
        self.assertNotIn("smart", light["synonyms"])
        self.assertNotIn("smart", senses_for(conn(), "bright", "a")[1]["synonyms"])
        self.assertIn("smart", clever["synonyms"])


class RelationTest(unittest.TestCase):
    def test_members_in_synset_order(self):
        m = members(conn(), "oewn-08437235-n")
        self.assertEqual(m[0], "depository financial institution")
        self.assertIn("bank", m)
        self.assertEqual(members(conn(), "no-such-synset"), [])

    def test_hypernym_of_river_bank_is_slope(self):
        hyper = related(conn(), "oewn-09236472-n", "hypernym")
        self.assertEqual(len(hyper), 1)
        self.assertIn("slope", hyper[0]["members"])
        back = related(conn(), hyper[0]["synset_id"], "hyponym")
        self.assertIn("oewn-09236472-n", [h["synset_id"] for h in back])

    def test_antonyms_by_sense_and_synset(self):
        good = senses_for(conn(), "good", "a")[0]
        self.assertEqual([a["lemma"] for a in antonyms(conn(), good["sense_id"])], ["bad"])
        self.assertIn("bad", [a["lemma"] for a in antonyms(conn(), good["synset_id"])])
        self.assertTrue(all(a["source"] == oewn.SOURCE_ID for a in antonyms(conn(), good["sense_id"])))

    def test_indirect_antonym_through_head_adjective(self):
        clever = next(s for s in senses_for(conn(), "bright", "a")
                      if "learning" in s["definition"])
        self.assertEqual(antonyms(conn(), clever["sense_id"]), [])
        ind = antonyms(conn(), clever["sense_id"], indirect=True)
        self.assertIn("unintelligent", [a["lemma"] for a in ind])
        self.assertTrue(all(a["indirect"] for a in ind))

    def test_synset_lookup(self):
        s = synset(conn(), "oewn-09236472-n")
        self.assertEqual(s["pos"], "n")
        self.assertEqual(s["lexfile"], "noun.object")
        self.assertEqual(s["ili"], "i85041")
        self.assertEqual(s["source"], oewn.SOURCE_ID)
        self.assertIsNone(synset(conn(), "oewn-00000000-n"))


class LemmatizeTest(unittest.TestCase):
    def assertLemma(self, word, lemma, pos):
        self.assertIn((lemma, pos), lemmas_of(word), f"{word} -> {lemmas_of(word)}")

    def test_required_cases(self):
        self.assertLemma("ducks", "duck", "n")
        self.assertLemma("drifted", "drift", "v")
        self.assertLemma("mice", "mouse", "n")
        self.assertLemma("waiting", "wait", "v")
        self.assertLemma("brighter", "bright", "a")
        got = {lemma for lemma, _ in lemmas_of("better")}
        self.assertTrue({"good", "well"} & got, got)

    def test_more_rules(self):
        for word, lemma, pos in [
                ("stopping", "stop", "v"), ("stopped", "stop", "v"), ("bigger", "big", "a"),
                ("biggest", "big", "a"), ("happier", "happy", "a"), ("carried", "carry", "v"),
                ("churches", "church", "n"), ("boxes", "box", "n"), ("babies", "baby", "n"),
                ("making", "make", "v"), ("baked", "bake", "v"), ("nicer", "nice", "a"),
                ("went", "go", "v"), ("geese", "goose", "n"), ("children", "child", "n"),
                ("wolves", "wolf", "n"), ("lying", "lie", "v"), ("best", "good", "a")]:
            self.assertLemma(word, lemma, pos)

    def test_word_that_is_a_lemma_comes_first(self):
        self.assertEqual(lemmas_of("bank")[:2], [("bank", "n"), ("bank", "v")])
        self.assertEqual(lemmas_of("better")[0][0], "better")

    def test_case_punctuation_and_possessive(self):
        self.assertLemma("Ducks,", "duck", "n")
        self.assertLemma("“Mice”", "mouse", "n")
        self.assertLemma("children's", "child", "n")
        self.assertLemma("dog’s", "dog", "n")

    def test_only_existing_lemmas(self):
        self.assertEqual(lemmas_of("xyzzyplugh"), [])
        self.assertEqual(lemmas_of(""), [])
        self.assertEqual(lemmas_of("!!"), [])
        for lemma, pos in lemmas_of("singing"):
            self.assertTrue(senses_for(conn(), lemma, pos), (lemma, pos))
        self.assertNotIn(("sing", "n"), lemmas_of("singing"))

    def test_no_duplicates(self):
        got = lemmas_of("better")
        self.assertEqual(len(got), len(set(got)))


class DatabaseTest(unittest.TestCase):
    def meta(self):
        return dict(conn().execute("SELECT key, value FROM meta"))

    def test_licence_and_version_from_header(self):
        m = self.meta()
        self.assertTrue("CC BY 4.0" in m["licence"] or "Creative Commons" in m["licence"])
        self.assertEqual(m["licence_url"], "https://creativecommons.org/licenses/by/4.0")
        self.assertEqual(m["version"], "2025")
        self.assertEqual(m["source_id"], "oewn2025")
        self.assertIn("CC BY 4.0", m["attribution"])
        self.assertIn("Open English", m["attribution"])

    def test_row_counts_in_sane_ranges(self):
        ranges = {
            "synsets": (100_000, 130_000), "entries": (120_000, 160_000),
            "senses": (170_000, 220_000), "examples": (40_000, 60_000),
            "forms": (3_000, 8_000), "pronunciations": (30_000, 60_000),
            "sense_relations": (100_000, 150_000), "synset_relations": (200_000, 280_000),
        }
        for table, (lo, hi) in ranges.items():
            n = conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertTrue(lo <= n <= hi, f"{table}: {n}")
        pos = dict(conn().execute("SELECT pos, COUNT(*) FROM synsets GROUP BY pos"))
        self.assertEqual(set(pos), {"n", "v", "a", "s", "r"})
        self.assertGreater(pos["n"], 60_000)

    def test_every_row_carries_its_source(self):
        for table in ("synsets", "examples", "entries", "forms", "pronunciations", "senses",
                      "sense_relations", "synset_relations"):
            bad = conn().execute(
                f"SELECT COUNT(*) FROM {table} WHERE source IS NOT 'oewn2025'").fetchone()[0]
            self.assertEqual(bad, 0, table)

    def test_no_dangling_references(self):
        self.assertEqual(set(oewn.integrity(conn()).values()), {0})

    def test_every_synset_has_a_definition(self):
        n = conn().execute("SELECT COUNT(*) FROM synsets WHERE definition = ''").fetchone()[0]
        self.assertEqual(n, 0)

    def test_file_never_mentions_gutenberg(self):
        self.assertNotIn(b"gutenberg", Path(_STATE["path"]).read_bytes().lower())

    def test_lemma_lookups_use_indexes(self):
        plan = " ".join(r[-1] for r in conn().execute(
            "EXPLAIN QUERY PLAN SELECT s.id FROM entries e JOIN senses s ON s.entry_id = e.id "
            "WHERE e.lemma_key = ?", ("bank",)))
        self.assertIn("entries_key", plan)
        self.assertNotIn("SCAN", plan)
        plan = " ".join(r[-1] for r in conn().execute(
            "EXPLAIN QUERY PLAN SELECT entry_id FROM forms WHERE form_key = ?", ("mice",)))
        self.assertNotIn("SCAN", plan)

    def test_lookups_are_fast(self):
        t = time.perf_counter()
        for _ in range(50):
            senses_for(conn(), "run")
            lemmatize(conn(), "running")
        self.assertLess((time.perf_counter() - t) / 50, 0.05)

    def test_opened_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            conn().execute("DELETE FROM meta")


MINI = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE LexicalResource SYSTEM "http://globalwordnet.github.io/schemas/WN-LMF-1.3.dtd">
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="oewn" label="Open English Wordnet" language="en" email="x@example.org"
           license="https://creativecommons.org/licenses/by/4.0" version="2025"
           url="https://example.org/wn">
    <LexicalEntry id="oewn-mouse-n">
      <Lemma writtenForm="mouse" partOfSpeech="n">
        <Pronunciation variety="GB">ma&#650;s</Pronunciation>
      </Lemma>
      <Form writtenForm="mice"/>
      <Sense id="oewn-mouse__1.05.00.." synset="oewn-00000002-n">
        <SenseRelation relType="other" target="oewn-rodent__1.05.00.." dc:type="agent"/>
      </Sense>
      <Sense id="oewn-mouse__1.06.00.." synset="oewn-00000003-n"/>
    </LexicalEntry>
    <LexicalEntry id="oewn-rodent-n">
      <Lemma writtenForm="rodent" partOfSpeech="n"/>
      <Sense id="oewn-rodent__1.05.00.." synset="oewn-00000002-n"/>
    </LexicalEntry>
    <LexicalEntry id="oewn-hot-a">
      <Lemma writtenForm="hot" partOfSpeech="a"/>
      <Sense id="oewn-hot__3.00.00.." synset="oewn-00000004-a">
        <SenseRelation relType="antonym" target="oewn-cold__3.00.00.."/>
      </Sense>
    </LexicalEntry>
    <LexicalEntry id="oewn-cold-a">
      <Lemma writtenForm="cold" partOfSpeech="a"/>
      <Sense id="oewn-cold__3.00.00.." synset="oewn-00000005-a">
        <SenseRelation relType="antonym" target="oewn-hot__3.00.00.."/>
      </Sense>
    </LexicalEntry>
    <Synset id="oewn-00000002-n" ili="i1" members="oewn-rodent-n oewn-mouse-n"
            partOfSpeech="n" lexfile="noun.animal" dc:source="Test Source">
      <Definition>a small furry animal</Definition>
      <Definition>a small rodent</Definition>
      <SynsetRelation relType="hypernym" target="oewn-00000003-n"/>
      <Example>the mouse ate the cheese</Example>
      <Example>Gutenberg's mouse was famous</Example>
      <Example dc:source="A. Writer">a mouse in the house</Example>
    </Synset>
    <Synset id="oewn-00000003-n" ili="in" members="oewn-mouse-n" partOfSpeech="n"
            lexfile="noun.artifact">
      <Definition>a hand-operated pointing device</Definition>
      <ILIDefinition>a hand-operated pointing device</ILIDefinition>
      <SynsetRelation relType="hyponym" target="oewn-00000002-n"/>
    </Synset>
    <Synset id="oewn-00000004-a" ili="i2" members="oewn-hot-a" partOfSpeech="a" lexfile="adj.all">
      <Definition>of high temperature</Definition>
    </Synset>
    <Synset id="oewn-00000005-a" ili="i3" members="oewn-cold-a" partOfSpeech="a" lexfile="adj.all">
      <Definition>of low temperature</Definition>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


class MiniBuildTest(unittest.TestCase):
    """Parser behaviour on a hand-written WN-LMF file."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="oewn-mini-"))
        xml = cls.tmp / "mini.xml.gz"
        with gzip.open(xml, "wt", encoding="utf-8") as f:
            f.write(MINI)
        cls.report = oewn.build(xml, cls.tmp / "mini.sqlite")
        cls.conn = open_db(cls.tmp / "mini.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_counts_and_report(self):
        c = self.report["counts"]
        self.assertEqual((c["entries"], c["senses"], c["synsets"], c["forms"]), (4, 5, 4, 1))
        self.assertEqual(self.report["stats"]["examples_dropped_gutenberg"], 1)
        self.assertEqual(self.report["stats"]["synsets_with_several_definitions"], 1)
        self.assertEqual(set(self.report["integrity"].values()), {0})
        self.assertFalse((self.tmp / "mini.sqlite.tmp").exists())

    def test_definitions_joined_and_examples_filtered(self):
        s = synset(self.conn, "oewn-00000002-n")
        self.assertEqual(s["definition"], "a small furry animal; a small rodent")
        self.assertEqual(s["examples"], ["the mouse ate the cheese", "a mouse in the house"])
        row = self.conn.execute(
            "SELECT seq, origin FROM examples WHERE text = 'a mouse in the house'").fetchone()
        self.assertEqual(tuple(row), (2, "A. Writer"))
        self.assertEqual(self.conn.execute(
            "SELECT origin FROM synsets WHERE id = 'oewn-00000002-n'").fetchone()[0], "Test Source")

    def test_new_ili_stored_as_null(self):
        self.assertIsNone(synset(self.conn, "oewn-00000003-n")["ili"])
        self.assertEqual(synset(self.conn, "oewn-00000002-n")["ili"], "i1")

    def test_rank_and_member_order(self):
        self.assertEqual([(s["rank"], s["synset_id"]) for s in senses_for(self.conn, "mouse")],
                         [(1, "oewn-00000002-n"), (2, "oewn-00000003-n")])
        self.assertEqual(members(self.conn, "oewn-00000002-n"), ["rodent", "mouse"])
        self.assertEqual(senses_for(self.conn, "mouse")[0]["synonyms"], ["rodent"])

    def test_forms_relations_and_pronunciation(self):
        self.assertEqual(lemmatize(self.conn, "mice"), [("mouse", "n")])
        self.assertEqual([a["lemma"] for a in antonyms(self.conn, "oewn-hot__3.00.00..")], ["cold"])
        self.assertEqual([a["lemma"] for a in antonyms(self.conn, "oewn-00000005-a")], ["hot"])
        self.assertEqual([r["synset_id"] for r in related(self.conn, "oewn-00000002-n", "hypernym")],
                         ["oewn-00000003-n"])
        self.assertEqual(tuple(self.conn.execute(
            "SELECT rel, subtype FROM sense_relations WHERE src_sense = 'oewn-mouse__1.05.00..'"
        ).fetchone()), ("other", "agent"))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT variety, ipa FROM pronunciations").fetchone()), ("GB", "maʊs"))

    def test_meta(self):
        m = dict(self.conn.execute("SELECT key, value FROM meta"))
        self.assertIn("CC BY 4.0", m["licence"])
        self.assertEqual(m["version"], "2025")

    def test_forbidden_word_in_a_definition_stops_the_build(self):
        bad = self.tmp / "bad.xml.gz"
        with gzip.open(bad, "wt", encoding="utf-8") as f:
            f.write(MINI.replace("of low temperature", "as cold as Gutenberg's cellar"))
        with self.assertRaises(oewn.GutenbergMention):
            oewn.build(bad, self.tmp / "bad.sqlite")
        self.assertFalse((self.tmp / "bad.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
