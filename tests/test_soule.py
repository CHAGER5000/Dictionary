"""Tests for pipeline.soule (Soule, A Dictionary of English Synonymes, 1871).

The "real entry" checks were verified by eye against sources/pg38390.txt.
Run from the project root: python3 -m unittest discover -s tests -v
"""
import json
import re
import unittest
from pathlib import Path

from pipeline import soule
from pipeline.pgtext import book_text, mentions_gutenberg

ROOT = Path(__file__).resolve().parent.parent
_CACHE = {}


def report():
    if "report" not in _CACHE:
        _CACHE["report"] = soule.parse()
    return _CACHE["report"]


def records():
    return report().records


def find(headword, pos_raw=None):
    """All records for a headword (optionally with one printed tag)."""
    out = [r for r in records() if r["headword"] == headword
           and (pos_raw is None or r["pos_raw"] == pos_raw)]
    if not out:
        raise AssertionError(f"no record for {headword!r} {pos_raw!r}")
    return out


def one(headword, pos_raw=None):
    out = find(headword, pos_raw)
    if len(out) != 1:
        raise AssertionError(f"{len(out)} records for {headword!r} {pos_raw!r}")
    return out[0]


class RealEntries(unittest.TestCase):
    """Exact checks against entries read in the book."""

    def test_affection_four_senses_med_label(self):
        # ~Affection~, _n._ ~1.~ Feeling, ... ~4.~ (_Med._) Disorder, malady, disease.
        r = one("affection")
        self.assertEqual((r["pos"], r["pos_raw"], r["source"], r["line"]), ("n", "n.", "soule1871", 1967))
        self.assertEqual([s["n"] for s in r["senses"]], [1, 2, 3, 4])
        self.assertEqual(r["senses"][0]["synonyms"],
                         ["feeling", "passion", "inclination", "propensity", "bent", "bias",
                          "turn of mind", "cast or frame of mind"])
        self.assertEqual(r["senses"][1]["synonyms"],
                         ["attribute", "quality", "property", "accident", "modification", "mode"])
        self.assertEqual(r["senses"][3]["synonyms"], ["disorder", "malady", "disease"])
        self.assertEqual(r["senses"][3]["labels"], ["Med."])
        self.assertEqual(r["senses"][0]["labels"], [])

    def test_abandon_capitals_are_xrefs(self):
        # ~2.~ Surrender, cede, ..., vacate, ABDICATE, deliver up, ...
        r = one("abandon")
        self.assertEqual((r["pos"], r["pos_raw"], r["line"]), ("v", "v. a.", 218))
        self.assertEqual(len(r["senses"]), 2)
        s1, s2 = r["senses"]
        self.assertEqual(s1["synonyms"][0], "leave")
        self.assertEqual(s1["synonyms"][-1], "withdraw from")
        self.assertEqual(len(s1["synonyms"]), 13)
        self.assertIn("abdicate", s2["synonyms"])
        self.assertEqual(s2["xrefs"], ["abdicate"])
        self.assertEqual(s1["xrefs"], [])

    def test_abaft_two_parts_of_speech_are_two_records(self):
        prep, adv = one("abaft", "prep."), one("abaft", "ad.")
        self.assertEqual(prep["pos"], "prep")
        self.assertEqual(prep["senses"], [{"n": 1, "synonyms": ["behind", "back of", "in the rear of"],
                                           "xrefs": [], "labels": ["Naut."], "notes": [],
                                           "review": []}])
        self.assertEqual(adv["pos"], "adv")
        self.assertEqual(adv["senses"][0]["synonyms"],
                         ["aft", "behind", "astern", "rearward", "back", "in the rear"])

    def test_abjuration_italic_gloss_is_a_note(self):
        # ~Abjuration~, ~1.~ Renunciation (_upon oath_), relinquishment, ...  (no pos printed)
        r = one("abjuration")
        self.assertIsNone(r["pos"])
        self.assertIsNone(r["pos_raw"])
        s1 = r["senses"][0]
        self.assertEqual(s1["synonyms"],
                         ["renunciation", "relinquishment", "rejection", "abandonment", "abnegation"])
        self.assertEqual(s1["notes"], ["upon oath"])
        self.assertEqual(r["senses"][1]["synonyms"][0], "recantation")

    def test_youngster_colloquial_and_hyphen_across_lines(self):
        # ~Youngster~, _n._ [_Colloquial._] Youth, boy, lad, stripling, school-\nboy, ...
        r = one("youngster")
        s = r["senses"][0]
        self.assertEqual(s["labels"], ["Colloquial."])
        self.assertEqual(s["synonyms"], ["youth", "boy", "lad", "stripling", "school-boy",
                                         "younker", "yonker", "young man"])

    def test_zephyr_poetical_sense(self):
        # ~2.~ [_Poetical._] Gentle, mild, or soft breeze; light wind.
        r = one("zephyr")
        self.assertEqual(r["senses"][0]["synonyms"], ["west wind"])
        self.assertEqual(r["senses"][1]["labels"], ["Poetical."])
        # "gentle" and "mild" share "breeze": the run is kept aside, not split
        self.assertEqual(r["senses"][1]["synonyms"], ["light wind"])
        self.assertEqual(r["senses"][1]["review"], ["Gentle, mild, or soft breeze"])

    def test_shared_words_are_given_back(self):
        # "... prudent, not rash, heedless, or headlong." (heedless is not a synonym!)
        s = one("circumspect")["senses"][0]["synonyms"]
        self.assertEqual(s[-3:], ["not rash", "not heedless", "not headlong"])
        self.assertNotIn("heedless", s)
        self.assertEqual(find("abate")[1]["senses"][1]["synonyms"],
                         ["be defeated", "be frustrated", "be overthrown"])
        # "Orchestral introduction to an opera, oratorio, &c."
        self.assertEqual(one("overture")["senses"][1]["synonyms"],
                         ["orchestral introduction to an opera",
                          "orchestral introduction to an oratorio"])
        # "And others, and so forth, and so on." keeps its "and"
        self.assertEqual(one("et cætera")["senses"][0]["synonyms"],
                         ["and others", "and so forth", "and so on"])

    def test_headword_case_is_decided_for_the_whole_headword(self):
        for hw in ("Black Sea", "Milky Way", "Davy Jones", "Little Bear", "stick sulphur",
                   "billing and cooing", "prismatic spectrum", "put a flea in one's ear",
                   "house of God", "take French leave", "the Almighty"):
            self.assertTrue(find(hw), hw)
        self.assertFalse([r for r in records() if r["headword"] in ("black Sea", "stick Sulphur")])

    def test_capitals_in_glosses_are_xrefs(self):
        # ~Horse~ ... PONY, SHELTIE (or SHELTY), PALFREY ...; ~Layer~ ... Stratum, bed, _LAY_.
        self.assertIn("shelty", one("horse")["senses"][0]["xrefs"])
        self.assertIn("lay", one("layer")["senses"][0]["xrefs"])

    def test_hypochondria_bold_and_capitals(self):
        # (_Med._) MELANCHOLY, spleen, ..., ~hypochondriasis~, low spirits.
        s = one("hypochondria")["senses"][0]
        self.assertEqual(s["labels"], ["Med."])
        self.assertEqual(s["xrefs"], ["melancholy"])
        self.assertEqual(s["synonyms"], ["melancholy", "spleen", "vapors", "depression",
                                         "dejection", "hypochondriasis", "low spirits"])

    def test_flank_misset_sense_number(self):
        # "3~.~ Border upon, stand at the side of." is printed with the tilde misplaced.
        r = one("flank", "v. a.")
        self.assertEqual([s["n"] for s in r["senses"]], [1, 2, 3])
        self.assertEqual(r["senses"][0]["labels"], ["Mil."])
        self.assertEqual(r["senses"][2]["synonyms"], ["border upon", "stand at the side of"])

    def test_pave_the_way_glued_to_pause(self):
        # "2.~ Hesitate, demur, deliberate, waver.~~Pave the way~, Prepare, ..."
        pause = one("pause", "v. n.")
        self.assertEqual(pause["senses"][1]["synonyms"], ["hesitate", "demur", "deliberate", "waver"])
        pave = one("pave the way")
        self.assertEqual((pave["pos"], pave["line"]), (None, 51203))
        self.assertEqual(pave["senses"][0]["synonyms"],
                         ["prepare", "make ready", "get ready", "make preparation", "smooth the way"])

    def test_run_in_headword_revive(self):
        # "~Revival~, _n._ ... quickening. ~Revive~, _v. a._ ~1.~ Resuscitate, ..."
        self.assertEqual(one("revival")["senses"][0]["synonyms"][-1], "quickening")
        r = one("revive", "v. a.")
        self.assertEqual(r["line"], 59526)
        self.assertEqual(r["senses"][0]["synonyms"],
                         ["resuscitate", "reanimate", "revivify", "bring to life again"])
        self.assertEqual(len(r["senses"]), 2)

    def test_heathen_noun_and_adjective(self):
        rs = find("heathen", "n. & a.")
        self.assertEqual(sorted(r["pos"] for r in rs), ["adj", "n"])
        for r in rs:
            self.assertEqual(r["senses"][0]["synonyms"], ["gentile", "pagan", "heathenish"])

    def test_blow_up_active_and_neuter(self):
        active, neuter = one("blow up", "Active."), one("blow up", "Neuter.")
        self.assertEqual((active["pos"], neuter["pos"]), ("v", "v"))
        self.assertEqual(len(active["senses"]), 4)
        self.assertEqual(active["senses"][3]["labels"], ["Low."])
        self.assertEqual(neuter["senses"][0]["synonyms"],
                         ["explode", "burst", "be scattered by explosion"])

    def test_at_a_stand_multiword_xref(self):
        s2 = one("at a stand")["senses"][1]
        self.assertEqual(s2["synonyms"], ["perplexed", "embarrassed", "at a loss"])
        self.assertEqual(s2["xrefs"], ["at a loss"])

    def test_greek_keeps_capital(self):
        adj, noun = one("Greek", "a."), one("Greek", "n.")
        self.assertEqual(adj["senses"][0]["synonyms"], ["of Greece"])
        self.assertEqual(noun["senses"][1]["synonyms"], ["Greek language"])

    def test_curve_misprinted_tag_is_verb(self):
        r = one("curve", "n. a.")
        self.assertEqual(r["pos"], "v")
        self.assertEqual(r["senses"][0]["synonyms"], ["bend", "crook", "inflect"])

    def test_space_synonyms_misset_in_italic(self):
        self.assertEqual(one("space")["senses"][1]["synonyms"], ["capacity", "room"])

    def test_language_label_before_pos(self):
        # ~À la mode~, [Fr.] _a._ Fashionable, ...
        r = one("à la mode", "a.")
        self.assertEqual(r["pos"], "adj")
        self.assertEqual(r["senses"][0]["labels"], ["Fr."])

    def test_wit_repeated_sense_number(self):
        self.assertEqual([s["n"] for s in one("wit")["senses"]], [1, 2, 3, 4])

    def test_children_plural_pointer_is_not_an_entry(self):
        self.assertFalse([r for r in records() if r["headword"] == "children"])


