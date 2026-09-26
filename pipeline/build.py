"""Build the English data file for the app: data/english.sqlite.

Run from the project root (needs data/oewn.sqlite, data/soule.jsonl and
data/roget.jsonl; missing inputs are rebuilt by their own modules):

    python3 -m pipeline.build          # writes data/english.sqlite, prints a report

What goes in
------------
* Every Open English WordNet table from data/oewn.sqlite, unchanged
  (synsets, examples, entries, forms, pronunciations, senses, sense_relations,
  synset_relations). Its ``meta`` table is kept and extended: the full
  attribution and Princeton notice, and the list of changes this build makes.
* ``sense_synonyms(synset_id, word, word_key, source, score, ref)``: synonyms
  for one WordNet meaning (synset).

  - ``oewn2025``: the synset's own members, score 1.0, ref = the sense id.
  - ``soule1871``: the words of a numbered Soule sense that was aligned to this
    synset; score = the alignment score, ref = "<line>#<sense n>".
  - ``roget1911``: the words of a Roget semicolon group that was aligned to this
    synset, for the group word that has this synset, and only those WordNet
    links closely to it (see roget_keeps); score = alignment score,
    ref = "<head>:<group index>".

* ``thesaurus_alignments``: one row per old-book group that was aligned
  (source, ref, headword, pos, synset_id, score, runner-up score).
* ``gloss_keys(synset_id, def_keys, ex_keys, source)``: lemma keys of the
  content words of each definition and its examples (space separated), and
  ``lemma_idf(key, df, idf)``, both used by pipeline.context.
* ``kid_filter(kind, key, reason, source)``: meanings (kind 'synset') and words
  (kind 'word') that must not be shown to children. Source ``oewn2025`` marks
  WordNet's own labels, ``build`` this build's blocklist.

A second file, data/english_audit.sqlite, is for people checking the data and
is not shipped: ``thesaurus_unaligned`` (groups that were not aligned, as
printed, with the reason: ``below_threshold``, ``tie``, ``long_group``,
``no_wordnet_entry`` or ``unsupported_pos``) and ``thesaurus_withheld`` (Roget
words of aligned groups that were not made synonyms, and why).

How an old-book group is aligned
--------------------------------
For a group G listed under headword h with part of speech p, every WordNet
synset of h with part of speech p is a candidate. Each word of G (other than
h) scores against the candidate's neighbourhood and takes the best weight it
finds: a member of the synset (1.0), a member of a 'similar' or 'also see'
synset (0.8), a member of a hypernym (0.6), a member of a hyponym (0.4), or a
content word of the definition (0.5). A phrase that is not itself a member
scores half the best weight of its words; the headword's own words never
count ("badger dog" does not match "lap dog" through "dog"). For Roget, the
other groups of the same paragraph and the head's name add a quarter of their
score, at most 1.0, and the group's own words must score at least GROUP_MIN,
so a paragraph cannot align a group on its own. The group goes to the best
candidate if its score is at least MIN_SCORE and beats every other candidate
by MARGIN, and a candidate WordNet lists as a commoner sense also by a factor
of RATIO_UPSET (see decide()); otherwise it goes to thesaurus_unaligned. A
Roget group of LONG_GROUP or more usable words aligned to a headword with no
rival sense needs LONG_GROUP_SHARE of a point per word.

Which Roget words become synonyms
---------------------------------
Roget's groups are often lists of kinds of a thing (horses, shops, tools)
rather than synonyms, so an aligned group does not give all its words to the
synset. A word is kept if WordNet has it in the synset, in a similar synset
(or the same adjective cluster), as a direct hypernym, or, for verbs,
adjectives and adverbs, as a direct hyponym; or if WordNet does not know the
word with that part of speech at all (mostly phrases: "tongue of land"),
unless the group looks like a list. The rest go to thesaurus_withheld.
Soule's senses are lists of synonyms, and keep all their words.

By-eye checks of 50 random rows that the old books add to a synset (rows for
words WordNet does not already have there; seed 20260926): Roget 39 right, 8
partly right, 3 wrong ("gold-colored" -> "apricot-colored"); Soule 38 right,
11 partly right (near-synonyms such as "agreeable" -> "delectable"), 1 wrong
("note" -> "eye" for "make mention of"). The same check of Roget on the build
before the Roget rule: 27 right, 5 partly right, 18 wrong.

Which words are kept
--------------------
A synonym is kept if it is a WordNet lemma, or a phrase of 2-4 words that are
each WordNet lemmas (or inflections of them) or common function words
("tongue of land", "immerse one's self"). Anything else (digits, stray marks,
archaic spellings WordNet does not know) is dropped. Obsolete and foreign Roget
words are never used; words the e-text flags only as unknown to its editor's
spelling checker ([obs3]) are used when WordNet has them. For children, words
and meanings WordNet marks as disparaging, obscene or ethnic slurs, the modern
slang synsets imported from Colloquial WordNet, Roget words labelled
slang/vulgar/derogatory, Soule senses labelled vulgar/low/cant/in contempt/in
derision and every word or phrase caught by blocked() are all left out.

Nothing in the output may mention "Gutenberg"; both finished files are
checked byte for byte, as data/oewn.sqlite is.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pipeline import oewn
from pipeline.context import STOP_WORDS, Lexicon

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "english.sqlite"
OEWN_DB = DATA / "oewn.sqlite"
SOULE_JSONL = DATA / "soule.jsonl"
ROGET_JSONL = DATA / "roget.jsonl"
AUDIT = DATA / "english_audit.sqlite"

OEWN, SOULE, ROGET = "oewn2025", "soule1871", "roget1911"
BUILD_VERSION = "2"

# alignment weights (see module docstring)
W_MEMBER = 1.0
W_SIMILAR = 0.8          # 'similar' and 'also' neighbours
W_HYPERNYM = 0.6
W_HYPONYM = 0.4
W_DEFINITION = 0.5
PHRASE_FACTOR = 0.5      # a phrase that is not a member scores half its best word
CONTEXT_FACTOR = 0.25    # Roget: other groups of the paragraph, and the head name ...
CONTEXT_CAP = 1.0        # ... adding at most this much
MIN_SCORE = 1.0          # e.g. one member match, or two weaker hits (0.8 was too loose)
GROUP_MIN = 0.4          # the group itself (not just its Roget paragraph) must give this
MARGIN = 0.2             # the winner must beat the runner-up by this much, and ...
RATIO_UPSET = 1.5        # ... score this many times as much if the runner-up is a commoner sense
LONG_GROUP = 10          # Roget: a group this long with no rival sense needs ...
LONG_GROUP_SHARE = 0.3   # ... a group score of this much per usable word
# Roget: how a group word must be linked in WordNet to the synset to become its synonym
# (see WordNet.relation); other words are withheld (english_audit.sqlite).
ROGET_KEEP = {"member", "similar", "hypernym", "unknown"}
ROGET_KEEP_EXCEPT_NOUNS = {"hyponym"}      # "greet" -> "hail"; but not "light" -> "sun"
LIST_MIN_WORDS = 3       # a group is a list of kinds when this many of its WordNet words ...
LIST_SHARE = 0.5         # ... and this share of them are only loosely linked to the synset

# Soule pos -> WordNet pos; None (phrase headwords) tries every part of speech.
SOULE_POS = {"n": "n", "v": "v", "adj": "a", "adv": "r"}
ROGET_POS = {"n": "n", "v": "v", "adj": "a", "adv": "r"}

FUNCTION_WORDS = frozenset("""
a an the of to in on at by for from with without into onto out up down off over under
upon about after before through and or as one one's oneself self be is not no any some
all its it his her their them him what that this away back again
""".split())

# Usage domains that mark a WordNet meaning as unfit for children.
OFFENSIVE_DOMAINS = {"disparagement", "obscenity", "ethnic slur", "slur", "racism",
                     "street name"}
ROGET_BAD_LABELS = {"vulgar", "derogatory", "slang"}
# compared without case and without the final full stop Soule prints ("Low.", "In contempt.")
SOULE_BAD_LABELS = {"vulgar", "low", "cant", "cant term", "cant word", "in contempt",
                    "in derision"}
# A last line of defence for words the tags above miss (see blocked()).
BLOCKLIST = frozenset("""
arse arsehole asshole bastard bitch bitchy bollocks bugger bullshit cock cocksucker crap
cunt damn dick dickhead dildo dyke fag faggot fuck fucker fucking goddamn gook hooker
horny jizz kike motherfucker nigga nigger nookie paki piss pissed poof poofter
porn prick pussy queer raghead retard shit shite slag slut spastic spic tits
titty tosser twat wank wanker whore wog wop
""".split())
# These are blocked only as whole words: inside a phrase they are mostly innocent
# ("cock-a-doodle-doo", "bastard toadflax", "spotted dick", "horny layer", "basic slag").
BLOCK_WHOLE_ONLY = frozenset("""
bastard cock crap dick fag hooker horny prick queer slag spastic
""".split())
# Blocked anywhere inside a word ("fuckup", "fuck-up", "shithead", "motherfucker") ...
BLOCK_INSIDE = ("fuck", "cunt", "shit", "wank", "whore", "slut", "jizz", "dildo", "twat",
                "cocksuck", "nigger", "nigga", "bitch")
# ... except in these innocent words.
BLOCK_ALLOW = frozenset({"shittah", "shittah tree", "shittim", "shittimwood", "wankel engine",
                         "wankel rotary engine", "brood bitch"})
# Explicit phrases none of the rules above catch.
BLOCK_PHRASES = frozenset({
    "cock sucking", "cock ring", "cock up", "cockup", "cock-up", "jack off", "jerk off",
    "she-bop", "pain in the ass", "kick ass", "piece of ass", "ugly ass", "long ass",
    "bare-ass", "bare-assed", "ass-kisser", "take a crap", "crap up", "crapper", "crappy",
    "blow job", "blowjob", "hand job", "prickteaser"})


def blocked(key: str) -> bool:
    """Is this lemma key (oewn.lookup_key) a word or phrase children must not see?

    Every word of a phrase is checked, so "fuck off" and "dog shit" are caught
    as well as "fuck"; BLOCK_WHOLE_ONLY words count only on their own, and
    BLOCK_INSIDE stems count inside a word ("fuckup")."""
    key = key.replace("’", "'")
    if key in BLOCK_ALLOW:
        return False
    if key in BLOCKLIST or key in BLOCK_PHRASES:
        return True
    toks = [t for t in re.split(r"[ \-'/.]+", key) if t]
    if any(t in BLOCKLIST and t not in BLOCK_WHOLE_ONLY for t in toks):
        return True
    return any(stem in key for stem in BLOCK_INSIDE)

_WORD_OK = re.compile(r"^[^\W\d_][^\W\d_'’ .-]*(?:[ '’.-]+[^\W\d_]+)*\.?$", re.UNICODE)


# --------------------------------------------------------------------------- loading

class WordNet:
    """The parts of WordNet the build needs, held in memory."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lex = Lexicon(conn)
        self.pos = {}
        self.definition = {}
        self.origin = {}
        for sid, pos, definition, origin in conn.execute(
                "SELECT id, pos, definition, origin FROM synsets"):
            self.pos[sid] = "a" if pos == "s" else pos
            self.definition[sid] = definition
            self.origin[sid] = origin
        self.members = defaultdict(list)          # synset -> [lemma] in member order
        self.member_sense = {}                    # (synset, lemma key) -> sense id
        self.lemma_synsets = defaultdict(list)    # (lemma key, pos) -> [synset] by rank
        rows = conn.execute(
            "SELECT s.id, s.synset_id, s.lemma, e.pos, s.rank, e.id FROM senses s "
            "JOIN entries e ON e.id = s.entry_id "
            "ORDER BY s.synset_id, s.member_rank IS NULL, s.member_rank, s.lemma").fetchall()
        by_lemma = defaultdict(list)
        for sense_id, sid, lemma, pos, rank, eid in rows:
            self.members[sid].append(lemma)
            self.member_sense[(sid, oewn.lookup_key(lemma))] = sense_id
            by_lemma[(oewn.lookup_key(lemma), "a" if pos == "s" else pos)].append((eid, rank, sid))
        for k, lst in by_lemma.items():
            lst.sort()
            self.lemma_synsets[k] = list(dict.fromkeys(sid for _, _, sid in lst))
        self.lemma_keys = {k for k, _ in self.lemma_synsets}
        self.canonical = {}                       # lemma key -> display form
        for (eid, lemma) in conn.execute("SELECT id, lemma FROM entries ORDER BY id"):
            k = oewn.lookup_key(lemma)
            prev = self.canonical.get(k)
            if prev is None or (prev != k and lemma == k):
                self.canonical[k] = lemma
        self.rels = defaultdict(list)
        for src, rel, dst in conn.execute(
                "SELECT src, rel, dst FROM synset_relations "
                "WHERE rel IN ('similar', 'also', 'hypernym', 'hyponym')"):
            self.rels[(src, rel)].append(dst)
        self.def_keys = {}
        self._hood = {}
        self._near = {}

    def synsets_of(self, key: str, pos) -> list:
        if pos is None:
            return [s for p in ("n", "v", "a", "r") for s in self.lemma_synsets.get((key, p), [])]
        return self.lemma_synsets.get((key, pos), [])

    def definition_keys(self, sid: str) -> list:
        got = self.def_keys.get(sid)
        if got is None:
            got = self.def_keys[sid] = self.lex.content_keys(self.definition[sid])
        return got

    def neighbourhood(self, sid: str) -> dict:
        """lemma key -> best alignment weight for words around synset sid."""
        got = self._hood.get(sid)
        if got is not None:
            return got
        hood: dict = {}

        def put(key, w):
            if w > hood.get(key, 0.0):
                hood[key] = w

        def put_members(s, w):
            for m in self.members.get(s, ()):
                k = oewn.lookup_key(m)
                put(k, w)
                if " " in k or "-" in k:
                    for part in re.split(r"[ -]+", k):
                        if part and part not in STOP_WORDS:
                            put(part, w * PHRASE_FACTOR)

        for k in self.definition_keys(sid):
            put(k, W_DEFINITION)
        for rel, w in (("hyponym", W_HYPONYM), ("hypernym", W_HYPERNYM),
                       ("also", W_SIMILAR), ("similar", W_SIMILAR)):
            for dst in self.rels.get((sid, rel), ()):
                put_members(dst, w)
        put_members(sid, W_MEMBER)
        self._hood[sid] = hood
        return hood

    def near(self, sid: str) -> dict:
        """synset -> 'similar' | 'hypernym' | 'hyponym' | 'sibling' for the synsets one
        step from sid (similar/also, and the other satellites of an adjective's head,
        count as similar; a sibling shares a hypernym with sid)."""
        got = self._near.get(sid)
        if got is not None:
            return got
        got = {}
        sim = set(self.rels.get((sid, "similar"), ())) | set(self.rels.get((sid, "also"), ()))
        for x in sim:
            got[x] = "similar"
        for head in list(sim):
            for x in self.rels.get((head, "similar"), ()):
                got.setdefault(x, "similar")
        for x in self.rels.get((sid, "hypernym"), ()):
            got.setdefault(x, "hypernym")
        for x in self.rels.get((sid, "hyponym"), ()):
            got.setdefault(x, "hyponym")
        for h in self.rels.get((sid, "hypernym"), ()):
            for x in self.rels.get((h, "hyponym"), ()):
                got.setdefault(x, "sibling")
        got.pop(sid, None)
        self._near[sid] = got
        return got

    def relation(self, sid: str, word: str) -> str:
        """How WordNet links word to synset sid, closest first over the word's meanings
        with sid's part of speech: 'member', 'similar', 'hypernym', 'hyponym',
        'sibling', 'far' (no link within one step), or 'unknown' (WordNet has no
        such word with that part of speech, so only the book speaks for it)."""
        syns = self.lemma_synsets.get((oewn.lookup_key(word), self.pos[sid]))
        if not syns:
            return "unknown"
        if sid in syns:
            return "member"
        near = self.near(sid)
        found = {near[x] for x in syns if x in near}
        for r in ("similar", "hypernym", "hyponym", "sibling"):
            if r in found:
                return r
        return "far"


