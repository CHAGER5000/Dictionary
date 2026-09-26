"""Pick the meaning of a tapped word from the sentence around it.

    from pipeline.context import open_english, pick_sense
    conn = open_english()                      # data/english.sqlite, read-only
    pick_sense(conn, "The little boat drifted slowly towards the bank.", "bank")
    # -> [{"synset_id": "oewn-09236472-n", "definition": "sloping land ...",
    #      "score": 11.936, "clues": ["boat", "drifted"], "lemma": "bank",
    #      "pos": "n", "rank": 1}, ...]

The first part of this file holds the text helpers that pipeline.build shares
(tokenise, stop words, lemma keys); pick_sense and its scoring come after.

How a meaning is scored
-----------------------
Every WordNet synset the tapped word can belong to, through any lemma it can
be a form of (lemmatize("rose") -> rose n, rise v), is a candidate; meanings
marked unfit for children in kid_filter are never candidates. Each candidate
gets a *signature*: lemma keys that tend to appear near that meaning, each
with a weight, from

* its definition and examples, and its members (synonyms);
* the old-book synonyms aligned to it (sense_synonyms, Soule and Roget);
* the members, definitions and examples of related synsets: hypernyms,
  hyponyms, similar, also, domain topic/region, part/member/substance
  meronyms and holonyms, attribute, entails and causes, and the synsets of
  senses derived from it (bark n "the sound made by a dog" <- bark v).

The tapped word itself, its lemmas and all their forms ("trained", "trains",
"rose" for rise) are never clues and never signature words. Each other
content word of the sentence (a *clue*) then scores for a candidate:

* direct: signature weight x idf of the clue's lemma ("money" in the
  definition of the financial bank);
* indirect: what the clue's own meanings talk about (the words of their
  definitions and members, and of their hypernyms and hyponyms, averaged over
  its senses; examples and names are left out) that overlaps the signature,
  x INDIRECT, damped for very large signatures (INDIRECT_SIG_REF) and capped
  at INDIRECT_MAX. This is how "boat" (a small vessel for travel on water)
  supports "sloping land beside a body of water". Very general words (make,
  use, move, part ...) never link meanings this way.

A clue keeps the larger of the two, times a mild distance decay, and a lemma
counts once however often it appears. Names ("Nina", "Grandma's") are not
clues. A candidate's score is the sum over clues, plus

* PRIOR / rank (WordNet lists a word's common senses first),
* POS_BONUS x a part-of-speech hint from the neighbouring words ("the ...",
  "the old stone ...", "to ...", "can ...", "is ...", a name before an
  inflected verb); a clear hint for another part of speech also multiplies
  the candidate's clue score by POS_OTHER,
* COLLOCATION x extra words when the sentence shares a run of three or more
  words, with the target and one content word, with one of the meaning's
  WordNet examples ("give me a hand").

The clues a child is shown are those that matched a word of the meaning
directly, or through their own meaning by a word that is in both the
meaning's own words and the clue's own definition or synonyms ("boat" ->
"water"); weaker indirect links still count but are not shown, and neither
are very general words.

Every number above was tuned on the dev split of tests/data/wsd_eval.jsonl
only; pipeline.evaluate reports dev and test. In the second round (after a
review that had looked at test items), INDIRECT, INDIRECT_MAX,
INDIRECT_SIG_REF and POS_OTHER were chosen by a grid on dev: the best dev
score was reached by many settings, and the one leaning least on the
indirect channel was taken.
"""
from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

from pipeline import oewn

ENGLISH_DB = Path(__file__).resolve().parent.parent / "data" / "english.sqlite"

# --------------------------------------------------------------------------- text

# Standard English function words plus a few very general words that carry no
# topic. They never count as clues.
STOP_WORDS = frozenset("""
a about above across after afterwards again against ago all almost alone along already also
although always am among an and another any anybody anyone anything anyway anywhere are
around as at away be became because become becomes been before beforehand behind being
below beside besides between beyond both but by can cannot can't could couldn't did didn't
do does doesn't doing don't done down during each either else elsewhere enough etc even ever
every everybody everyone everything everywhere except few for from further get gets getting
got had hadn't has hasn't have haven't having he he'd he'll her here hers herself he's him
himself his how however i i'd if i'll i'm in indeed instead into is isn't it its it's itself
i've just last least less let let's like many may me might mine more moreover most mostly
much must mustn't my myself near nearly neither never nevertheless next no nobody none
noone nor not nothing now nowhere of off often oh ok okay on once one ones only onto or
other others otherwise our ours ourselves out over own per perhaps please quite rather
really same several shall shan't she she'd she'll she's should shouldn't since so some
somebody someone something sometime sometimes somewhere soon still such than that that's
the their theirs them themselves then there thereby therefore these they they'd they'll
they're they've thing things this those though through throughout thus till to together
too toward towards under unless until up upon us very via was wasn't way we we'd we'll
well were we're weren't we've what whatever what's when whenever where whereas wherever
whether which while who whoever whole whom whose why will with within without won't would
wouldn't yes yet you you'd you'll your you're yours yourself yourselves you've
especially usually typically generally various esp e.g i.e eg ie
two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty
hundred thousand million
""".split())