class WholeBook(unittest.TestCase):
    def test_counts_in_sane_range(self):
        rep = report()
        self.assertTrue(23000 <= rep.entries_printed <= 24500, rep.entries_printed)
        self.assertTrue(23000 <= len(records()) <= 24500, len(records()))
        n_senses = sum(len(r["senses"]) for r in records())
        n_syn = sum(len(s["synonyms"]) for r in records() for s in r["senses"])
        self.assertTrue(33000 <= n_senses <= 38000, n_senses)
        self.assertTrue(125000 <= n_syn <= 150000, n_syn)

    def test_few_failures(self):
        self.assertLessEqual(report().failed_paragraphs, 10)

    def test_front_and_end_matter_skipped(self):
        lines = book_text(soule.BOOK).split("\n")
        first, last = records()[0], records()[-1]
        self.assertEqual((first["headword"], last["headword"]), ("aback", "zymotic"))
        # Title page, transcriber's note, preface and explanatory table come first.
        self.assertTrue(any(l.startswith("EXPLANATORY TABLE") for l in lines[:first["line"]]))
        self.assertTrue(all(r["line"] >= first["line"] for r in records()))
        # Only the printer's line follows the last entry.
        self.assertIn("Press of John Wilson", "\n".join(lines[last["line"]:]))
        blob = json.dumps(records(), ensure_ascii=False)
        for phrase in ("Transcriber", "Press of John Wilson", "Entered according to Act"):
            self.assertNotIn(phrase, blob)

    def test_no_markup_or_empty_synonyms(self):
        for r in records():
            self.assertTrue(r["headword"] and not re.search(r"[_~]", r["headword"]), r)
            self.assertTrue(r["senses"], r)
            for s in r["senses"]:
                self.assertTrue(s["synonyms"], r)
                for x in s["synonyms"]:
                    self.assertTrue(x.strip(), r)
                    self.assertEqual(x, x.strip(), r)
                    self.assertNotIn("_", x, r)
                    self.assertNotIn("~", x, r)
                    self.assertFalse(re.search(r"[()\[\]]", x), (r["line"], x))
                    self.assertNotIn("  ", x, r)
                for field in ("labels", "notes", "xrefs"):
                    for x in s[field]:
                        self.assertTrue(x and not re.search(r"[_~]", x), (r["line"], field, x))

    def test_labels_never_synonyms(self):
        for r in records():
            for s in r["senses"]:
                for lab in s["labels"]:
                    self.assertTrue(lab.endswith("."), lab)
                    self.assertNotIn(lab, s["synonyms"])

    def test_xrefs_are_lowercase_synonyms(self):
        for r in records():
            for s in r["senses"]:
                for x in s["xrefs"]:
                    self.assertEqual(x, x.lower())
                    self.assertTrue(any(x in syn for syn in s["synonyms"])
                                    or any(x in t.lower() for t in s["notes"] + s["review"]),
                                    (r["line"], x))

    def test_record_shape_and_provenance(self):
        lines = book_text(soule.BOOK).split("\n")
        for r in records():
            self.assertEqual(set(r), {"headword", "pos", "pos_raw", "senses", "source", "line"})
            self.assertEqual(r["source"], "soule1871")
            self.assertIn(r["pos"], soule.POS_VALUES + (None,))
            if r["pos_raw"] is not None:
                self.assertRegex(r["pos_raw"], r"^[A-Za-z]([A-Za-z.,& ]*[a-z])?\.$")
                self.assertNotRegex(r["pos_raw"], r"\.\s*\.|,\.")
            self.assertIsInstance(r["line"], int)
            # The line holds the headword in ~bold~ markup.
            self.assertIn("~", lines[r["line"] - 1], r)
            ns = [s["n"] for s in r["senses"]]
            self.assertEqual(ns, sorted(set(ns)), r)
            for s in r["senses"]:
                self.assertEqual(set(s), {"n", "synonyms", "xrefs", "labels", "notes", "review"})
                self.assertIsInstance(s["n"], int)

    def test_output_never_mentions_gutenberg(self):
        blob = "\n".join(json.dumps(r, ensure_ascii=False) for r in records())
        self.assertFalse(mentions_gutenberg(blob))


