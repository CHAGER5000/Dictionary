"""Tests for pipeline.roget (Roget's Thesaurus 1911 parser).

The exact checks below were verified by eye against the e-text (sources/pg22.txt).
"""
import json
import re
import unittest

from pipeline import roget
from pipeline.pgtext import book_text, mentions_gutenberg


def _group_with(record, word, key="words"):
    for i, g in enumerate(record["groups"]):
        if word in g[key]:
            return i, g
    raise AssertionError(f"{word!r} not in any group's {key} of head {record['head']}")


class RogetBookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.log = roget.parse()
        cls.by_head = {r["head"]: r for r in cls.records}
        cls.lines = book_text(roget.SOURCE_FILE).split("\n")

    # ---------------------------------------------------------------- counts
    def test_head_count_is_sane(self):
        # 1000 numbered heads plus 44 lettered ones (59a, 100a, 252a, ...)
        self.assertGreaterEqual(len(self.records), 1000)
        self.assertLessEqual(len(self.records), 1060)
        self.assertEqual(len(self.records), 1044)
        numbered = {r["head"] for r in self.records if isinstance(r["head"], int)}
        self.assertEqual(numbered, set(range(1, 1001)))

    def test_heads_unique_and_in_book_order(self):
        heads = [r["head"] for r in self.records]
        self.assertEqual(len(heads), len(set(heads)))
        nums = [int(re.match(r"\d+", str(h)).group(0)) for h in heads]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(heads[0], 1)
        self.assertEqual(heads[-1], 1000)

    def test_stats_sane(self):
        st = roget.stats(self.records)
        self.assertGreater(st["groups"], 35000)
        self.assertGreater(st["words"], 80000)
        self.assertGreater(st["unrecognised"], 7000)      # [obs3]
        self.assertGreater(st["obsolete"], 400)           # the book's dagger |
        self.assertGreater(st["obsolete_now"], 100)       # the e-text editor's |!
        self.assertGreater(st["xrefs"], 6000)
        self.assertGreater(st["phrases"], 1000)

    def test_few_parse_failures(self):
        self.assertLess(self.log.counts["bad_word"], 20)
        self.assertEqual(self.log.counts["text_outside_head"], 0)
        self.assertEqual(self.log.counts["head_without_dash"], 0)

    # ------------------------------------------------------- verified heads
    def test_220_exteriority(self):
        r = self.by_head[220]
        self.assertEqual(r["name"], "Exteriority")
        self.assertEqual(r["class_num"], 2)
        self.assertEqual(r["class"], "Words relating to space")
        self.assertEqual(r["section"], "Dimensions")
        self.assertEqual(r["subsections"], ["Centrical dimensions", "General"])
        first = r["groups"][0]
        self.assertEqual(first["pos"], "n")
        self.assertEqual(first["unrecognised"], ["exteriority"])     # exteriority[obs3]
        self.assertNotIn("exteriority", [w for g in r["groups"] for w in g["words"]])
        _, g = _group_with(r, "outside")
        self.assertEqual(g["words"], ["outside", "exterior"])
        _, g = _group_with(r, "skin")
        self.assertIn({"word": "skin", "head": 223}, g["xrefs"])
        _, g = _group_with(r, "circumjacence", "unrecognised")
        self.assertIn({"word": "circumjacence", "head": 227}, g["xrefs"])
        _, g = _group_with(r, "extra muros", "foreign")
        self.assertEqual(g["pos"], "adv")
        _, g = _group_with(r, "ecdemic")
        self.assertEqual(g["labels"], {"ecdemic": ["medicine"]})
        self.assertEqual(g["unrecognised"], ["exomorphic"])
        self.assertEqual(self.lines[r["line"] - 1][:16], "#220. Exteriorit")

    def test_1_existence(self):
        r = self.by_head[1]
        self.assertEqual(r["name"], "Existence")
        self.assertEqual((r["class_num"], r["section_num"], r["section"]), (1, 1, "Existence"))
        self.assertEqual(r["subsections"], ["Being, in the abstract"])
        g0 = r["groups"][0]
        self.assertEqual(g0["words"], ["existence", "being", "entity", "subsistence"])
        self.assertEqual(g0["foreign"], ["ens", "esse"])       # italic _ens_, _esse_
        _, g = _group_with(r, "truth")
        self.assertEqual(g["xrefs"], [{"word": "truth", "head": 494}])
        _, g = _group_with(r, "presence")
        self.assertEqual(g["xrefs"], [{"word": "presence", "head": 186}])
        _, g = _group_with(r, "ontology")
        self.assertEqual(g["label"], "Science of existence")
        _, g = _group_with(r, "abide")
        self.assertEqual((g["pos"], g["words"]),
                         ("v", ["abide", "continue", "endure", "last", "remain", "stay"]))
        _, g = _group_with(r, "unideal", "unrecognised")
        self.assertEqual(g["words"], ["unimagined"])
        pos_order = []
        for g in r["groups"]:
            if not pos_order or pos_order[-1] != g["pos"]:
                pos_order.append(g["pos"])
        self.assertEqual(pos_order, ["n", "v", "adj", "adv"])
        self.assertIn("thinkest thou existence doth depend on time?", r["phrases"])
        self.assertIn("ens rationis", r["foreign_phrases"])

    def test_2_inexistence(self):
        r = self.by_head[2]
        self.assertEqual(r["name"], "Inexistence")
        self.assertEqual(r["groups"][0]["unrecognised"], ["inexistence"])
        self.assertEqual(r["groups"][1]["words"], ["nonexistence", "nonsubsistence"])
        _, g = _group_with(r, "tabula rasa", "foreign")
        self.assertEqual(g["words"], ["blank"])
        _, g = _group_with(r, "fabulous")
        self.assertEqual(g["xrefs"], [{"word": "ideal", "head": 515},
                                      {"word": "supposititious", "head": 514}])
        self.assertEqual(r["foreign_phrases"], ["non ens"])

    def test_252a_sponge_head_without_hash(self):
        r = self.by_head["252a"]
        self.assertEqual(r["name"], "Sponge")
        self.assertEqual(r["groups"][0]["words"], ["sponge", "honeycomb", "network"])
        _, g = _group_with(r, "frit")
        self.assertEqual(g["labels"], {"frit": ["chemistry"]})
        self.assertEqual(r["section"], "Form")

    def test_100a_lettered_head(self):
        r = self.by_head["100a"]
        self.assertIsInstance(r["head"], str)
        self.assertEqual(r["name"], "Fraction")
        self.assertEqual(r["name_notes"], ["Less than one"])
        self.assertEqual(r["groups"][0]["words"], ["fraction", "fractional part"])
        self.assertEqual(r["groups"][1]["xrefs"], [{"word": "part", "head": 51}])
        self.assertEqual(r["groups"][2]["pos"], "adj")

    def test_454_topic_header_with_unclosed_bracket(self):
        r = self.by_head[454]
        self.assertEqual(r["name"], "Topic")
        _, g = _group_with(r, "subject matter")
        self.assertEqual(g["words"], ["subject", "subject matter"])

    def test_587_response_is_a_pointer(self):
        r = self.by_head[587]
        self.assertEqual(r["name"], "Response")
        self.assertEqual(len(r["groups"]), 1)
        self.assertEqual(r["groups"][0]["words"], ["answer"])
        self.assertEqual(r["groups"][0]["xrefs"], [{"word": "answer", "head": 462}])

    def test_737b_politics_continuation_line_starting_with_hash(self):
        r = self.by_head["737b"]
        self.assertEqual(r["name"], "Politics")
        self.assertEqual(r["name_notes"], ["contention for governmental authority or influence"])
        self.assertEqual(r["groups"][0]["words"], ["politics"])

    def test_316_capitalised_name_and_388a_antonym(self):
        self.assertEqual(self.by_head[316]["name"], "Materiality")
        self.assertEqual(self.by_head[316]["section"], "Matter in general")
        self.assertEqual(self.by_head["388a"]["antonym_heads"], [388])
        self.assertEqual(self.by_head[135]["antonym_heads"], [134])

    def test_1000_temple(self):
        r = self.by_head[1000]
        self.assertEqual(r["name"], "Temple")
        self.assertEqual((r["class_num"], r["section"]), (6, "Religious affections"))
        self.assertEqual(r["groups"][0]["words"], ["place of worship"])
        _, g = _group_with(r, "minster", "unrecognised")
        self.assertIn("cathedral", g["words"])
        _, g = _group_with(r, "chancel")
        self.assertEqual(g["label"], "parts of a church: list")
        _, g = _group_with(r, "parsonage")
        self.assertEqual(g["label"], "clergymen's residence")
        _, g = _group_with(r, "claustral")
        self.assertEqual(g["pos"], "adj")
        self.assertEqual(r["phrases"], ["there's nothing ill can dwell in such a temple"])
        self.assertEqual(r["foreign_phrases"], ["ne vile fano"])

    def test_interjection_next_to_a_quotation(self):
        # #914: '... poor fellow! woe betide! "quis talia fando ..." [Lat][Vergil].'
        ints = [w for g in self.by_head[914]["groups"] if g["pos"] == "int" for w in g["words"]]
        self.assertIn("woe betide", ints)
        self.assertIn("quis talia fando temperet a lachrymiss!", self.by_head[914]["foreign_phrases"])
        # #836: 'hurrah! &c. 838; "hence loathed melancholy!" begone dull care! ...'
        ints = [w for g in self.by_head[836]["groups"] if g["pos"] == "int" for w in g["words"]]
        self.assertIn("begone dull care", ints)
        # #576: 'call a spade "a spade"' is one phrase
        self.assertIn("call a spade a spade",
                      [w for g in self.by_head[576]["groups"] for w in g["words"]])

    def test_garbled_filler_and_continuing_filler(self):
        # #577: 'ornament; floridness c[obs3]. adj. turgidity' ("c[obs3]." is "&c.")
        _, g = _group_with(self.by_head[577], "turgidity")
        self.assertEqual(g["pos"], "n")
        # #33: 'beat &c. all others, bear the palm'
        _, g = _group_with(self.by_head[33], "bear the palm")
        self.assertEqual(g["words"], ["beat all others", "bear the palm"])

    def test_dash_slot_shorthand(self):
        # #58: 'in -turn, - its turn; step by step; by regular -steps, -gradations, ...'
        _, g = _group_with(self.by_head[58], "in turn")
        self.assertEqual(g["words"], ["in turn", "in its turn"])
        _, g = _group_with(self.by_head[58], "by regular steps")
        self.assertEqual(g["words"], ["by regular steps", "by regular gradations",
                                      "by regular stages", "by regular intervals"])

    def test_foreign_phrases_are_split_from_english(self):
        r = self.by_head[914]
        self.assertIn("onor di bocca assai giova e poco costa", r["foreign_phrases"])
        self.assertIn("a fellow feeling makes one wondrous kind", r["phrases"])
        self.assertIn("horresco referens", self.by_head[860]["foreign_phrases"])

    def test_hierarchy_division(self):
        self.assertEqual(self.by_head[450]["division"], "Formation of ideas")
        self.assertEqual(self.by_head[516]["division"], "Communication of ideas")
        self.assertIsNone(self.by_head[820]["division"])
        self.assertEqual(self.by_head[402]["subsections"],
                         ["Sensation", "Special Sensation", "Sound", "Sound in general"])

    # ---------------------------------------------------------- invariants
    def test_no_bad_words(self):
        for r in self.records:
            for g in r["groups"]:
                self.assertIn(g["pos"], {"n", "v", "adj", "adv", "int", "pron"})
                self.assertTrue(any(g[k] for k in roget.WORD_LISTS))
                for key in roget.WORD_LISTS:
                    for w in g[key]:
                        self.assertTrue(w and w == w.strip(), (r["head"], w))
                        self.assertTrue(re.search(r"[^\W\d_]", w), (r["head"], w))
                        for bad in ("[", "]", "&c", "&", "|", "{", "}", "_", "*", "\"", "#"):
                            self.assertNotIn(bad, w, (r["head"], w))
                        self.assertIsNone(re.search(r"[\x00-\x1f]", w), (r["head"], w))
                for x in g["xrefs"]:
                    self.assertIn(x["word"], [w for k in roget.WORD_LISTS for w in g[k]])
                    self.assertTrue(isinstance(x["head"], int) or re.fullmatch(r"\d+[a-d]", x["head"]))
            for p in r["phrases"] + r["foreign_phrases"]:
                self.assertTrue(p)
                self.assertNotIn("[", p)
                self.assertNotIn("&c", p)

    def test_xrefs_resolve(self):
        heads = set(self.by_head)
        xrefs = [x for r in self.records for g in r["groups"] for x in g["xrefs"]]
        unresolved = [x for x in xrefs if x["head"] not in heads]
        self.assertLessEqual(len(unresolved), 3, unresolved)

    def test_provenance(self):
        for r in self.records:
            self.assertEqual(r["source"], "roget1911")
            self.assertIsInstance(r["line"], int)
            self.assertTrue(self.lines[r["line"] - 1].lstrip().lstrip("#").startswith(str(r["head"])),
                            (r["head"], r["line"]))

    def test_never_mentions_gutenberg(self):
        text = "".join(json.dumps(r, ensure_ascii=False) for r in self.records)
        self.assertFalse(mentions_gutenberg(text))
        if roget.OUT_PATH.exists():
            self.assertFalse(mentions_gutenberg(roget.OUT_PATH.read_text(encoding="utf-8")))

    def test_index(self):
        idx = roget.index(self.records)
        self.assertIn((220, 1), idx["exterior"])
        self.assertIn((220, 3), idx["skin"])
        self.assertTrue(all(k == k.lower() for k in idx))
        self.assertNotIn("exteriority", idx)          # obsolete in 220, not a main word anywhere
        self.assertIn("exteriority", roget.index(self.records, include_obsolete=True))
        for word, places in list(idx.items())[:2000]:
            for head, gi in places:
                g = self.by_head[head]["groups"][gi]
                self.assertIn(word, [w.lower() for w in g["words"]])


