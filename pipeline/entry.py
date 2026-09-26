"""Build the word screen the app shows when a child taps or looks up a word.

    from pipeline.context import open_english
    from pipeline.entry import word_entry
    conn = open_english()
    word_entry(conn, "bank", sentence="The little boat drifted towards the bank.")
    word_entry(conn, "bright", level="explorer")

    python3 -m pipeline.entry            # writes data/demo_bank.json and data/demo_bright.json
    python3 -m pipeline.entry WORD [--sentence S] [--level junior|explorer]   # prints one screen

The screen (a dict, JSON-ready)::

    {"headword": "bank", "tapped": "bank", "level": "junior",
     "part_of_speech": "noun", "pronunciation": {"GB": "bæŋk", ...},
     "sentence": "...", "chosen_from_sentence": true,
     "meanings": [
        {"synset_id": ..., "word": "bank", "part_of_speech": "noun",
         "definition": ..., "example": ... or null,
         "synonyms": [{"word": "riverbank", "sources": ["roget1911"]}, ...],
         "antonyms": [{"word": ..., "source": "oewn2025"}],
         "related": [{"word": "riverbank", "relation": "more specific", ...}],
         "clues": ["boat", "drifted"],                   # first meaning, with a sentence
         "clue_links": [{"clue": "boat", "matched": "water", "via": ...,
                         "channel": "indirect"}],
         "fit_score": 12.758, "margin_over_next": 4.148, # first meaning, with a sentence
         "folded": false, "source": "oewn2025"},
        {... "folded": true}, ...],
     "more_meanings": 3,          # meanings left off the junior screen
     "sources": {"oewn2025": "<attribution>", ...}}

Rules:

* With a sentence, the meaning pick_sense puts first opens the screen, with
  its clues, its score and its lead over the runner-up; the other meanings
  follow in WordNet's order (noun, verb, adjective, adverb, each by sense
  rank), which is also the whole order without a sentence. Only the first
  meaning is open; the others are folded.
* A meaning with fewer than three synonyms also gets up to four "related"
  words: more specific ones (hyponyms) and more general ones (hypernyms).
* Junior shows at most JUNIOR_MEANINGS meanings and 6 synonyms per meaning,
  and only synonyms of one or two words; Explorer shows every meaning, 10
  synonyms and phrases up to four words.
* Synonyms belong to that meaning only (sense_synonyms of its synset). They
  are ranked by how many sources give them (WordNet counts most), how
  familiar the word is (words with more WordNet senses are usually commoner),
  and the old-book alignment score; shorter wins ties. The headword itself,
  words in kid_filter and, by default, American spellings are left out.
* Meanings in kid_filter (offensive or modern slang) are never shown.
* Every definition, example, synonym and antonym carries its source id.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

from pipeline import oewn
from pipeline.context import lexicon, open_english, pick_sense
from pipeline.pgtext import mentions_gutenberg

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

LEVELS = {"junior": {"synonyms": 6, "max_words": 2, "meanings": 5},
          "explorer": {"synonyms": 10, "max_words": 4, "meanings": None}}
JUNIOR_MEANINGS = LEVELS["junior"]["meanings"]
POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}
SOURCE_WEIGHT = {"oewn2025": 3.0, "soule1871": 1.0, "roget1911": 1.0}

DEMO_BANK_SENTENCE = ("The little boat drifted slowly towards the bank, "
                      "where the ducks were waiting.")


# --------------------------------------------------------------------------- helpers

def _unfit(conn) -> tuple:
    """(unfit synset ids, unfit word keys) from kid_filter, cached per connection.

    A database without kid_filter is an error, never an empty filter: the
    children's filter must not switch itself off."""
    cache = getattr(_unfit, "_cache", {})
    got = cache.get(id(conn))
    if got is None or got[0] is not conn:
        syn, wrd = set(), set()
        try:
            rows = conn.execute("SELECT kind, key FROM kid_filter").fetchall()
        except sqlite3.OperationalError as e:
            raise RuntimeError("this database has no kid_filter table; word screens need "
                               "data/english.sqlite from python3 -m pipeline.build") from e
        for kind, key in rows:
            (syn if kind == "synset" else wrd).add(key)
        got = cache[id(conn)] = (conn, syn, wrd)
        _unfit._cache = cache
    return got[1], got[2]