# Very general words: fine as direct clues, but too common to link meanings
# indirectly ("hat" -> "cover" -> "tree trunk").
GENERIC = frozenset("""
make made use used using move give take cause put get go come become have do be person
people someone something thing part act action form state kind type sort number way time
place unit amount small large big great long short good bad new old high low certain
particular different general main whole first second side end point line group set area
body quality condition process activity event object matter means result case system
relate related relating consist consisting include including contain containing refer
show provide keep hold bring turn run let
""".split())

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def tokenise(text: str) -> list:
    """Word tokens in order, as (surface, start, end). Hyphenated words are split
    into their parts ("shallow-water" -> "shallow", "water"); apostrophes inside a
    word are kept ("can't", "Grandma's")."""
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text or "")]


def words(text: str) -> list:
    """Just the surface tokens of text."""
    return [t for t, _, _ in tokenise(text)]


def is_stop(token: str) -> bool:
    k = oewn.lookup_key(token)
    return k in STOP_WORDS or len(k) < 2


class Lexicon:
    """Lemma lookups for one connection, cached (the lemmatiser runs SQL)."""

    def __init__(self, conn):
        self.conn = conn
        self._keys: dict = {}
        self._lemmas: dict = {}

    def lemmas(self, token: str) -> list:
        """oewn.lemmatize(token), cached: [(lemma, pos), ...]."""
        k = oewn.lookup_key(token)
        got = self._lemmas.get(k)
        if got is None:
            got = self._lemmas[k] = oewn.lemmatize(self.conn, k)
        return got

    def keys(self, token: str) -> tuple:
        """Lemma keys a token stands for, most likely first.

        A word that is itself a lemma keeps its irregular lemmas (saw -> see,
        found -> find) and regular noun/verb inflections (glasses -> glass,
        waiting -> wait), but not the suffix-rule guesses that would turn
        "number" into the comparative of "numb". Unknown words stand for
        themselves.
        """
        k = oewn.lookup_key(token)
        got = self._keys.get(k)
        if got is not None:
            return got
        lem = self.lemmas(k)
        out = []
        if any(oewn.lookup_key(l) == k for l, _ in lem):
            out.append(k)
            irregular = {oewn.lookup_key(r[0]) for r in self.conn.execute(
                "SELECT e.lemma FROM forms f JOIN entries e ON e.id = f.entry_id "
                "WHERE f.form_key = ?", (k,))}
            for l, p in lem:
                lk = oewn.lookup_key(l)
                if lk in irregular or (p in ("n", "v") and lk != k):
                    out.append(lk)
        else:
            out = [oewn.lookup_key(l) for l, _ in lem]
        got = self._keys[k] = tuple(dict.fromkeys(out)) if out else (k,)
        return got

    def content_keys(self, text: str) -> list:
        """Distinct lemma keys of the content words of text (stop words dropped)."""
        out = []
        for tok in words(text):
            if is_stop(tok):
                continue
            for key in self.keys(tok):
                if key not in STOP_WORDS and key not in out:
                    out.append(key)
        return out


_LEXICONS: dict = {}


def lexicon(conn) -> Lexicon:
    """The cached Lexicon for this connection."""
    got = _LEXICONS.get(id(conn))
    if got is None or got.conn is not conn:
        if len(_LEXICONS) > 8:
            _LEXICONS.clear()
        got = _LEXICONS[id(conn)] = Lexicon(conn)
    return got


# --------------------------------------------------------------------------- parameters

# signature weights
W_DEF = 1.0
W_EXAMPLE = 1.0
W_MEMBER = 1.0
W_MEMBER_PART = 0.6          # a word inside a multiword member ("savings" in "savings bank")
W_BOOK = 0.7                 # old-book synonyms aligned to the synset
REL_WEIGHTS = {
    "hypernym": 0.7, "hyponym": 0.6, "similar": 0.7, "also": 0.6,
    "domain_topic": 0.8, "domain_region": 0.6, "has_domain_topic": 0.4,
    "mero_part": 0.6, "holo_part": 0.6, "mero_member": 0.6, "holo_member": 0.6,
    "mero_substance": 0.6, "holo_substance": 0.6, "attribute": 0.5,
    "entails": 0.5, "is_entailed_by": 0.4, "causes": 0.5, "is_caused_by": 0.4,
    "derivation": 0.6,
}
REL_DEF_FACTOR = 0.6         # a related synset's definition counts this much of its members
REL_EX_FACTOR = 0.3          # ... and its examples this much
MAX_RELATED = 60             # at most this many synsets per relation (hyponym lists can be huge)

# clue weights
INDIRECT = 0.35              # weight of the indirect (clue's own meanings) overlap
INDIRECT_MAX = 2.0           # per-clue cap on the indirect score (a direct hit is ~5-10)
INDIRECT_MIN_IDF = 4.5       # keys commoner than this never link meanings indirectly
BEST_SENSE_MIX = 0.25         # indirect overlap = (1 - mix) x average over the clue's senses
                             #                  + mix x its best-matching sense