class RogetGrammarTest(unittest.TestCase):
    """The head grammar on a small synthetic head."""

    SAMPLE = [
        "#999. [Something noted.] Sample.—N. alpha, beta[obs3]; gamma &c. (delta) 12;",
        "#",
        "epsilon &c. adj. [a label] zeta, eta[Lat], theta|, omicron|!; iota &c. 5, (kappa) 7.",
        "     V. do &c. n.; see &c. (look) 441 an opportunity; nu* [U.S.].",
        "     Adj. good &c. 648; _bonus_. Int. alas! hurrah! Phr. \"a saying; with a semicolon\" [Pope]; tace[It].",
    ]

    def test_sample_head(self):
        log = roget.ParseLog()
        r = roget.parse_head(self.SAMPLE, 7, log, {"class": "C", "subsections": []})
        self.assertEqual((r["head"], r["name"], r["name_notes"], r["line"]), (999, "Sample", ["Something noted"], 7))
        g = r["groups"]
        self.assertEqual((g[0]["pos"], g[0]["words"], g[0]["unrecognised"], g[0]["obsolete"]),
                         ("n", ["alpha"], ["beta"], []))
        self.assertEqual(g[1]["xrefs"], [{"word": "gamma", "head": 12}])
        self.assertEqual((g[2]["words"], g[2]["para"]), (["epsilon"], 0))
        self.assertEqual(g[3]["para"], 1)                  # "&c. adj." then a new paragraph
        self.assertEqual(g[3]["label"], "a label")
        self.assertEqual(g[3]["words"], ["zeta"])
        self.assertEqual(g[3]["foreign"], ["eta"])
        self.assertEqual(g[3]["obsolete"], ["theta"])
        self.assertEqual(g[3]["obsolete_now"], ["omicron"])
        self.assertEqual(g[4]["xrefs"], [{"word": "iota", "head": 5}, {"word": "iota", "head": 7}])
        verbs = [x for x in g if x["pos"] == "v"]
        self.assertEqual(verbs[0]["words"], ["do"])
        self.assertEqual(verbs[1]["words"], ["see an opportunity"])
        self.assertEqual(verbs[1]["xrefs"], [{"word": "see an opportunity", "head": 441}])
        self.assertEqual(verbs[2]["labels"], {"nu": ["U.S.", "slang"]})
        adjs = [x for x in g if x["pos"] == "adj"]
        self.assertEqual(adjs[0]["xrefs"], [{"word": "good", "head": 648}])
        self.assertEqual(adjs[1]["foreign"], ["bonus"])
        ints = [x for x in g if x["pos"] == "int"]
        self.assertEqual(ints[0]["words"], ["alas", "hurrah"])
        self.assertEqual(r["phrases"], ["a saying; with a semicolon"])
        self.assertEqual(r["foreign_phrases"], ["tace"])

    def test_filler_ambiguity(self):
        # "&c. Adj." is a filler when a real "Adj." marker follows later in the head
        lines = ["#998. Filler.—N. healthiness &c. Adj. fine air; tonic. Adj. healthy, well."]
        r = roget.parse_head(lines, 1, roget.ParseLog(), {})
        self.assertEqual([(g["pos"], g["words"]) for g in r["groups"]],
                         [("n", ["healthiness"]), ("n", ["fine air"]), ("n", ["tonic"]),
                          ("adj", ["healthy", "well"])])
        # ... and a marker when it is the last one
        lines = ["#997. Marker.—N. zoologist &c. Adj. zoological &c. n."]
        r = roget.parse_head(lines, 1, roget.ParseLog(), {})
        self.assertEqual([(g["pos"], g["words"]) for g in r["groups"]],
                         [("n", ["zoologist"]), ("adj", ["zoological"])])


if __name__ == "__main__":
    unittest.main()