def _american_only(conn, keys) -> set:
    """Word keys WordNet marks as American spellings and never as British."""
    keys = list(keys)
    if not keys:
        return set()
    q = ",".join("?" * len(keys))
    marks: dict = {}
    for k, mark in conn.execute(
            "SELECT e.lemma_key, d.lemma FROM entries e JOIN senses s ON s.entry_id = e.id "
            "JOIN sense_relations r ON r.src_sense = s.id AND r.rel = 'exemplifies' "
            "JOIN senses d ON d.id = r.dst_sense "
            f"WHERE e.lemma_key IN ({q}) AND d.lemma IN ('American spelling', 'British spelling')",
            keys):
        marks.setdefault(k, set()).add(mark)
    return {k for k, m in marks.items() if m == {"American spelling"}}


def _familiarity(conn, keys) -> dict:
    """Word key -> number of WordNet senses (a rough stand-in for how common it is)."""
    keys = list(keys)
    out = {k: 0 for k in keys}
    if keys:
        q = ",".join("?" * len(keys))
        for k, n in conn.execute(
                "SELECT e.lemma_key, COUNT(*) FROM entries e JOIN senses s ON s.entry_id = e.id "
                f"WHERE e.lemma_key IN ({q}) GROUP BY e.lemma_key", keys):
            out[k] = n
    return out


def synonyms_for(conn, synset_id: str, headword: str, level: str = "junior",
                 spelling: str = "british") -> list:
    """Synonyms of one meaning, best first: [{"word", "sources"}]."""
    cfg = LEVELS[level]
    _, bad_words = _unfit(conn)
    rows = conn.execute(
        "SELECT word, word_key, source, score FROM sense_synonyms WHERE synset_id = ?",
        (synset_id,)).fetchall()
    own = oewn.lookup_key(headword)
    words: dict = {}
    for word, key, source, score in rows:
        if key == own or key in bad_words:
            continue
        if len(key.replace("-", " ").split()) > cfg["max_words"]:
            continue
        w = words.setdefault(key, {"word": word, "sources": {}, "score": 0.0})
        if source == "oewn2025":
            w["word"] = word                      # WordNet's spelling and case win
        w["sources"][source] = max(score, w["sources"].get(source, 0.0))
    if spelling == "british":
        for k in _american_only(conn, words):
            words.pop(k, None)
    fam = _familiarity(conn, words)
    ranked = []
    for key, w in words.items():
        agree = sum(SOURCE_WEIGHT.get(s, 1.0) * (1.0 if s == "oewn2025" else min(sc, 3.0) / 3.0 + 0.5)
                    for s, sc in w["sources"].items())
        score = agree + 0.6 * math.log1p(fam[key]) - 0.4 * (key.count(" ") + key.count("-"))
        if w["word"][:1].isupper() and not headword[:1].isupper():
            score -= 1.0                          # proper names rarely help a child
        ranked.append((-score, len(key), key, w))
    ranked.sort()
    return [{"word": w["word"], "sources": sorted(w["sources"])}
            for _, _, _, w in ranked[:cfg["synonyms"]]]


def _british_variants(word: str) -> list:
    """Likely British spellings of an American one (color -> colour, center -> centre)."""
    out = []
    if "or" in word:
        out.append(word.replace("or", "our", 1))
    if word.endswith("er"):
        out.append(word[:-2] + "re")
    if "gray" in word:
        out.append(word.replace("gray", "grey"))
    if word.endswith("og"):
        out.append(word + "ue")
    return out


def _british_form(conn, synset_id: str, word: str) -> str:
    """word, or its British spelling when the same synset has one. WordNet marks
    only some spellings, so a few regular patterns (color/colour) are tried too."""
    members = [r[0] for r in conn.execute(
        "SELECT lemma FROM senses WHERE synset_id = ? ORDER BY member_rank", (synset_id,))]
    for v in _british_variants(word):
        if v in members:
            return v
    if not _american_only(conn, [oewn.lookup_key(word)]):
        return word
    for (lemma,) in conn.execute(
            "SELECT s.lemma FROM senses s JOIN sense_relations r ON r.src_sense = s.id "
            "AND r.rel = 'exemplifies' JOIN senses d ON d.id = r.dst_sense "
            "WHERE s.synset_id = ? AND d.lemma = 'British spelling' ORDER BY s.member_rank",
            (synset_id,)):
        return lemma
    return word