INDIRECT_SIG_REF = 150       # indirect x min(1, sqrt(this / signature size)): a meaning with a
                             # huge signature collects indirect points by chance
DIST_DECAY = 0.04            # clue weight = 1 / (1 + DIST_DECAY * (distance in words - 1))
PRIOR = 2.0                  # prior = PRIOR / rank
POS_BONUS = 4.0              # x the strength (0..1.5) of the part-of-speech hint
POS_OTHER = 0.5              # clue score x this for a meaning of another part of speech than
                             # a clear cue (strength 1) points to ("his seal", "very cross")
COLLOCATION = 3.0            # per word beyond two shared with an example
MIN_CLUE = 0.8               # a clue is reported if it added at least this much
CLUE_SHARE = 0.12            # ... and at least this share of the meaning's clue score

POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective", "r": "adverb"}

_DETERMINERS = frozenset("""a an the my your his her its our their this that these those some
any every each no another either neither which what whose one two three four five six seven
eight nine ten twenty hundred many few several more most much""".split())
_SUBJECTS = frozenset("i you we they he she it who".split())
_MODALS = frozenset("""can can't cannot could couldn't will won't would wouldn't shall should
shouldn't may might must mustn't do does did don't doesn't didn't to let's 'll""".split())
_COPULAS = frozenset("""is isn't was wasn't are aren't were weren't be been being am 'm
feel feels felt feeling look looks looked seem seems seemed become became""".split())
_DEGREE = frozenset("very so too quite really rather".split())
_PREPOSITIONS = frozenset("""in on at into onto from with by for of under over through across
behind beside near""".split())


def open_english(path=None) -> sqlite3.Connection:
    """Open data/english.sqlite (or path) read-only."""
    return oewn.open_db(Path(path) if path else ENGLISH_DB)


WARM_UP_SENTENCES = (
    ("The dog ran across the field to fetch the ball.", "ran"),
    ("She put the book back on the shelf.", "book"),
    ("We had a lovely time at the seaside.", "time"),
)


def warm_up(conn, sentences=WARM_UP_SENTENCES) -> None:
    """Fill the caches with a few look-ups, then freeze the garbage collector's
    view of them (gc.freeze), so later calls are not held up by collections
    that walk the caches (those caused the rare 40-50 ms calls)."""
    import gc
    for sentence, target in sentences:
        pick_sense(conn, sentence, target)
    gc.collect()
    gc.freeze()


# --------------------------------------------------------------------------- data access