# --------------------------------------------------------------------------- words

class WordFilter:
    """Decides which old-book words may become synonyms, and why others may not."""

    def __init__(self, wn: WordNet, bad_synsets: set):
        self.wn = wn
        self.bad_synsets = bad_synsets
        # words whose every WordNet meaning is unfit for children, plus the blocklist
        self.bad_words = set(BLOCKLIST)
        for sid in bad_synsets:
            for m in wn.members.get(sid, ()):
                k = oewn.lookup_key(m)
                if all(s in bad_synsets for s in wn.synsets_of(k, None)):
                    self.bad_words.add(k)
        self.dropped = Counter()

    def fits_pos(self, word: str, pos: str) -> bool:
        """A one-word synonym must be a WordNet lemma with the synset's part of
        speech (so the fish 'gar' never becomes a verb meaning 'make'); phrases
        and hyphenated compounds pass."""
        key = oewn.lookup_key(word)
        if " " in key or (key, pos) in self.wn.lemma_synsets:
            return True
        if "-" in key and key not in self.wn.lemma_keys:
            return True
        self.dropped["wrong_part_of_speech"] += 1
        return False

    def clean(self, word: str):
        """The display form of word if it is usable, else None (reason counted)."""
        w = " ".join(str(word).replace("’", "'").split()).strip(" ,;:")
        if not w or not _WORD_OK.match(w) or w.endswith("."):
            self.dropped["junk"] += 1
            return None
        key = oewn.lookup_key(w)
        parts = re.split(r"[ -]+", key)
        if key in self.bad_words or blocked(key):
            self.dropped["unfit_for_children"] += 1
            return None
        if key in self.wn.lemma_keys:
            return self.wn.canonical.get(key, w)
        toks = key.split()
        if len(toks) == 1:
            if "-" in key and all(p in self.wn.lemma_keys for p in parts if p):
                return w                      # hyphenated compound of known words
            self.dropped["not_in_wordnet"] += 1
            return None
        if len(toks) > 4:
            self.dropped["phrase_too_long"] += 1
            return None
        content = 0
        for t in toks:
            if t in FUNCTION_WORDS:
                continue
            sub = [p for p in t.split("-") if p]
            if not sub or not all(self.wn.lex.lemmas(p) or p in FUNCTION_WORDS for p in sub):
                self.dropped["phrase_not_english"] += 1
                return None
            content += 1
        if not content:
            self.dropped["phrase_not_english"] += 1
            return None
        return w