def related_for(conn, synset_id: str, lemma: str, limit: int = 4) -> list:
    """A few nearby words for a meaning with few synonyms: more specific words
    (hyponyms, "riverbank" for the river bank) and more general ones
    (hypernyms, "slope"), each the first word of its synset and one word long.
    [{"word", "relation", "source"}]."""
    bad_syn, bad_words = _unfit(conn)
    own = oewn.lookup_key(lemma)
    out, seen = [], {own}
    for rel, label in (("hyponym", "more specific"), ("hypernym", "more general")):
        for dst, word, key in conn.execute(
                "SELECT r.dst, s.lemma, e.lemma_key FROM synset_relations r "
                "JOIN senses s ON s.synset_id = r.dst AND s.member_rank = 1 "
                "JOIN entries e ON e.id = s.entry_id "
                "WHERE r.src = ? AND r.rel = ? ORDER BY r.dst", (synset_id, rel)):
            if key in seen or key in bad_words or dst in bad_syn or " " in key:
                continue
            if word[:1].isupper() and not lemma[:1].isupper():
                continue
            seen.add(key)
            out.append({"word": _british_form(conn, dst, word), "relation": label,
                        "source": "oewn2025"})
            if len(out) >= limit:
                return out
    return out


def antonyms_for(conn, synset_id: str, lemma: str) -> list:
    """Antonyms of this word in this meaning (WordNet; for adjectives also the
    opposites of the head adjective it is 'similar' to)."""
    row = conn.execute(
        "SELECT s.id FROM senses s JOIN entries e ON e.id = s.entry_id "
        "WHERE s.synset_id = ? AND e.lemma_key = ? ORDER BY e.lemma = ? DESC LIMIT 1",
        (synset_id, oewn.lookup_key(lemma), lemma)).fetchone()
    if not row:
        return []
    bad_syn, bad_words = _unfit(conn)
    out, seen = [], set()
    for a in oewn.antonyms(conn, row[0], indirect=True):
        k = oewn.lookup_key(a["lemma"])
        if k in seen or k in bad_words or a["synset_id"] in bad_syn:
            continue
        seen.add(k)
        out.append({"word": _british_form(conn, a["synset_id"], a["lemma"]),
                    "source": a["source"]})
    return out[:4]