class _Store:
    """Cached reads of the synset data the picker needs, per connection."""

    def __init__(self, conn):
        self.conn = conn
        self.lex = lexicon(conn)
        self.info = {}         # synset -> (pos, definition, def_keys, ex_keys, members)
        self.rels = {}         # synset -> [(rel, dst)]
        self.book = {}         # synset -> [word key]
        self.examples = {}     # synset -> [[token keys]]
        self.flagged = None
        self.sig = {}          # (synset, dropped keys) -> signature
        self.expansion = {}    # clue key -> {key: weight}
        self.idf = {}
        n = conn.execute("SELECT value FROM meta WHERE key = 'idf_documents'").fetchone()
        self.n_docs = int(n[0]) if n else 107519
        self.idf_default = math.log((self.n_docs + 1) / 2) + 1.0

    def _chunks(self, ids):
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            yield chunk, ",".join("?" * len(chunk))

    def load(self, ids):
        need = [i for i in dict.fromkeys(ids) if i not in self.info]
        for chunk, q in self._chunks(need):
            got = {r[0]: [r[1], r[2], r[3].split(), r[4].split(), []] for r in self.conn.execute(
                "SELECT y.id, y.pos, y.definition, g.def_keys, g.ex_keys FROM synsets y "
                f"JOIN gloss_keys g ON g.synset_id = y.id WHERE y.id IN ({q})", chunk)}
            for sid, lemma in self.conn.execute(
                    f"SELECT synset_id, lemma FROM senses WHERE synset_id IN ({q}) "
                    "ORDER BY synset_id, member_rank", chunk):
                got[sid][4].append(lemma)
            for sid in chunk:
                self.info[sid] = tuple(got.get(sid, ["", "", [], [], []]))
        return [self.info[i] for i in ids]

    def relations(self, ids):
        need = [i for i in dict.fromkeys(ids) if i not in self.rels]
        for chunk, q in self._chunks(need):
            got = {s: [] for s in chunk}
            for src, rel, dst in self.conn.execute(
                    f"SELECT src, rel, dst FROM synset_relations WHERE src IN ({q})", chunk):
                if rel in REL_WEIGHTS:
                    got[src].append((rel, dst))
            for src, dst in self.conn.execute(
                    "SELECT s.synset_id, d.synset_id FROM senses s "
                    "JOIN sense_relations r ON r.src_sense = s.id AND r.rel = 'derivation' "
                    f"JOIN senses d ON d.id = r.dst_sense WHERE s.synset_id IN ({q})", chunk):
                if dst != src and ("derivation", dst) not in got[src]:
                    got[src].append(("derivation", dst))
            self.rels.update(got)
        return {i: self.rels[i] for i in ids}

    def book_words(self, ids):
        need = [i for i in dict.fromkeys(ids) if i not in self.book]
        for chunk, q in self._chunks(need):
            got = {s: [] for s in chunk}
            for sid, key in self.conn.execute(
                    f"SELECT synset_id, word_key FROM sense_synonyms WHERE synset_id IN ({q}) "
                    "AND source != 'oewn2025'", chunk):
                got[sid].append(key)
            self.book.update(got)
        return {i: self.book[i] for i in ids}

    def example_tokens(self, ids):
        """synset -> list of examples, each a list of lower-cased surface words."""
        need = [i for i in dict.fromkeys(ids) if i not in self.examples]
        for chunk, q in self._chunks(need):
            got = {s: [] for s in chunk}
            for sid, text in self.conn.execute(
                    f"SELECT synset_id, text FROM examples WHERE synset_id IN ({q}) "
                    "ORDER BY synset_id, seq", chunk):
                got[sid].append([oewn.lookup_key(t) for t in words(text)])
            self.examples.update(got)
        return {i: self.examples[i] for i in ids}

    def unfit(self) -> set:
        """Synsets in kid_filter. A database without the table is an error: the
        picker must never offer meanings the children's filter would hide."""
        if self.flagged is None:
            try:
                self.flagged = {r[0] for r in self.conn.execute(
                    "SELECT key FROM kid_filter WHERE kind = 'synset'")}
            except sqlite3.OperationalError as e:
                raise RuntimeError("this database has no kid_filter table; the picker needs "
                                   "data/english.sqlite from python3 -m pipeline.build") from e
        return self.flagged

    def idf_of(self, keys):
        need = [k for k in dict.fromkeys(keys) if k not in self.idf]
        for chunk, q in self._chunks(need):
            found = dict(self.conn.execute(
                f"SELECT key, idf FROM lemma_idf WHERE key IN ({q})", chunk).fetchall())
            for k in chunk:
                self.idf[k] = found.get(k, self.idf_default)
        return self.idf

    def synsets_of(self, lemma: str, pos: str) -> list:
        """[(synset_id, rank)] for one (lemma, pos), in WordNet order."""
        poss = oewn.normalise_pos(pos)
        q = ",".join("?" * len(poss))
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT s.synset_id, s.rank FROM entries e JOIN senses s ON s.entry_id = e.id "
            f"WHERE e.lemma_key = ? AND e.pos IN ({q}) ORDER BY e.lemma = ? DESC, e.id, s.rank",
            (oewn.lookup_key(lemma), *poss, lemma))]

    # -- signatures
    def signature(self, sid: str, drop: frozenset) -> dict:
        """key -> (weight, how) for synset sid; keys in drop (the target) left out."""
        ck = (sid, drop)
        got = self.sig.get(ck)
        if got is not None:
            return got
        sig: dict = {}

        def put(key, w, how):
            if key in drop or key in STOP_WORDS:
                return
            cur = sig.get(key)
            if cur is None or w > cur[0]:
                sig[key] = (w, how)

        def put_members(members, w, how):
            for m in members:
                k = oewn.lookup_key(m)
                put(k.replace(" ", "_"), w, how)
                if " " in k or "-" in k:
                    for part in re.split(r"[ -]+", k):
                        if part:
                            put(part, w * W_MEMBER_PART, how)

        _, _, dkeys, ekeys, members = self.load([sid])[0]
        by_rel: dict = {}
        for rel, dst in self.relations([sid])[sid]:
            lst = by_rel.setdefault(rel, [])
            if len(lst) < MAX_RELATED:
                lst.append(dst)
        self.load([d for lst in by_rel.values() for d in lst])
        for rel, lst in by_rel.items():
            w = REL_WEIGHTS[rel]
            for dst in lst:
                _, _, rd, rex, rm = self.info[dst]
                put_members(rm, w, rel)
                for k in rd:
                    put(k, w * REL_DEF_FACTOR, rel)
                for k in rex:
                    put(k, w * REL_EX_FACTOR, rel)
        for k in self.book_words([sid])[sid]:
            put(k.replace(" ", "_"), W_BOOK, "old-book synonym")
            if " " in k:
                for part in k.split():
                    put(part, W_BOOK * W_MEMBER_PART, "old-book synonym")
        for k in ekeys:
            put(k, W_EXAMPLE, "example")
        for k in dkeys:
            put(k, W_DEF, "definition")
        put_members(members, W_MEMBER, "synonym")
        if len(self.sig) > 20000:
            self.sig.clear()
        self.sig[ck] = sig
        return sig

    def prefetch(self, keys):
        """Load, in a few batched queries, what expand() will need for keys."""
        need = [k for k in dict.fromkeys(keys) if k not in self.expansion]
        if not need:
            return
        ids = []
        for chunk, q in self._chunks(need):
            ids += [r[0] for r in self.conn.execute(
                "SELECT s.synset_id FROM entries e JOIN senses s ON s.entry_id = e.id "
                f"WHERE e.lemma_key IN ({q})", chunk)]
        self.load(ids)
        rels = self.relations(ids)
        self.load([d for sid in ids for r, d in rels[sid] if r in ("hypernym", "hyponym")])

    def expand(self, key: str):
        """What a clue word's own meanings talk about: the words of their
        definitions and their members (weight 1), and of the members (0.6) and
        definitions (0.4) of their hypernyms and hyponyms. Examples and names
        are left out: they tie meanings together by accident ("Princeton").

        Returns (mean, per_sense): mean is key -> weight in [0, 1] averaged over
        the clue's senses (earlier senses count a little more); per_sense is
        key -> [(sense index, weight in that sense)], so a caller can also ask
        how well the clue's single best-fitting sense matches.
        """
        got = self.expansion.get(key)
        if got is not None:
            return got
        senses = []
        for lemma, pos in self.lex.lemmas(key):
            if oewn.lookup_key(lemma) == key:
                senses += self.synsets_of(lemma, pos)
        senses = list(dict.fromkeys(senses))[:40]
        mean: dict = {}
        per_sense: dict = {}
        if senses:
            ids = [s for s, _ in senses]
            self.load(ids)
            rels = self.relations(ids)
            near = {s: [d for r, d in rels[s] if r in ("hypernym", "hyponym")][:25] for s in ids}
            self.load([d for lst in near.values() for d in lst])
            weights = [1.0 / (1.0 + 0.15 * (rank - 1)) for _, rank in senses]
            total = sum(weights)
            for idx, ((sid, _), wt) in enumerate(zip(senses, weights)):
                bag: dict = {}

                def add(k, v):
                    if v > bag.get(k, 0.0):
                        bag[k] = v

                _, _, dk, _, mem = self.info[sid]
                for k in dk:
                    add(k, 1.0)
                for m in mem:
                    if m[:1].isupper():
                        continue                  # names ("Princeton") link nothing
                    for part in re.split(r"[ -]+", oewn.lookup_key(m)):
                        add(part, 1.0)
                for d in near[sid]:
                    _, _, rdk, _, rmem = self.info[d]
                    for m in rmem:
                        if m[:1].isupper():
                            continue
                        for part in re.split(r"[ -]+", oewn.lookup_key(m)):
                            add(part, 0.6)
                    for k in rdk:
                        add(k, 0.4)
                for k, v in bag.items():
                    mean[k] = mean.get(k, 0.0) + v * wt / total
                    per_sense.setdefault(k, []).append((idx, v))
        idf = self.idf_of(list(mean))
        keep = {k for k in mean
                if k and k != key and k not in STOP_WORDS and k not in GENERIC
                and idf[k] >= INDIRECT_MIN_IDF}
        got = ({k: v for k, v in mean.items() if k in keep},
               {k: v for k, v in per_sense.items() if k in keep})
        if len(self.expansion) > 20000:
            self.expansion.clear()
        self.expansion[key] = got
        return got