def offensive_synsets(wn: WordNet) -> dict:
    """synset id -> reason, for meanings that must not be shown to children."""
    conn = wn.conn
    domains = set()
    for sid, members in wn.members.items():
        if {oewn.lookup_key(m) for m in members} & OFFENSIVE_DOMAINS:
            domains.add(sid)
    out = {}
    q = ",".join("?" * len(domains))
    for src, dst in conn.execute(
            f"SELECT src, dst FROM synset_relations WHERE rel = 'exemplifies' AND dst IN ({q})",
            sorted(domains)):
        out[src] = "offensive"
    for (sid,) in conn.execute(
            "SELECT s.synset_id FROM sense_relations r JOIN senses s ON s.id = r.src_sense "
            "JOIN senses d ON d.id = r.dst_sense "
            f"WHERE r.rel = 'exemplifies' AND d.synset_id IN ({q})", sorted(domains)):
        out[sid] = "offensive"
    for sid, origin in wn.origin.items():
        if origin == "Colloquial WordNet" and sid not in out:
            out[sid] = "modern_slang"
    return out


# --------------------------------------------------------------------------- alignment

def _word_score(wn: WordNet, word: str, hood: dict) -> float:
    key = oewn.lookup_key(word)
    if key in hood:
        return hood[key]
    toks = [t for t in re.split(r"[ -]+", key) if t and t not in STOP_WORDS]
    if len(toks) > 1:
        best = 0.0
        for t in toks:
            best = max(best, max((hood.get(k, 0.0) for k in wn.lex.keys(t)), default=0.0))
        return best * PHRASE_FACTOR
    return max((hood.get(k, 0.0) for k in wn.lex.keys(key)), default=0.0)