def _example(conn, synset_id: str):
    row = conn.execute("SELECT text, source FROM examples WHERE synset_id = ? ORDER BY seq LIMIT 1",
                       (synset_id,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _pronunciation(conn, lemma: str, pos: str) -> dict:
    out = {}
    for variety, ipa in conn.execute(
            "SELECT p.variety, p.ipa FROM entries e JOIN pronunciations p ON p.entry_id = e.id "
            "WHERE e.lemma_key = ? AND e.pos IN (%s) ORDER BY e.lemma = ? DESC, e.id"
            % ",".join("?" * len(oewn.normalise_pos(pos))),
            (oewn.lookup_key(lemma), *oewn.normalise_pos(pos), lemma)):
        out.setdefault(variety or "any", ipa)
    return out


def _sources(conn, used) -> dict:
    """source id -> the attribution to show with it (for WordNet, the licence line
    with its creator and copyright, and the Princeton WordNet notice)."""
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    oewn_text = " ".join(x for x in (meta.get("attribution"), meta.get("princeton_notice")) if x)
    text = {"oewn2025": oewn_text or "Open English WordNet (CC BY 4.0)",
            "soule1871": meta.get("attribution_soule1871", "Soule (1871), public domain"),
            "roget1911": meta.get("attribution_roget1911", "Roget (1911), public domain")}
    return {s: text[s] for s in sorted(used) if s in text}


# --------------------------------------------------------------------------- the screen

def word_entry(conn, word: str, sentence: str | None = None, level: str = "junior",
               spelling: str = "british", at: int | None = None) -> dict:
    """The app's word screen for word (as tapped or typed), as a dict.

    With a sentence, the meaning that fits it comes first, with the clues that
    chose it; the other meanings are folded. at is the character offset of the
    tap in sentence, for a word that occurs twice. level is "junior" or
    "explorer".
    """
    if level not in LEVELS:
        raise ValueError(f"level must be junior or explorer, not {level!r}")
    cfg = LEVELS[level]
    bad_syn, _ = _unfit(conn)
    tapped = word.strip()
    ranked = []
    if sentence:
        ranked = [dict(r) for r in pick_sense(conn, sentence, tapped, explain=True, at=at)]
    # every meaning in WordNet's order: noun, verb, adjective, adverb, each by rank
    in_order, seen = [], set()
    for lemma, pos in lexicon(conn).lemmas(tapped):
        for s in oewn.senses_for(conn, lemma, pos):
            if s["synset_id"] in seen or s["synset_id"] in bad_syn:
                continue
            seen.add(s["synset_id"])
            in_order.append({"synset_id": s["synset_id"], "definition": s["definition"],
                             "lemma": s["lemma"], "pos": s["pos"], "rank": s["rank"]})
    order = {"n": 0, "v": 1, "a": 2, "r": 3}
    in_order.sort(key=lambda r: order.get(r["pos"], 9))    # stable: keeps rank order
    sentence_used = bool(ranked)
    if ranked:
        # the meaning that fits the sentence first, then the rest in WordNet's order
        best = ranked[0]
        best["margin"] = round(best["score"] - ranked[1]["score"], 3) if len(ranked) > 1 else None
        ranked = [best] + [r for r in in_order if r["synset_id"] != best["synset_id"]]
    else:
        ranked = in_order
    if not ranked:
        return {"headword": tapped, "tapped": tapped, "level": level, "found": False,
                "part_of_speech": None, "sentence": sentence, "chosen_from_sentence": False,
                "meanings": [], "more_meanings": 0, "sources": {}}

    limit = cfg["meanings"] or len(ranked)
    shown = ranked[:limit]
    used = set()
    meanings = []
    for i, r in enumerate(shown):
        ex, ex_src = _example(conn, r["synset_id"])
        syns = synonyms_for(conn, r["synset_id"], r["lemma"], level, spelling)
        ants = antonyms_for(conn, r["synset_id"], r["lemma"])
        used.add("oewn2025")
        for s in syns:
            used.update(s["sources"])
        m = {"synset_id": r["synset_id"], "word": r["lemma"],
             "part_of_speech": POS_NAMES.get(r["pos"], r["pos"]),
             "definition": r["definition"], "example": ex,
             "synonyms": syns, "antonyms": ants, "folded": i > 0, "source": "oewn2025"}
        if ex is not None:
            m["example_source"] = ex_src
        if len(syns) < 3:
            m["related"] = related_for(conn, r["synset_id"], r["lemma"])
        if i == 0 and sentence_used:
            m["clues"] = r.get("clues", [])
            # a link is shown with the word it matched only when that word can be
            # named (an indirect link too weak to name has matched = None)
            m["clue_links"] = [{k: l[k] for k in ("clue", "matched", "via", "channel")
                                if l.get(k) is not None}
                               for l in r.get("links", []) if l["clue"] in m["clues"]]
            m["fit_score"] = r["score"]
            m["margin_over_next"] = r.get("margin")
        meanings.append(m)
    first = shown[0]
    return {
        "headword": first["lemma"],
        "tapped": tapped,
        "level": level,
        "found": True,
        "part_of_speech": POS_NAMES.get(first["pos"], first["pos"]),
        "pronunciation": _pronunciation(conn, first["lemma"], first["pos"]),
        "sentence": sentence,
        "chosen_from_sentence": sentence_used,
        "meanings": meanings,
        "more_meanings": len(ranked) - len(shown),
        "sources": _sources(conn, used),
    }


def write_json(obj, path: Path) -> Path:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    if mentions_gutenberg(text):
        raise ValueError(f"{path} would mention the forbidden word")
    path.write_text(text, encoding="utf-8")
    return path


def write_demos(conn, out_dir: Path = DATA) -> list:
    """data/demo_bank.json (boat sentence, junior) and data/demo_bright.json (explorer)."""
    bank = word_entry(conn, "bank", sentence=DEMO_BANK_SENTENCE, level="junior")
    bright = word_entry(conn, "bright", level="explorer")
    return [write_json(bank, out_dir / "demo_bank.json"),
            write_json(bright, out_dir / "demo_bright.json")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print a word screen or write the demo files.")
    ap.add_argument("word", nargs="?")
    ap.add_argument("--sentence")
    ap.add_argument("--level", default="junior", choices=sorted(LEVELS))
    ap.add_argument("--at", type=int, help="character offset of the tap in the sentence")
    ap.add_argument("--db")
    args = ap.parse_args(argv)
    conn = open_english(args.db)
    if args.word:
        print(json.dumps(word_entry(conn, args.word, args.sentence, args.level, at=args.at),
                         indent=2, ensure_ascii=False))
    else:
        for p in write_demos(conn):
            print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