_STORES: dict = {}


def _store(conn) -> _Store:
    got = _STORES.get(id(conn))
    if got is None or got.conn is not conn:
        if len(_STORES) > 8:
            _STORES.clear()
        got = _STORES[id(conn)] = _Store(conn)
    return got


# --------------------------------------------------------------------------- cues

def find_target(tokens, target: str, at: int | None = None, lex=None) -> int:
    """Index of the tapped word in tokens (-1 if absent).

    at is the character offset of the tap in the sentence: the token there
    wins, so the second "bat" of a sentence can be looked up. Without it the
    first token spelled like target is used, or failing that (with lex) the
    first token that is a form of the same lemma ("rose" for "rise"); a word
    that merely contains target ("bring" for "ring") never counts."""
    want = oewn.lookup_key(target.strip(" \t\n.,;:!?\"'()"))
    same = [i for i, (t, _, _) in enumerate(tokens) if oewn.lookup_key(t) == want]
    if not same and lex is not None and want:
        keys = set(lex.keys(want))
        same = [i for i, (t, _, _) in enumerate(tokens) if keys & set(lex.keys(t))]
    if at is not None:
        for i, (_, start, end) in enumerate(tokens):
            if start <= at < end:
                return i
        if same:
            return min(same, key=lambda i: abs(tokens[i][1] - at))
    return same[0] if same else -1


