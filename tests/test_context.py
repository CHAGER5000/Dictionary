"""Tests for pipeline.context (the sense picker) and pipeline.evaluate.

The sentences here are not in tests/data/wsd_eval.jsonl. They read
data/english.sqlite, or a temporary copy built by tests/dbfixture.py.
Run from the project root: python3 -m unittest discover -s tests -v
"""
import statistics
import time
import unittest

try:
    from dbfixture import english_paths          # python3 -m unittest discover -s tests
except ImportError:
    from tests.dbfixture import english_paths    # python3 -m unittest tests.test_...
from pipeline import context, evaluate
from pipeline.context import Lexicon, find_target, pick_sense, pos_cues, tokenise

RIVER_BANK = "oewn-09236472-n"        # sloping land (especially the slope beside a body of water)
MONEY_BANK = "oewn-08437235-n"        # a financial institution that accepts deposits ...
BRIGHT_LIGHT = "oewn-00279417-a"      # emitting or reflecting light readily ...
BRIGHT_SMART = "oewn-01338411-s"      # characterized by quickness and ease in learning

_STATE = {}


def setUpModule():
    _STATE["conn"] = context.open_english(english_paths()[0])


def tearDownModule():
    if "conn" in _STATE:
        _STATE["conn"].close()


def conn():
    return _STATE["conn"]


def top(sentence, target):
    return pick_sense(conn(), sentence, target)[0]


class BankTest(unittest.TestCase):
    def test_boat_sentence_picks_the_river_bank_with_its_clues(self):
        best = top("The little boat drifted slowly towards the bank, where the ducks were "
                   "waiting.", "bank")
        self.assertEqual(best["synset_id"], RIVER_BANK)
        self.assertIn("slope beside a body of water", best["definition"])
        for clue in ("boat", "drifted"):
            self.assertIn(clue, best["clues"])

    def test_paying_money_in_picks_the_financial_bank(self):
        best = top("I paid the money into the bank.", "bank")
        self.assertEqual(best["synset_id"], MONEY_BANK)
        self.assertIn("money", best["clues"])

    def test_sat_on_the_bank(self):
        self.assertEqual(top("We sat on the bank and watched the ducks.", "bank")["synset_id"],
                         RIVER_BANK)


class BrightTest(unittest.TestCase):
    def test_bright_light(self):
        best = top("The bright light hurt my eyes.", "bright")
        self.assertEqual(best["synset_id"], BRIGHT_LIGHT)
        self.assertIn("light", best["clues"])

    def test_bright_pupil(self):
        best = top("She is a bright pupil who learns quickly.", "bright")
        self.assertEqual(best["synset_id"], BRIGHT_SMART)
        self.assertIn("learns", best["clues"])


class ShapeTest(unittest.TestCase):
    def test_result_shape_and_order(self):
        sentence = "Two ducks swam across the pond."
        ranked = pick_sense(conn(), sentence, "ducks")
        self.assertGreater(len(ranked), 3)
        scores = [r["score"] for r in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))
        words = {t for t, _, _ in tokenise(sentence)}
        for r in ranked:
            self.assertEqual(set(r) >= {"synset_id", "definition", "score", "clues", "lemma",
                                        "pos", "rank"}, True)
            self.assertIn(r["pos"], ("n", "v", "a", "r"))
            self.assertTrue(set(r["clues"]) <= words, r["clues"])
            self.assertNotIn("ducks", r["clues"])
        self.assertIn("swimming bird", ranked[0]["definition"])     # inflected target
        self.assertEqual(ranked[0]["lemma"], "duck")

    def test_every_candidate_is_a_sense_of_the_word(self):
        # "rose" is the noun/adjective rose or the past tense of the verb rise
        ranked = pick_sense(conn(), "The sun rose over the hills.", "rose")
        self.assertEqual({(r["lemma"], r["pos"]) for r in ranked},
                         {("rose", "n"), ("rose", "a"), ("rise", "v")})
        n = conn().execute(
            "SELECT COUNT(DISTINCT s.synset_id) FROM entries e JOIN senses s ON s.entry_id = e.id "
            "WHERE (e.lemma_key = 'rose' OR (e.lemma_key = 'rise' AND e.pos = 'v')) "
            "AND s.synset_id NOT IN (SELECT key FROM kid_filter WHERE kind = 'synset')"
        ).fetchone()[0]
        self.assertEqual(len(ranked), n)

    def test_explain_gives_links_for_every_clue(self):
        best = pick_sense(conn(), "I paid the money into the bank.", "bank", explain=True)[0]
        linked = {l["clue"] for l in best["links"]}
        self.assertTrue(set(best["clues"]) <= linked)
        self.assertEqual(set(best["parts"]), {"clues", "rank_prior", "pos_bonus", "collocation"})

    def test_unknown_word(self):
        self.assertEqual(pick_sense(conn(), "The zzzqx sat there.", "zzzqx"), [])

    def test_names_are_not_clues(self):
        best = top("Nina drifted towards the bank.", "bank")
        self.assertNotIn("Nina", best["clues"])

    def test_unfit_meanings_are_never_offered(self):
        bad = {r[0] for r in conn().execute("SELECT key FROM kid_filter WHERE kind = 'synset'")}
        for word in ("pig", "cow", "dog"):
            ranked = pick_sense(conn(), f"The {word} is in the field.", word)
            self.assertTrue(ranked)
            self.assertFalse({r["synset_id"] for r in ranked} & bad, word)

    def test_limit(self):
        self.assertEqual(len(pick_sense(conn(), "The bright light.", "bright", limit=2)), 2)

    def test_forms_of_the_tapped_word_are_never_clues(self):
        for sentence, target, form in (
                ("The trained dog watched the train.", "train", "trained"),
                ("She played the piano while we watched the play.", "play", "played"),
                ("The flying fish leapt as a fly buzzed past.", "fly", "flying")):
            ranked = pick_sense(conn(), sentence, target, explain=True)
            for r in ranked:
                self.assertNotIn(form, r["clues"], (target, r["definition"]))
                self.assertNotIn(form, [l["clue"] for l in r["links"]])
            self.assertEqual(ranked[0]["pos"], "n", (target, ranked[0]["definition"]))

    def test_the_tap_position_picks_the_occurrence(self):
        sentence = "The bat flew out of the cave as Tom swung his cricket bat."
        toks = tokenise(sentence)
        self.assertEqual(find_target(toks, "bat"), 1)
        self.assertEqual(find_target(toks, "bat", at=sentence.rindex("bat")), len(toks) - 1)
        # the words near the tap count more, so both taps may still agree; the call works
        self.assertTrue(pick_sense(conn(), sentence, "bat", at=sentence.rindex("bat")))

    def test_shown_clues_are_named_and_not_general(self):
        for sentence, target in (("The little boat drifted slowly towards the bank.", "bank"),
                                 ("I paid the money into the bank.", "bank"),
                                 ("Our old dog took his ball to the park.", "park")):
            best = pick_sense(conn(), sentence, target, explain=True)[0]
            named = {l["clue"] for l in best["links"] if l["matched"]}
            self.assertTrue(set(best["clues"]) <= named, best)
            self.assertFalse({"old", "took"} & set(best["clues"]), best["clues"])