def align(wn: WordNet, headword: str, pos, group: list, context: list = (),
          name_keys: list = ()):
    """Score group (and optional context words / head-name keys) against every
    synset of headword with part of speech pos.

    Returns (candidates, ranked) where ranked is [(score, synset_id), ...] best
    first. pos None tries every part of speech.
    """
    hkey = oewn.lookup_key(headword)
    cands = wn.synsets_of(hkey, pos)
    ranked = []
    group_only = {}
    own = {hkey}
    gkeys = [w for w in dict.fromkeys(group) if oewn.lookup_key(w) not in own]
    ckeys = [w for w in dict.fromkeys(context)
             if oewn.lookup_key(w) not in own and w not in gkeys]
    # the headword's own words say nothing ("badger dog" vs "lap dog")
    own_parts = {hkey} | {p for p in re.split(r"[ -]+", hkey) if p}
    for sid in cands:
        hood = dict(wn.neighbourhood(sid))
        for p in own_parts:
            hood.pop(p, None)
        g = sum(_word_score(wn, w, hood) for w in gkeys)
        extra = 0.0
        if ckeys:
            extra += sum(_word_score(wn, w, hood) for w in ckeys)
        if name_keys:
            extra += max((hood.get(k, 0.0) for k in name_keys), default=0.0)
        s = g + min(CONTEXT_CAP, CONTEXT_FACTOR * extra)
        group_only[sid] = g
        ranked.append((round(s, 4), sid))
    ranked.sort(key=lambda x: (-x[0], cands.index(x[1])))
    return group_only, ranked


def decide(ranked, group_only=None, order=None):
    """(synset_id or None, reason, best_score, second_score).

    The best candidate is aligned if it scores at least MIN_SCORE (its own
    group at least GROUP_MIN) and beats every other candidate by MARGIN, and a
    candidate that WordNet lists as a commoner sense (order: synset -> position
    in WordNet's sense order) also by a factor of RATIO_UPSET, so that a rare
    sense does not win a close call ("Time" 3 "period, age, era, epoch" is not
    a prison term). Otherwise the call is too close: 'tie', left unaligned.

    Two other rules were tried on Soule and judged by eye (25 cases each), and
    dropped: letting the commoner sense win close calls picked the worse
    meaning in all 7 cases where it changed the winner, and requiring a ratio
    of 1.3 against every runner-up removed 17 right alignments and 6 wrong."""
    if not ranked:
        return None, "no_wordnet_entry", None, None
    best, sid = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0

    def passes(x, score):
        return score >= MIN_SCORE and (group_only is None or group_only[x] >= GROUP_MIN)

    if not passes(sid, best):
        return None, "below_threshold", best, second
    rank = order or {}

    def beaten(x, score):
        upset = rank.get(x, 0) < rank.get(sid, 0)     # x is the commoner sense
        return best - score >= MARGIN and (not upset or best >= RATIO_UPSET * score)

    if all(beaten(x, score) for score, x in ranked[1:]):
        return sid, "aligned", best, second
    return None, "tie", best, second


# --------------------------------------------------------------------------- the build

class Collector:
    def __init__(self):
        self.synonyms = {}        # (synset, word key, source) -> [word, score, ref]
        self.alignments = []
        self.unaligned = []
        self.withheld = {}        # (synset, word key, source) -> [word, ref, relation]
        self.stats = Counter()

    def add_synonym(self, sid, word, source, score, ref):
        k = (sid, oewn.lookup_key(word), source)
        cur = self.synonyms.get(k)
        if cur is None or score > cur[1]:
            self.synonyms[k] = [word, score, ref]

    def withhold(self, sid, word, source, ref, why):
        self.withheld.setdefault((sid, oewn.lookup_key(word), source), [word, ref, why])