def pos_cues(tokens, i: int, lex: Lexicon, sentence: str = "") -> dict:
    """Part-of-speech hints from the words around token i: {pos: strength 0..1.5}."""
    low = [oewn.lookup_key(t) for t, _, _ in tokens]
    word = low[i]
    prev = low[i - 1] if i > 0 else None
    prev2 = low[i - 2] if i > 1 else None
    nxt = low[i + 1] if i + 1 < len(low) else None
    cues: dict = {}

    def cue(pos, s):
        cues[pos] = cues.get(pos, 0.0) + s

    def only(tok, poss):
        got = {p for _, p in lex.lemmas(tok)}
        return bool(got) and got <= poss

    lemmas = lex.lemmas(word)
    can_be = {p for _, p in lemmas}
    inflected_verb = any(p == "v" and oewn.lookup_key(l) != word for l, p in lemmas)
    possessive = bool(prev and (prev.endswith("'s") or prev.endswith("s'")))
    next_is_noun = nxt is not None and nxt not in STOP_WORDS \
        and "n" in {p for _, p in lex.lemmas(nxt)}
    after_adjective = prev is not None and prev not in STOP_WORDS and only(prev, {"a", "r"})

    if prev in _DETERMINERS or possessive:
        if next_is_noun and "a" in can_be:
            cue("a", 1.0)                             # "the right answer"
        else:
            cue("n", 1.0)                             # "the bank", "Grandma's attic"
    elif after_adjective and (prev2 in _DETERMINERS or prev2 is None or "n" in can_be):
        cue("n", 1.0)                                 # "one loud bark", "pretty shells"
    elif _determiner_before(low, i, 1 if inflected_verb else 3):
        if next_is_noun and "a" in can_be:
            cue("a", 0.8)                             # "a deep blue sea"
        else:
            cue("n", 0.8)                             # "the final note", "the old stone well"
    elif prev in _PREPOSITIONS:
        cue("n", 0.6)
    if prev in _MODALS or prev in _SUBJECTS or prev == "please":
        cue("v", 1.0)                                 # "to wind", "we plant", "can't bear"
    if nxt in _DETERMINERS and prev not in _DETERMINERS and prev not in _PREPOSITIONS \
            and not possessive:
        cue("v", 0.5)                                 # "... wind the clock", not "in spring the"
    if prev in _COPULAS:
        if inflected_verb or word.endswith("ing"):
            cue("v", 0.6)
        cue("a", 0.8)                                 # "isn't fair", "feeling well"
    if prev in _DEGREE:
        cue("a", 1.0)
    if i == 0 and (nxt is None or nxt in _DETERMINERS or nxt in _SUBJECTS
                   or sentence[tokens[i][2]:tokens[i][2] + 1] == "!"):
        cue("v", 0.8)                                 # "Duck!", "Plant the seeds"
    if inflected_verb and prev and prev not in _DETERMINERS and not possessive \
            and prev not in STOP_WORDS and _is_name(lex.conn, tokens, i - 1):
        cue("v", 0.8)                                 # "Sofia trains", "Nina left", not "Turn left"
    return {p: min(v, 1.5) for p, v in cues.items()}


def _determiner_before(low, i, reach=3) -> bool:
    """Is there a determiner one to `reach` words before token i with only content
    words (modifiers) between it and i ("the final note", "the old stone well")?
    The word right before i must not be a degree word ("the very ..."). pos_cues
    looks past one modifier only for a word that may be an inflected verb, which
    usually follows its noun phrase ("the school bell rings")."""
    if i < 2 or low[i - 1] in STOP_WORDS or low[i - 1] in _DEGREE:
        return False
    for j in range(i - 2, max(i - 2 - reach, -1), -1):
        if low[j] in _DETERMINERS:
            return True
        if low[j] in STOP_WORDS:
            return False
    return False


def _collocation(sent_keys, ti, examples, is_target) -> int:
    """Longest run of words the sentence shares with one example, through the
    target; 0 unless it is 3+ words and holds a content word besides the target.
    sent_keys[i] is the set of forms token i may match (its surface and lemmas);
    examples are lists of lower-cased surface words; is_target(word) says
    whether an example word is a form of the tapped word."""
    best = 0
    for ex in examples:
        for j, w in enumerate(ex):
            if not is_target(w):
                continue
            lo = 0
            while ti - lo - 1 >= 0 and j - lo - 1 >= 0 and ex[j - lo - 1] in sent_keys[ti - lo - 1]:
                lo += 1
            hi = 0
            while ti + hi + 1 < len(sent_keys) and j + hi + 1 < len(ex) and \
                    ex[j + hi + 1] in sent_keys[ti + hi + 1]:
                hi += 1
            n = lo + hi + 1
            if n >= 3 and any(ex[j + d] not in STOP_WORDS
                              for d in range(-lo, hi + 1) if d):
                best = max(best, n)
    return best


def _is_name(conn, tokens, j) -> bool:
    """Is token j a person's name ("Tom", "Nina", "Grandma's")? Names are not clues.

    Inside a sentence, a capitalised word is a name unless WordNet has it
    capitalised as something other than a person ("Canada", "Christmas",
    "April" stay clues). At the start of a sentence it is a name only if
    WordNet has no lower-case word of that spelling either ("Sam", but not
    "Duck" or "Hurry")."""
    tok = tokens[j][0]
    if not tok[:1].isupper() or (tok.isupper() and len(tok) > 1):
        return False                               # "BIG", "OK": emphasis, not a name
    base = tok[:-2] if tok.endswith(("'s", "’s")) else tok
    rows = conn.execute(
        "SELECT e.lemma, y.lexfile FROM entries e JOIN senses s ON s.entry_id = e.id "
        "JOIN synsets y ON y.id = s.synset_id WHERE e.lemma_key = ?",
        (oewn.lookup_key(base),)).fetchall()
    proper = any(l == base and lf != "noun.person" for l, lf in rows)
    if j > 0:
        return not proper
    return not proper and not any(l == l.lower() for l, _ in rows)