class HelpersTest(unittest.TestCase):
    def test_tokenise(self):
        self.assertEqual([t for t, _, _ in tokenise("Grandma's five-pound note, isn't it?")],
                         ["Grandma's", "five", "pound", "note", "isn't", "it"])

    def test_find_target(self):
        toks = tokenise("Duck! That snowball is coming.")
        self.assertEqual(find_target(toks, "Duck"), 0)
        self.assertEqual(find_target(toks, "snowball"), 2)
        self.assertEqual(find_target(toks, "absent"), -1)
        # a word that only contains the target is not the target
        self.assertEqual(find_target(tokenise("Please bring the cake."), "ring"), -1)
        # a form of the same lemma is, when a lexicon is given
        toks = tokenise("The sun rose early.")
        self.assertEqual(find_target(toks, "rise", lex=Lexicon(conn())), 2)

    def test_lemma_keys(self):
        lex = Lexicon(conn())
        self.assertEqual(lex.keys("ducks"), ("duck",))
        self.assertIn("see", lex.keys("saw"))
        self.assertIn("wait", lex.keys("waiting"))
        self.assertNotIn("numb", lex.keys("number"))      # not "more numb"
        self.assertEqual(lex.keys("zzzqx"), ("zzzqx",))

    def test_part_of_speech_cues(self):
        lex = Lexicon(conn())

        def cues(sentence, target):
            toks = tokenise(sentence)
            return pos_cues(toks, find_target(toks, target), lex, sentence)

        self.assertGreater(cues("He used a key to wind the clock.", "wind").get("v", 0), 1.0)
        self.assertGreater(cues("A huge wave hit the boat.", "wave").get("n", 0), 0.5)
        self.assertGreater(cues("It isn't fair!", "fair").get("a", 0), 0.5)
        self.assertGreater(cues("Duck! It is coming.", "Duck").get("v", 0), 0.5)
        self.assertGreater(cues("The right answer is four.", "right").get("a", 0), 0.5)
        # a name before an inflected verb is a verb cue; a capitalised first word is not a name
        self.assertGreater(cues("Mia left the room early.", "left").get("v", 0), 0.5)
        self.assertEqual(cues("Walk left past the shop.", "left").get("v", 0), 0)
        # after a preposition, a determiner next does not make a verb
        self.assertEqual(cues("In winter the pond freezes.", "winter").get("v", 0), 0)
        # a determiner a few modifiers back still marks a noun
        self.assertGreater(cues("They climbed the tall old iron gate.", "gate").get("n", 0), 0.5)


class SpeedTest(unittest.TestCase):
    def test_under_50_ms_after_warm_up(self):
        context.warm_up(conn())
        sentences = [
            ("We had to run all the way to the station because the train was leaving.", "run"),
            ("Please set the table for dinner and put the big plates next to the knives.", "set"),
            ("The old man took a long walk before the storm broke over the hills.", "broke"),
            ("The light from the lighthouse swept across the dark water all night.", "light"),
        ]
        times = []
        for sentence, target in sentences:
            t0 = time.perf_counter()
            pick_sense(conn(), sentence, target)
            times.append((time.perf_counter() - t0) * 1000)
        self.assertLess(statistics.median(times), 50, times)


class EvaluationTest(unittest.TestCase):
    """Regression guard on the sense evaluation set (numbers in README.md)."""

    @classmethod
    def setUpClass(cls):
        cls.dev = evaluate.evaluate(conn(), evaluate.load_items(split="dev"))
        cls.test = evaluate.evaluate(conn(), evaluate.load_items(split="test"))

    def test_items(self):
        self.assertEqual(self.dev["items"], 36)
        self.assertEqual(self.test["items"], 80)

    def test_beats_the_first_sense_baseline(self):
        self.assertGreaterEqual(self.dev["accuracy"], 0.70)
        self.assertGreater(self.test["accuracy"], self.test["baseline_first_sense"] + 0.10)

    def test_failures_are_listed(self):
        rows = self.test["rows"]
        self.assertEqual(sum(not r["correct"] for r in rows),
                         self.test["items"] - self.test["correct"])
        for r in rows:
            self.assertIn("predicted_definition", r)


if __name__ == "__main__":
    unittest.main()