class OutputFile(unittest.TestCase):
    path = ROOT / "data" / "soule.jsonl"

    def setUp(self):
        if not self.path.exists():
            self.skipTest("data/soule.jsonl not generated yet (python3 -m pipeline.soule)")

    def test_file_matches_parser_and_is_clean(self):
        text = self.path.read_text(encoding="utf-8")
        self.assertFalse(mentions_gutenberg(text))
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(rows, records())


class Units(unittest.TestCase):
    def test_paragraphs_join_hyphen_without_space(self):
        paras = list(soule.paragraphs("~A~, _n._ school-\nboy, big\ncat.\n\n~2.~ Dog.\n"))
        self.assertEqual([(p[0], p[1]) for p in paras],
                         [(1, "~A~, _n._ school-boy, big cat."), (5, "~2.~ Dog.")])

    def test_sense_text_labels_notes_xrefs(self):
        s = soule.parse_sense_text(2, "(_Med._) Deed (_viewed as one act_), ACTION, "
                                      "[_Colloquial, U. S._] go-ahead; AT A LOSS.")
        self.assertEqual(s.synonyms, ["Deed", "action", "go-ahead", "at a loss"])
        self.assertEqual(s.xrefs, ["action", "at a loss"])
        self.assertEqual(s.labels, ["Med.", "Colloquial.", "U. S."])
        self.assertEqual(s.notes, ["viewed as one act"])

    def test_sense_text_drops_etc_and_leading_or(self):
        s = soule.parse_sense_text(1, "Color, hue, or tint; &c.")
        self.assertEqual(s.synonyms, ["Color", "hue", "tint"])
        s = soule.parse_sense_text(1, "Gentle, mild, or soft breeze; light wind.")
        self.assertEqual((s.synonyms, s.review), (["light wind"], ["Gentle, mild, or soft breeze"]))

    def test_expand_shorthand(self):
        ex = soule.expand_shorthand
        self.assertEqual(ex(["Not acute", "shrill", "or sharp", "grave"])[0],
                         ["Not acute", "Not shrill", "Not sharp", "grave"])
        self.assertEqual(ex(["Shed feathers", "hair", "&c"])[0], ["Shed feathers", "Shed hair"])
        self.assertEqual(ex(["Mark with a scratch", "or with scratches"])[:2],
                         (["Mark with a scratch"], ["or with scratches"]))
        self.assertEqual(ex(["in all lands", "here", "there", "and everywhere"], "Everywhere")[:2],
                         (["in all lands"], ["here, there, and everywhere"]))

    def test_header_variants(self):
        h = soule.parse_header("~Agreeable to, 1.~ Conformable to.")
        self.assertEqual((h.headword, h.pos_raw, h.first_n), ("Agreeable to", None, 1))
        h = soule.parse_header("~Vest~, n_._ ~1.~ Vesture.")
        self.assertEqual((h.headword, h.pos_raw), ("Vest", "n."))
        h = soule.parse_header("~Else~, _a._ or _pron._ Other.")
        self.assertEqual(h.pos_raw, "a. or pron.")
        h = soule.parse_header("~Nem. con.~ [L.] Unanimously.")
        self.assertEqual((h.headword, h.labels), ("Nem. con.", ["L."]))
        h = soule.parse_header("~Blow up~, [_Neuter._] Explode.")
        self.assertEqual((h.pos_raw, h.voice), (None, "Neuter."))
        h = soule.parse_header("~Move~, _v. a._. ~1.~ Impel.")
        self.assertEqual((h.pos_raw, h.first_n), ("v. a.", None))
        h = soule.parse_header("~Awake~, _v. a._ & _n._ AWAKEN.")
        self.assertEqual((h.pos_raw, h.body), ("v. a. & n.", "AWAKEN."))

    def test_normalise_pos(self):
        cases = {"n.": ["n"], "a.": ["adj"], "v. a.": ["v"], "v. n.": ["v"], "ad.": ["adv"],
                 "n. pl.": ["n"], "v. a. & n.": ["v"], "n. & a.": ["n", "adj"],
                 "n. & v. n.": ["n", "v"], "p. a.": ["adj"], "pron., sing. & pl.": ["pron"],
                 "conj. & ad.": ["conj", "adv"], "interj.": ["interj"], "prep.": ["prep"],
                 "n. a.": ["v"], None: []}
        for raw, want in cases.items():
            self.assertEqual(soule.normalise_pos(raw), want, raw)

    def test_classify_group(self):
        self.assertEqual(soule.classify_group("_Colloquial U. S._"), (["Colloquial.", "U. S."], []))
        self.assertEqual(soule.classify_group("_Bot._ and _Zoöl._"), (["Bot.", "Zoöl."], []))
        self.assertEqual(soule.classify_group("_Viverra zorilla_"), ([], ["Viverra zorilla"]))
        self.assertEqual(soule.classify_group("Written also _Mold_."), ([], ["Written also Mold."]))


if __name__ == "__main__":
    unittest.main()