# --------------------------------------------------------------------------- picker

def _inflections(lemma: str, pos: str) -> set:
    """Regular inflected spellings of a one-word lemma (over-generating is harmless:
    the result is only used to keep the tapped word out of its own clues)."""
    w = lemma
    if not w or " " in w:
        return set()
    out = set()
    if pos in ("n", "v"):
        out |= {w + "s", w + "es", w + "'s"}
        if w.endswith("y"):
            out.add(w[:-1] + "ies")
    if pos == "v":
        out |= {w + "ed", w + "d", w + "ing", w + w[-1] + "ed", w + w[-1] + "ing"}
        if w.endswith("e"):
            out.add(w[:-1] + "ing")
        if w.endswith("y"):
            out.add(w[:-1] + "ied")
    if pos in ("a", "s", "r"):
        out |= {w + "er", w + "est", w + "r", w + "st", w + w[-1] + "er", w + w[-1] + "est"}
        if w.endswith("y"):
            out |= {w[:-1] + "ier", w[:-1] + "iest"}
    return out


def _target_forms(st: _Store, tword: str, lemmas) -> frozenset:
    """Keys that stand for the tapped word itself: its spelling, its lemmas, their
    irregular forms (the forms table) and regular inflections. None of them is
    ever a clue or a signature word ("The trained dog watched the train")."""
    keys = {oewn.lookup_key(tword)}
    for lemma, pos in lemmas:
        k = oewn.lookup_key(lemma)
        keys.add(k.replace(" ", "_"))
        keys |= _inflections(k, pos)
    got = [oewn.lookup_key(l) for l, _ in lemmas]
    if got:
        q = ",".join("?" * len(got))
        keys |= {r[0] for r in st.conn.execute(
            "SELECT f.form_key FROM forms f JOIN entries e ON e.id = f.entry_id "
            f"WHERE e.lemma_key IN ({q})", got)}
    return frozenset(keys)