def _soule_records():
    if not SOULE_JSONL.exists():
        from pipeline import soule
        rep = soule.parse()
        soule.write_jsonl(rep.records)
    with open(SOULE_JSONL, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _roget_records():
    from pipeline import roget
    if not ROGET_JSONL.exists():
        roget.write(roget.parse()[0])
    return roget.load(ROGET_JSONL)


def _order(wn: WordNet, headword: str, pos) -> dict:
    """synset -> position in WordNet's sense order for headword (commonest first)."""
    return {sid: i for i, sid in enumerate(wn.synsets_of(oewn.lookup_key(headword), pos))}


def align_soule(wn: WordNet, wf: WordFilter, col: Collector, records: list):
    for r in records:
        pos_raw = r["pos"]
        if pos_raw is not None and pos_raw not in SOULE_POS:
            for s in r["senses"]:
                col.stats["soule_unsupported_pos"] += 1
                col.unaligned.append((r["headword"], pos_raw, s["synonyms"], SOULE,
                                      "unsupported_pos", None, None, f"{r['line']}#{s['n']}"))
            continue
        pos = SOULE_POS.get(pos_raw)
        order = _order(wn, r["headword"], pos)
        for s in r["senses"]:
            ref = f"{r['line']}#{s['n']}"
            col.stats["soule_groups"] += 1
            bad = {x.lower().rstrip(".") for x in s.get("labels", [])} & SOULE_BAD_LABELS
            if bad:
                col.stats["soule_groups_unfit_for_children"] += 1
                continue
            usable = [w for w in (wf.clean(x) for x in s["synonyms"]) if w]
            usable = [w for w in dict.fromkeys(usable)
                      if oewn.lookup_key(w) != oewn.lookup_key(r["headword"])]
            if not usable:
                col.stats["soule_groups_no_usable_words"] += 1
                continue
            group_only, ranked = align(wn, r["headword"], pos, s["synonyms"])
            sid, reason, best, second = decide(ranked, group_only, order)
            if sid is None:
                col.stats[f"soule_{reason}"] += 1
                col.unaligned.append((r["headword"], pos_raw, s["synonyms"], SOULE, reason,
                                      ranked[0][1] if ranked else None, best, ref))
                continue
            col.stats["soule_aligned"] += 1
            col.alignments.append((SOULE, ref, r["headword"], wn.pos[sid], sid, best, second,
                                   len(usable)))
            for w in usable:
                if wf.fits_pos(w, wn.pos[sid]):
                    col.add_synonym(sid, w, SOULE, best, ref)


def roget_keeps(wn: WordNet, sid: str, words: list, list_label: bool = False) -> dict:
    """word -> None (keep as a synonym of sid) or the reason it is withheld.

    A Roget group is often a list of kinds of a thing (horses, shops, tools)
    rather than of synonyms, so a group word only becomes a synonym if WordNet
    links it to the synset closely (ROGET_KEEP; one-step hyponyms too, except
    for nouns, where they are kinds: "light" -> "sun"), or if WordNet does not
    know the word with this part of speech at all. Those unknown words are
    withheld too when the group looks like a list: it is labelled as one
    ("[parts of a church: list]"), or at least LIST_MIN_WORDS, and LIST_SHARE,
    of its WordNet words are only loosely linked (siblings, far, noun hyponyms).
    """
    pos = wn.pos[sid]
    keep = ROGET_KEEP | (ROGET_KEEP_EXCEPT_NOUNS if pos != "n" else set())
    rel = {w: wn.relation(sid, w) for w in words}
    known = [w for w, r in rel.items() if r not in ("unknown", "member")]
    loose = [w for w in known if rel[w] not in keep]
    is_list = list_label or (len(loose) >= LIST_MIN_WORDS and len(loose) >= LIST_SHARE * len(known))
    out = {}
    for w, r in rel.items():
        if r not in keep:
            out[w] = r
        elif r == "unknown" and is_list:
            out[w] = "unknown_in_list"
        else:
            out[w] = None
    return out


def align_roget(wn: WordNet, wf: WordFilter, col: Collector, records: list):
    for r in records:
        name_keys = wn.lex.content_keys(r["name"])
        paras = defaultdict(list)
        for gi, g in enumerate(r["groups"]):
            paras[(g["pos"], g["para"])].append(gi)
        for gi, g in enumerate(r["groups"]):
            ref = f"{r['head']}:{gi}"
            pos = ROGET_POS.get(g["pos"])
            labels = g.get("labels", {})
            # "[obs3]" only means the e-text editor's spelling checker did not know the
            # word: use such words when WordNet has them with this part of speech
            extra = [w for w in g.get("unrecognised", [])
                     if pos and (oewn.lookup_key(w), pos) in wn.lemma_synsets]
            col.stats["roget_unrecognised_words_used"] += len(extra)
            raw = g["words"] + [w for w in extra if w not in g["words"]]
            words = [w for w in raw
                     if not ({x.lower() for x in labels.get(w, [])} & ROGET_BAD_LABELS)]
            col.stats["roget_words_unfit_for_children"] += len(raw) - len(words)
            if len(words) < 2:
                col.stats["roget_groups_single_word"] += 1
                continue
            clean = {w: wf.clean(w) for w in words}
            context = [w for oi in paras[(g["pos"], g["para"])] if oi != gi
                       for w in r["groups"][oi]["words"]]
            list_label = "list" in (g.get("label") or "").lower()
            for w in words:
                usable = [c for x, c in clean.items()
                          if c and oewn.lookup_key(c) != oewn.lookup_key(w)]
                if not usable:
                    continue
                col.stats["roget_groups"] += 1
                if pos is None:
                    col.stats["roget_unsupported_pos"] += 1
                    col.unaligned.append((w, g["pos"], words, ROGET, "unsupported_pos",
                                          None, None, ref))
                    continue
                group_only, ranked = align(wn, w, pos, words, context, name_keys)
                sid, reason, best, second = decide(ranked, group_only, _order(wn, w, pos))
                if sid is not None and second == 0 and len(usable) >= LONG_GROUP \
                        and group_only[sid] < LONG_GROUP_SHARE * len(usable):
                    sid, reason = None, "long_group"     # a long list with a lone sense
                if sid is None:
                    col.stats[f"roget_{reason}"] += 1
                    col.unaligned.append((w, g["pos"], words, ROGET, reason,
                                          ranked[0][1] if ranked else None, best, ref))
                    continue
                col.stats["roget_aligned"] += 1
                col.alignments.append((ROGET, ref, w, wn.pos[sid], sid, best, second,
                                       len(usable)))
                fit = [u for u in usable if wf.fits_pos(u, wn.pos[sid])]
                for u, why in roget_keeps(wn, sid, fit, list_label).items():
                    if why is None:
                        col.add_synonym(sid, u, ROGET, best, ref)
                    else:
                        col.stats[f"roget_words_withheld_{why}"] += 1
                        col.withhold(sid, u, ROGET, ref, why)


SCHEMA = """
CREATE TABLE sense_synonyms(
    synset_id TEXT NOT NULL,
    word TEXT NOT NULL,
    word_key TEXT NOT NULL,       -- oewn.lookup_key(word)
    source TEXT NOT NULL,         -- oewn2025 | soule1871 | roget1911
    score REAL NOT NULL,          -- 1.0 for WordNet members, else the group's alignment score
    ref TEXT NOT NULL,            -- sense id, '<line>#<sense n>' (Soule) or '<head>:<group>' (Roget)
    PRIMARY KEY (synset_id, word_key, source)
) WITHOUT ROWID;
CREATE TABLE thesaurus_alignments(
    source TEXT NOT NULL,
    ref TEXT NOT NULL,
    headword TEXT NOT NULL,
    pos TEXT NOT NULL,            -- WordNet pos of the synset (n v a r)
    synset_id TEXT NOT NULL,
    score REAL NOT NULL,
    second_score REAL NOT NULL,   -- best score among the other candidate synsets
    n_words INTEGER NOT NULL      -- usable words the group gave
);
CREATE TABLE gloss_keys(
    synset_id TEXT PRIMARY KEY,
    def_keys TEXT NOT NULL,       -- space-separated lemma keys of the definition's content words
    ex_keys TEXT NOT NULL,        -- the same for the examples
    source TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE lemma_idf(
    key TEXT PRIMARY KEY,
    df INTEGER NOT NULL,          -- synsets whose definition, examples or members use the key (>= 2;
                                  -- a key missing here has df 1, see meta idf_documents)
    idf REAL NOT NULL,
    source TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE kid_filter(
    kind TEXT NOT NULL,           -- 'synset' or 'word'
    key TEXT NOT NULL,            -- synset id or lemma key
    reason TEXT NOT NULL,         -- offensive | modern_slang | blocklist
    source TEXT NOT NULL,         -- oewn2025 (WordNet's own labels) | build (this build's blocklist)
    PRIMARY KEY (kind, key)
) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX align_synset ON thesaurus_alignments(synset_id);
"""

# The audit file is for people checking the data; it is not shipped to the app. It
# holds the raw book groups as printed, offensive words included.
AUDIT_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE thesaurus_unaligned(
    headword TEXT NOT NULL,
    pos TEXT,                     -- the book's part of speech (Soule adj/n/v..., Roget n/v/adj...)
    words TEXT NOT NULL,          -- JSON list, as printed in the book
    source TEXT NOT NULL,
    reason TEXT NOT NULL,         -- below_threshold | tie | long_group | no_wordnet_entry | unsupported_pos
    best_synset TEXT,
    best_score REAL,
    ref TEXT NOT NULL
);
CREATE TABLE thesaurus_withheld(
    synset_id TEXT NOT NULL,      -- the synset the Roget group was aligned to
    word TEXT NOT NULL,
    word_key TEXT NOT NULL,
    source TEXT NOT NULL,
    ref TEXT NOT NULL,
    relation TEXT NOT NULL,       -- why it is not a synonym: sibling | far | hyponym | unknown_in_list
    PRIMARY KEY (synset_id, word_key, source)
) WITHOUT ROWID;
CREATE INDEX unaligned_headword ON thesaurus_unaligned(headword);
"""

OEWN_ATTRIBUTION = (
    "Open English Wordnet (2025 edition), by the Open English WordNet team, "
    "https://github.com/globalwordnet/english-wordnet, licensed under the Creative Commons "
    "Attribution 4.0 International License (CC BY 4.0), "
    "https://creativecommons.org/licenses/by/4.0/. "
    "Copyright (c) 2019-present, The Open English WordNet Team.")
PRINCETON_NOTICE = ("This work is based on or incorporates elements of the Princeton "
                    "University WordNet database.")
# WNDB_License.txt of the Open English WordNet repository (tag 2025-edition), which asks
# that it appear on all copies of the database.
PRINCETON_LICENCE = """This software and database is being provided to you, the LICENSEE, by
the Open English Wordnet team under the Creative Commons Attribution 4.0
International License (CC-BY 4.0).

Open English Wordnet 2023 Copyright 2023 by the Open English Wordnet team.

Permission to use, copy, modify and distribute this software and
database and its documentation for any purpose and without fee or
royalty is hereby granted, provided that you agree to comply with
the following copyright notice and statements, including the disclaimer,
and that the same appear on ALL copies of the software, database and
documentation, including modifications that you make for internal
use or for distribution.

WordNet 3.1 Copyright 2011 by Princeton University.  All rights reserved.

THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PRINCETON
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF MERCHANT-
ABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE
OF THE LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT
INFRINGE ANY THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR
OTHER RIGHTS.

The name of Princeton University or Princeton may not be used in
advertising or publicity pertaining to distribution of the software
and/or database.  Title to copyright in this software, database and
any associated documentation shall at all times remain with
Princeton University and LICENSEE agrees to preserve same.
"""


def _gloss_rows(wn: WordNet):
    ex = defaultdict(list)
    for sid, text in wn.conn.execute("SELECT synset_id, text FROM examples ORDER BY synset_id, seq"):
        ex[sid].append(text)
    rows, df = [], Counter()
    for sid in wn.definition:
        d = wn.definition_keys(sid)
        e = [k for k in wn.lex.content_keys(" ".join(ex.get(sid, []))) if k not in d]
        rows.append((sid, " ".join(k.replace(" ", "_") for k in d),
                     " ".join(k.replace(" ", "_") for k in e), OEWN))
        doc = set(d) | set(e)
        for m in wn.members.get(sid, ()):
            mk = oewn.lookup_key(m)
            doc.add(mk)
            doc.update(p for p in re.split(r"[ -]+", mk) if p and p not in STOP_WORDS)
        df.update(doc)
    n = len(wn.definition)
    # keys used by only one synset are left out: they get idf_for(1, n)
    idf_rows = [(k.replace(" ", "_"), c, round(idf_for(c, n), 4), OEWN)
                for k, c in df.items() if c > 1]
    return rows, idf_rows, n


def idf_for(df: int, n_docs: int) -> float:
    """Smoothed inverse document frequency (documents = synsets)."""
    return math.log((n_docs + 1) / (df + 1)) + 1.0


def build(db_path: Path = OUT, oewn_db: Path = OEWN_DB, soule_records=None,
          roget_records=None, verbose: bool = True, audit_path=None) -> dict:
    """Build db_path, and its audit file next to it (<name>_audit.sqlite), from
    scratch and atomically. Returns a report dict."""
    t0 = time.time()
    db_path = Path(db_path)
    audit_path = Path(audit_path) if audit_path else db_path.with_name(db_path.stem + "_audit.sqlite")
    if not Path(oewn_db).exists():
        oewn.build(oewn.XML, Path(oewn_db))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    audit_tmp = audit_path.with_name(audit_path.name + ".tmp")
    for t in (tmp, audit_tmp):
        t.unlink(missing_ok=True)
    shutil.copyfile(oewn_db, tmp)
    conn = sqlite3.connect(tmp)
    try:
        report, audit_rows = _fill(conn, soule_records, roget_records, verbose, t0)
        conn.close()
        _write_audit(audit_tmp, *audit_rows)
        for t in (tmp, audit_tmp):
            if b"gutenberg" in t.read_bytes().lower():
                raise ValueError(f"{t.name} contains the forbidden word")
    except BaseException:
        conn.close()
        for t in (tmp, audit_tmp):
            t.unlink(missing_ok=True)
        raise
    os.replace(tmp, db_path)
    os.replace(audit_tmp, audit_path)
    report["db"] = str(db_path)
    report["db_bytes"] = db_path.stat().st_size
    report["audit_db"] = str(audit_path)
    report["seconds"] = round(time.time() - t0, 1)
    return report


def _write_audit(path: Path, unaligned: list, withheld: dict):
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.executescript(AUDIT_SCHEMA)
        with conn:
            conn.executemany("INSERT INTO meta VALUES (?,?)", [
                ("purpose", "Book groups that were not aligned, and Roget words withheld from "
                            "sense_synonyms, for review. Not for the app: the words are as "
                            "printed, offensive ones included."),
                ("english_build_version", BUILD_VERSION)])
            conn.executemany("INSERT INTO thesaurus_unaligned VALUES (?,?,?,?,?,?,?,?)",
                             [(h, p, json.dumps(w, ensure_ascii=False), s, r, b, sc, ref)
                              for h, p, w, s, r, b, sc, ref in unaligned])
            conn.executemany("INSERT INTO thesaurus_withheld VALUES (?,?,?,?,?,?)",
                             [(sid, word, k, src, ref, why)
                              for (sid, k, src), (word, ref, why) in sorted(withheld.items())])
        conn.execute("VACUUM")
    finally:
        conn.close()


def _log(verbose, t0, msg):
    if verbose:
        print(f"[{time.time() - t0:6.1f}s] {msg}", file=sys.stderr, flush=True)


def _fill(conn, soule_records, roget_records, verbose, t0):
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(SCHEMA)
    wn = WordNet(conn)
    _log(verbose, t0, f"WordNet loaded: {len(wn.definition)} synsets")

    gloss, idf_rows, n_docs = _gloss_rows(wn)
    _log(verbose, t0, f"gloss keys for {len(gloss)} synsets, {len(idf_rows)} idf keys")

    bad = offensive_synsets(wn)
    wf = WordFilter(wn, {s for s, why in bad.items() if why == "offensive"})
    col = Collector()

    soule_records = _soule_records() if soule_records is None else soule_records
    align_soule(wn, wf, col, soule_records)
    _log(verbose, t0, f"Soule aligned: {col.stats['soule_aligned']}/{col.stats['soule_groups']}")
    roget_records = _roget_records() if roget_records is None else roget_records
    align_roget(wn, wf, col, roget_records)
    _log(verbose, t0, f"Roget aligned: {col.stats['roget_aligned']}/{col.stats['roget_groups']}")

    blocked_keys = set(BLOCKLIST) | {k for k in wn.lemma_keys if blocked(k)}
    syn_rows = []
    for sid, members in wn.members.items():
        if sid in bad:
            continue
        seen = set()
        for m in members:
            k = oewn.lookup_key(m)
            if k in seen:
                continue
            seen.add(k)
            if k in blocked_keys:
                col.stats["wordnet_members_blocked"] += 1
                continue
            syn_rows.append((sid, m, k, OEWN, 1.0, wn.member_sense[(sid, k)]))
    for (sid, k, source), (word, score, ref) in col.synonyms.items():
        if sid in bad:
            col.stats["old_book_rows_on_unfit_synsets"] += 1
            continue
        syn_rows.append((sid, word, k, source, score, ref))

    kid_rows = [("synset", sid, why, OEWN) for sid, why in sorted(bad.items())]
    kid_rows += [("word", w, "blocklist", "build") for w in sorted(blocked_keys)]
    kid_rows += [("word", w, "offensive", OEWN) for w in sorted(wf.bad_words - blocked_keys)]
    with conn:
        conn.executemany("INSERT INTO gloss_keys VALUES (?,?,?,?)", gloss)
        conn.executemany("INSERT INTO lemma_idf VALUES (?,?,?,?)", idf_rows)
        conn.executemany("INSERT INTO sense_synonyms VALUES (?,?,?,?,?,?)", syn_rows)
        conn.executemany("INSERT INTO thesaurus_alignments VALUES (?,?,?,?,?,?,?,?)",
                         col.alignments)
        conn.executemany("INSERT INTO kid_filter VALUES (?,?,?,?)", kid_rows)
        conn.executescript(INDEXES)

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("sense_synonyms", "thesaurus_alignments", "gloss_keys", "lemma_idf",
                        "kid_filter")}
    by_source = dict(conn.execute(
        "SELECT source, COUNT(*) FROM sense_synonyms GROUP BY source").fetchall())
    synsets_with_book = conn.execute(
        "SELECT COUNT(DISTINCT synset_id) FROM sense_synonyms WHERE source != ?",
        (OEWN,)).fetchone()[0]
    new_words = conn.execute(
        "SELECT COUNT(*) FROM (SELECT synset_id, word_key FROM sense_synonyms "
        "GROUP BY synset_id, word_key HAVING SUM(source = ?) = 0)", (OEWN,)).fetchone()[0]
    unaligned_reasons = dict(sorted(Counter(f"{u[3]}:{u[4]}" for u in col.unaligned).items()))
    withheld_reasons = dict(sorted(Counter(why for _, _, why in col.withheld.values()).items()))
    params = {"W_MEMBER": W_MEMBER, "W_SIMILAR": W_SIMILAR, "W_HYPERNYM": W_HYPERNYM,
              "W_HYPONYM": W_HYPONYM, "W_DEFINITION": W_DEFINITION,
              "PHRASE_FACTOR": PHRASE_FACTOR, "CONTEXT_FACTOR": CONTEXT_FACTOR,
              "CONTEXT_CAP": CONTEXT_CAP, "MIN_SCORE": MIN_SCORE, "GROUP_MIN": GROUP_MIN,
              "MARGIN": MARGIN, "RATIO_UPSET": RATIO_UPSET,
              "LONG_GROUP": LONG_GROUP, "LONG_GROUP_SHARE": LONG_GROUP_SHARE,
              "ROGET_KEEP": sorted(ROGET_KEEP), "ROGET_KEEP_EXCEPT_NOUNS": sorted(ROGET_KEEP_EXCEPT_NOUNS),
              "LIST_MIN_WORDS": LIST_MIN_WORDS, "LIST_SHARE": LIST_SHARE}
    unfit = Counter(bad.values())
    changes = [
        "Synsets with more than one definition have them joined with '; '.",
        "One example sentence was removed because it contains a word this project keeps "
        "out of its data.",
        "Definitions and examples were lemmatised into search keys (gloss_keys), and word "
        "weights were computed from them (lemma_idf).",
        f"Synonyms from two public-domain thesauri, Soule (1871) and Roget (1911), were matched "
        f"to WordNet meanings and added to sense_synonyms ({by_source.get(SOULE, 0)} and "
        f"{by_source.get(ROGET, 0)} rows, sources soule1871 and roget1911; the matches are in "
        f"thesaurus_alignments).",
        f"{unfit.get('offensive', 0)} meanings WordNet marks as disparaging, obscene or ethnic "
        f"slurs, and {unfit.get('modern_slang', 0)} modern slang meanings from Colloquial "
        f"WordNet, are listed in kid_filter and are never shown to children; they have no "
        f"sense_synonyms rows.",
        f"{col.stats['wordnet_members_blocked']} WordNet synonyms that are on this build's "
        f"blocklist of explicit and offensive words were left out of sense_synonyms; the "
        f"words are listed in kid_filter.",
        "For the app screen, some American spellings are replaced by British ones from the "
        "same synset (for example 'colourless' for 'colorless').",
    ]
    meta = {
        "english_build_version": BUILD_VERSION,
        "english_built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "english_sources": json.dumps([OEWN, SOULE, ROGET]),
        "english_counts": json.dumps(counts, sort_keys=True),
        "idf_documents": str(n_docs),
        "alignment_params": json.dumps(params, sort_keys=True),
        "attribution": OEWN_ATTRIBUTION,
        "princeton_notice": PRINCETON_NOTICE,
        "princeton_licence": PRINCETON_LICENCE,
        "changes": " ".join(changes),
        "attribution_soule1871": "Richard Soule, A Dictionary of English Synonymes (1871); "
                                 "public domain.",
        "attribution_roget1911": "Roget's Thesaurus of English Words and Phrases, 1911 "
                                 "edition (Peter Mark Roget, John Lewis Roget, Samuel Romilly "
                                 "Roget); public domain.",
    }
    with conn:
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", sorted(meta.items()))
    conn.execute("ANALYZE")
    conn.commit()
    conn.execute("VACUUM")
    _log(verbose, t0, "written")
    report = {
        "counts": counts,
        "sense_synonyms_by_source": by_source,
        "synsets_with_old_book_synonyms": synsets_with_book,
        "old_book_words_new_to_wordnet_synset": new_words,
        "unaligned_by_reason": unaligned_reasons,
        "roget_withheld_by_reason": withheld_reasons,
        "audit_counts": {"thesaurus_unaligned": len(col.unaligned),
                         "thesaurus_withheld": len(col.withheld)},
        "stats": dict(sorted(col.stats.items())),
        "words_dropped": dict(sorted(wf.dropped.items())),
        "unfit_synsets": dict(unfit),
        "idf_documents": n_docs,
        "alignment_params": params,
    }
    return report, (col.unaligned, col.withheld)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else OUT
    report = build(out)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