def pick_sense(conn, sentence: str, target: str, limit=None, explain: bool = False,
               at: int | None = None) -> list:
    """Rank the meanings of target (the word as it appears in sentence).

    at is the character offset of the tap in sentence, for a word that occurs
    more than once ("The bat flew out ... his cricket bat."); without it the
    first occurrence is used.

    Returns a list, best first, of dicts: synset_id, definition, score, clues
    (the sentence's words that supported this meaning, as written, strongest
    first), lemma, pos (n v a r) and rank (WordNet's order for that lemma).
    With explain=True each dict also has "links" ([{"clue", "matched", "via",
    "channel", "points"}]: the signature word each clue matched, where it came
    from and whether the clue matched it directly or through its own meanings;
    "matched" is None for an indirect link too weak to show) and "parts" (the
    clue score, rank prior, part-of-speech and collocation bonuses).
    """
    st = _store(conn)
    lex = st.lex
    tokens = tokenise(sentence)
    ti = find_target(tokens, target, at, lex)
    tword = tokens[ti][0] if ti >= 0 else target
    lemmas = lex.lemmas(tword)
    cands, seen, unfit = [], set(), st.unfit()
    for lemma, pos in lemmas:
        for sid, rank in st.synsets_of(lemma, pos):
            if sid not in seen and sid not in unfit:
                seen.add(sid)
                cands.append((sid, lemma, pos, rank))
    if not cands:
        return []
    drop = _target_forms(st, tword, lemmas)

    # clues: content words other than the target and its forms, one per lemma (closest kept)
    clue_by_key: dict = {}
    for j, (tok, _, _) in enumerate(tokens):
        if j == ti or is_stop(tok) or _is_name(st.conn, tokens, j):
            continue
        all_keys = lex.keys(tok)
        if any(k in drop for k in all_keys):
            continue                                  # "trained" when "train" is tapped
        keys = [k for k in all_keys if k not in STOP_WORDS]
        if not keys:
            continue
        dist = abs(j - ti) if ti >= 0 else 5
        dw = 1.0 / (1.0 + DIST_DECAY * max(dist - 1, 0))
        cur = clue_by_key.get(keys[0])
        if cur is None or dw > cur[2]:
            clue_by_key[keys[0]] = (tok, keys, dw)
    clues = list(clue_by_key.values())

    ids = [c[0] for c in cands]
    st.load(ids)
    st.book_words(ids)
    rels = st.relations(ids)
    st.load([d for sid in ids for _, d in rels[sid]])
    sigs = {sid: st.signature(sid, drop) for sid in ids}
    # very general words ("make", "old", "took") never link meanings indirectly
    linking = [[k for k in keys[:2] if k not in GENERIC] if INDIRECT else []
               for _, keys, _ in clues]
    expansions = {}
    st.prefetch([k for ks in linking for k in ks])
    for ks in linking:
        for k in ks:
            mean, per_sense = st.expand(k)
            expansions[k] = (mean, per_sense, mean.keys())
    needed = {k for _, keys, _ in clues for k in keys}
    for mean, _, _ in expansions.values():
        needed.update(mean)
    idf = st.idf_of(list(needed))
    cues = pos_cues(tokens, ti, lex, sentence) if ti >= 0 else {}
    top_cue = max(cues.items(), key=lambda kv: kv[1]) if cues else (None, 0.0)
    sent_keys = [{oewn.lookup_key(t), *lex.keys(t)} for t, _, _ in tokens]
    exs = st.example_tokens(ids) if ti >= 0 else {}
    tkeys = set(drop)
    initials = {k[:1] for k in tkeys}

    def is_target(w):
        if w in tkeys:
            return True
        if w[:1] not in initials or w in STOP_WORDS:
            return False
        return bool(set(lex.keys(w)) & tkeys)

    out = []
    for sid, lemma, pos, rank in cands:
        sig = sigs[sid]
        size_factor = min(1.0, (INDIRECT_SIG_REF / len(sig)) ** 0.5) \
            if INDIRECT_SIG_REF and sig else 1.0
        total, per_clue, links = 0.0, [], []
        for (tok, keys, dw), ks in zip(clues, linking):
            best, link = 0.0, None
            for k in keys:
                hit = sig.get(k)
                if hit and hit[0] * idf[k] > best:
                    best = hit[0] * idf[k]
                    link = {"clue": tok, "matched": k.replace("_", " "), "via": hit[1],
                            "channel": "direct"}
            ind, ind_link, ind_best = 0.0, None, 0.0
            for k in ks:
                mean, per_sense, mean_keys = expansions[k]
                by_sense: dict = {}
                avg = 0.0
                for ek in mean_keys & sig.keys():
                    hit = sig[ek]
                    ew = mean[ek]
                    base = hit[0] * idf[ek]
                    avg += base * ew
                    for idx, w in per_sense[ek]:
                        by_sense[idx] = by_sense.get(idx, 0.0) + base * w
                    if base * ew > ind_best:
                        ind_best = base * ew
                        # name the shared word only when it is in this meaning's own
                        # words and in the clue's own definition or synonyms
                        plain = hit[1] in ("definition", "synonym", "example") and \
                            max(w for _, w in per_sense[ek]) >= 1.0
                        ind_link = {"clue": tok,
                                    "matched": ek.replace("_", " ") if plain else None,
                                    "via": hit[1] + ", through the clue's own meaning",
                                    "channel": "indirect"}
                top = max(by_sense.values(), default=0.0)
                ind = max(ind, (1 - BEST_SENSE_MIX) * avg + BEST_SENSE_MIX * top)
            ind = min(INDIRECT * ind * size_factor, INDIRECT_MAX)
            channel = "direct"
            if ind > best:
                best, link = ind, ind_link
                channel = "indirect" if ind_link and ind_link["matched"] else "indirect_weak"
            best *= dw
            if best > 0:
                total += best
                per_clue.append((best, tok, channel, bool(GENERIC.intersection(keys))))
                if link:
                    link["points"] = round(best, 2)
                    links.append(link)
        p = "a" if pos in ("a", "s") else pos
        if top_cue[0] and p != top_cue[0] and cues.get(p, 0.0) < top_cue[1] - 0.5:
            # a clear part-of-speech cue for another part of speech ("his seal",
            # "very cross") weighs down this meaning's clues, not just adds a bonus
            total *= 1.0 - (1.0 - POS_OTHER) * min(top_cue[1], 1.0)
        prior = PRIOR / rank
        bonus = POS_BONUS * cues.get(p, 0.0)
        span = _collocation(sent_keys, ti, exs.get(sid, []), is_target) if ti >= 0 else 0
        colloc = COLLOCATION * (span - 2) if span else 0.0
        score = total + prior + bonus + colloc
        per_clue.sort(key=lambda x: -x[0])
        # shown to the child: clues that matched a word of this meaning directly, or
        # through their own meaning by a word we can name; never very general words
        shown = [t for v, t, ch, general in per_clue
                 if ch != "indirect_weak" and v >= MIN_CLUE and v >= CLUE_SHARE * total
                 and not general]
        item = {"synset_id": sid, "definition": st.info[sid][1], "score": round(score, 3),
                "clues": list(dict.fromkeys(shown)), "lemma": lemma, "pos": p, "rank": rank}
        if explain:
            links.sort(key=lambda x: -x["points"])
            item["links"] = links
            item["parts"] = {"clues": round(total, 3), "rank_prior": round(prior, 3),
                             "pos_bonus": round(bonus, 3), "collocation": round(colloc, 3)}
        out.append(item)
    out.sort(key=lambda x: (-x["score"], x["rank"]))
    return out[:limit] if limit else out
