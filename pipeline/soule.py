"""Parse Richard Soule's *A Dictionary of English Synonymes* (1871) into JSON lines.

Run from the project root:

    python3 -m pipeline.soule            # writes data/soule.jsonl and prints a report

Every record carries ``"source": "soule1871"`` and the 1-based ``line`` of the
book text (as returned by :func:`pipeline.pgtext.book_text`) where the headword
is printed. One record per line::

    {"headword": "affection", "pos": "n", "pos_raw": "n.",
     "senses": [{"n": 1, "synonyms": [...], "xrefs": [...],
                 "labels": [...], "notes": [...]}, ...],
     "source": "soule1871", "line": 1967}

How the e-text is laid out (checked by eye):

* An entry opens a paragraph with ``~Headword~, _pos._`` followed by synonyms.
  Numbered senses are ``~1.~``, ``~2.~`` ...; sense 1 usually shares the entry
  paragraph and later senses start their own paragraphs. Single-sense entries
  have no number (stored as ``n = 1``).
* Phrase headwords often have no part of speech (``~Abide by.~ Act up to, ...``);
  their ``pos`` and ``pos_raw`` are ``null`` unless the entry is marked
  ``(_Active._)``/``(_Neuter._)`` (Soule's verb voices), which gives ``pos = "v"``.
* Words in CAPITALS are cross-references to other headwords. They are kept as
  synonyms (lower-cased) and also listed in ``xrefs``.
* ``(...)`` and ``[...]`` hold usage labels (``(_Med._)``, ``[_Colloquial._]``,
  ``[Fr.]``), glosses (``(_upon oath_)``), Latin names and spelling notes. None
  of it becomes a synonym: known usage/subject/language labels go to
  ``labels`` and everything else, markup stripped, to ``notes``. Labels and
  notes printed before the first sense number belong to the whole entry and
  are copied into every sense.
* A tag naming two parts of speech (``_n. & a._``) yields one record per part
  of speech, with the same senses and the same ``pos_raw``.
* Soule capitalises the first word of every headword and sense; it is
  lower-cased unless the book shows it is a proper word (see :func:`decap`).

``pos`` is one of n, adj, v, adv, prep, conj, interj, plus pron and art for the
handful of pronoun/article entries, or null.

The e-text has a few printing slips that are repaired and counted in the report
(mis-set sense numbers like ``2~.~``, a headword run into the paragraph before,
synonyms mis-set in italic, half-italic tags like ``n_._``).
"""
from __future__ import annotations

import bisect
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.pgtext import book_text, mentions_gutenberg

SOURCE_ID = "soule1871"
BOOK = "pg38390.txt"
OUT = Path(__file__).resolve().parent.parent / "data" / "soule.jsonl"

POS_VALUES = ("n", "adj", "v", "adv", "prep", "conj", "interj", "pron", "art")

# --------------------------------------------------------------------------
# Labels: Soule's explanatory table plus the register, region, language and
# verb-voice labels printed in brackets and parentheses. Matched without the
# final period and ignoring case; anything else in brackets is a note.
# --------------------------------------------------------------------------
_LABEL_TERMS = """
Alg|Algebra|Anat|Arch|Archery|Arith|Astrol|Astron|Bot|Carpentry|Chem|Com|Conch|
Cookery|Eccl|Elocution|Eng|Ent|Farriery|Fort|Geog|Geol|Geom|Gram|Her|Ich|Law|
Common Law|Eng. Law|International Law|Legislation|Logic|Masonry|Math|Mech|Med|
Meteor|Mil|Min|Mining|Music|Mus|Myth|Mythol|Hindoo Mythol|Naut|Optics|Ornith|
Orthoëpy|Painting|Photography|Physics|Printing|In printing|Rhet|Sculp|
Ship building|Surg|Surgery|Surveying|Theol|Zoöl|
Fr|L|Low L|Gr|It|Sp|Port|
Colloquial|Rare|Low|Poetical|Vulgar|Inelegant|Obsolete|Nearly obsolete|
Antiquated|Archaic|Modern|Recent|Local|Local Eng|Cant|Cant term|Cant word|
Childish term|Childish terms|Child's word|Ludicrous|In contempt|In derision|
Familiarly|Sailor's term|Sailor's phrase|Commercial term|Eng. politics|
Scottish|Scotch|Scotland|Irish|Chinese|England|New England|Pennsylvania|
U. S|Western|Southern U. S|
Active|Neuter|Passive|Adjectively|Adverbially|Imperatively
"""
LABELS = {t.strip().lower(): t.strip() for t in _LABEL_TERMS.replace("\n", "").split("|") if t.strip()}
LABELS["zool"] = "Zoöl"
VOICE_LABELS = {"Active.", "Neuter.", "Passive."}

# --------------------------------------------------------------------------
# Regular expressions for the e-text markup (~bold~, _italic_).
# --------------------------------------------------------------------------
# A sense number: "~2.~", "~ 2.~" (group 1) and the mis-set "2~.~" (group 2)
# or paragraph-initial "2.~" (group 3).
SENSE_RE = re.compile(r"~\s*(\d{1,2})\s*\.\s*~|(?<![\w~])(\d{1,2})\s*~\s*\.\s*~|^(\d{1,2})\.~")
# A headword run into the previous entry's paragraph: "... quickening. ~Revive~, _v. a._"
_POS_WORD = r"(?:n|a|v|ad|adv|prep|conj|interj|pron|art|p|part)"
HW_MID_RE = re.compile(
    r"~([^~\d\s][^~]*)~(?=\s*,?\s*(?:\[[^\]]*\]\s*)?_\s*" + _POS_WORD + r"\s*[.,_])"
)
# "waver.~~Pave the way~, ..." -- a headword glued to the end of a sense.
HW_GLUED_RE = re.compile(r"(?<=\.)~(?=~[^~\d\s][^~]*~)")
# The inside of an italic part-of-speech tag: "n.", "v. a. & n.", "pron., sing. & pl.".
POS_TAG_RE = re.compile(
    r"\s*(?:(?:n|a|v|ad|adv|prep|conj|interj|pron|art|p|part|pl|sing|i)\s*[.,]*\s*(?:&|and|or)?\s*)+"
)
# Bare or half-italic tags: "a. ~1.~", "n_._", "v_. n._".
BARE_POS_RE = re.compile(r"(?:v_\. [an]\._|(?:n|a|v|ad|prep|conj|interj|pron|pl)(?:\.|_\._))(?=\s|~|$)")
ITALIC_RE = re.compile(r"_([^_]*)_")
WORD_RE = re.compile(r"[^\W\d_]+")
LETTER_HEADING_RE = re.compile(r"[A-Z]")
# "hold. opine" -- a full stop printed for a comma between two synonyms.
_STRAY_STOP_RE = re.compile(r"(?<=[^\W\d_]{3})\. (?=[^\W\d_])")
_ABBREV_BEFORE_STOP = {"nem"}


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------
@dataclass
class Sense:
    n: int
    synonyms: list = field(default_factory=list)
    xrefs: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    review: list = field(default_factory=list)   # shorthand runs left unsplit (see expand_shorthand)
    raw: str = ""
    italic_fallback: bool = False
    from_first: list = field(default_factory=list)  # synonyms built from the sense's first item
    unbalanced: bool = False


@dataclass
class Header:
    headword: str
    pos_raw: str | None
    first_n: int | None
    labels: list
    notes: list
    body: str
    voice: str | None
    repairs: list


@dataclass
class Entry:
    headword: str
    pos_raw: str | None
    line: int
    labels: list = field(default_factory=list)   # entry-level, copied to every sense
    notes: list = field(default_factory=list)
    senses: list = field(default_factory=list)
    voice: str | None = None                      # "Active." / "Neuter." on phrasal verbs
    text: str = ""                                # the printed paragraph(s), for reports


@dataclass
class ParseResult:
    entries: list
    failures: list          # (line, reason, text): candidate paragraphs that gave nothing
    repairs: list           # (line, kind, text): printing slips worked around
    stats: Counter


# --------------------------------------------------------------------------
# Paragraphs
# --------------------------------------------------------------------------
def paragraphs(text: str):
    """Yield (first_line, joined_text, line_offsets) for each blank-line separated paragraph.

    Lines are joined with a space, except after a line-final hyphen: this e-text
    keeps real hyphens only ("school-\\nboy" is "school-boy"), so no space is added.
    ``line_offsets`` is a list of (offset_in_joined_text, line_number).
    """
    buf, offsets, first = "", [], None
    for no, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if not s:
            if buf:
                yield first, buf, offsets
            buf, offsets, first = "", [], None
            continue
        if not buf:
            first = no
        elif not buf.endswith("-"):
            buf += " "
        offsets.append((len(buf), no))
        buf += s
    if buf:
        yield first, buf, offsets


def _line_at(offsets, pos: int) -> int:
    i = bisect.bisect_right([o for o, _ in offsets], pos) - 1
    return offsets[max(i, 0)][1]


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def strip_markup(s: str) -> str:
    """Drop italic (_) and bold (~) markers."""
    return _squash(s.replace("_", " ").replace("~", " ")).replace(" .", ".").replace(" ,", ",")


# --------------------------------------------------------------------------
# Labels, notes and bracketed groups
# --------------------------------------------------------------------------
def _canon_labels(part: str) -> list | None:
    """'Colloquial U. S.' -> ['Colloquial.', 'U. S.']; None unless every word belongs to a label."""
    p = re.sub(r"\bU\.\s*S\b\.?", "U. S.", _squash(part))
    words, out, i = p.split(" "), [], 0
    while i < len(words):
        for j in range(len(words), i, -1):
            canon = LABELS.get(" ".join(words[i:j]).rstrip(".").lower())
            if canon:
                out.append(canon + ".")
                i = j
                break
        else:
            return None
    return out


def classify_group(content: str):
    """Classify the inside of (...) or [...] or _..._: returns (labels, notes)."""
    text = strip_markup(content).strip(" ,;")
    if not text:
        return [], []
    labels = []
    for part in re.split(r",\s*|\s+and\s+|\s*&\s*", text.rstrip(".")):
        if not part.strip():
            continue
        found = _canon_labels(part)
        if found is None:
            return [], [text]
        labels += [x for x in found if x not in labels]
    return labels, []


def split_groups(text: str):
    """Remove top-level (...) and [...] groups; return (remaining_text, groups, unbalanced)."""
    out, groups, depth, cur, opener = [], [], 0, [], None
    closer = {"(": ")", "[": "]"}
    unbalanced = False
    for ch in text:
        if depth == 0:
            if ch in closer:
                depth, opener, cur = 1, ch, []
                out.append(" ")
            elif ch in ")]":
                unbalanced = True
            else:
                out.append(ch)
        else:
            if ch == opener:
                depth += 1
            elif ch == closer[opener]:
                depth -= 1
                if depth == 0:
                    groups.append("".join(cur))
                    continue
            cur.append(ch)
    if depth:
        unbalanced = True
        groups.append("".join(cur))
    return "".join(out), groups, unbalanced


def _take_group(s: str):
    """s starts with ( or [: return (content, rest) for the balanced group."""
    opener, closer = s[0], {"(": ")", "[": "]"}[s[0]]
    depth = 0
    for i, ch in enumerate(s):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return s[1:i], s[i + 1:]
    return s[1:], ""


# --------------------------------------------------------------------------
# Senses
# --------------------------------------------------------------------------
def caps_runs(item: str):
    """Runs of all-capital words (cross-references) in a synonym, as printed.

    "AT A LOSS" and "À LA MODE" are one run each; a lone "A" or "I" is not a reference.
    """
    runs, cur = [], []

    def close():
        if any(sum(c.isalpha() for c in t) >= 2 for t in cur):
            runs.append(" ".join(cur))
        cur.clear()

    for tok in item.split(" "):
        letters = [c for c in tok if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            cur.append(tok)
        else:
            close()
    close()
    return runs


def _split_items(text: str):
    """Split a sense at commas and semicolons (and at a stray full stop between words)."""
    items = []
    for part in re.split(r"\s*[,;]\s*", text):
        last = 0
        for m in _STRAY_STOP_RE.finditer(part):
            word = WORD_RE.findall(part[:m.start()])[-1]
            if word.lower() in _ABBREV_BEFORE_STOP or '"' in part:
                continue
            items.append(part[last:m.start()])
            last = m.end()
        items.append(part[last:])
    return items


def parse_sense_text(n: int, raw: str, headword: str = "") -> Sense:
    """Parse the text of one sense (without its number) into synonyms, xrefs, labels, notes."""
    sense = _parse_sense(n, raw, italic_is_gloss=True, headword=headword)
    if not sense.synonyms and ITALIC_RE.search(split_groups(raw)[0]):
        # "~2.~ _Capacity_, _room_." -- synonyms mis-set in italic.
        sense = _parse_sense(n, raw, italic_is_gloss=False, headword=headword)
        sense.italic_fallback = bool(sense.synonyms)
    return sense


def _parse_sense(n: int, raw: str, italic_is_gloss: bool, headword: str = "") -> Sense:
    sense = Sense(n=n, raw=raw)
    text = re.sub(r"~([^~]*)~", r"\1", raw)          # in-body bold, e.g. ~NOTORIETY~
    m = re.fullmatch(r"\s*of ([A-Z][A-Z' -]+)\.?\s*", text)
    if m:
        # "~Children~, _pl._ of CHILD." points at another headword; it lists no synonyms.
        sense.notes.append(strip_markup(text).rstrip("."))
        sense.xrefs.append(m.group(1).strip().lower())
        return sense
    text, groups, sense.unbalanced = split_groups(text)
    glosses = list(groups)
    for g in groups:
        labels, notes = classify_group(g)
        sense.labels += labels
        sense.notes += notes
    if italic_is_gloss:
        # Italic outside brackets is a gloss or label, never a synonym.
        for g in ITALIC_RE.findall(text):
            glosses.append(g)
            labels, notes = classify_group(g)
            sense.labels += labels
            sense.notes += notes
        text = ITALIC_RE.sub(" ", text)
    text = _squash(text.replace("~", " ").replace("_", " "))
    if text.endswith("."):
        text = text[:-1]
    kept, first = [], []
    for part_no, part in enumerate(text.split(";")):
        # a semicolon closes a run of shared words: "Retort; smart, or witty reply"
        items = [_squash(i).strip(" .:") for i in _split_items(part)]
        items = [i for i in items if i]
        k, review, f = expand_shorthand(items, headword)
        kept += k
        first += [x and part_no == 0 for x in f]
        sense.review += review
    for item, from_first in zip(kept, first):
        for run in caps_runs(item):
            x = run.strip(".,;:!?").lower()
            if x and x not in sense.xrefs:
                sense.xrefs.append(x)
            item = item.replace(run, run.lower(), 1)
        if item not in sense.synonyms:
            sense.synonyms.append(item)
            if from_first:
                sense.from_first.append(item)
    # capitals inside a gloss are cross-references too: "(or SHELTY)", "_LAY_"
    for g in glosses:
        for run in caps_runs(strip_markup(g)):
            x = run.strip(".,;:!?()").lower()
            if x and not _canon_labels(run) and x not in sense.xrefs:
                sense.xrefs.append(x)
    return sense


# "and so forth" is a phrase of its own, not the last member of a list
_KEEP_CONJ = {"and so forth", "and so on", "and the like", "and others"}
_CONJ_RE = re.compile(r"^(?:or|and)\s+(\S.*)$")
# a coordinated tail that starts like this continues the item before it
_FRAGMENT_START = {"with", "about", "in", "of", "to", "by", "for", "from", "on", "at",
                   "into", "upon", "which"}


def expand_shorthand(items: list, headword: str = ""):
    """Undo Soule's shared-word shorthand in the comma items of one sense.

    Soule often lets several words share the words around them:

    * "not rash, heedless, or headlong" means not rash, *not* heedless, not
      headlong, and "Be defeated, frustrated, or overthrown" means be
      frustrated, be overthrown. When a phrase is followed by single words that
      end in an "or"/"and" word (or in "&c."), the phrase's leading words are
      given to each of them.
    * "Gentle, mild, or soft breeze" (single words followed by a phrase joined
      with "or") shares the phrase's last words, but where the shared part
      starts cannot be told from the text alone ("Chance, fit, suitable, or
      favorable time"). Such a run is not split: it goes to ``review``.
    * A plain list ("Color, hue, or tint", "rubicund, or a red color") only
      loses its "or"; "and so forth"
      keeps its "and". A run ending in the headword itself ("here, there, and
      everywhere" under Everywhere) is a set phrase and goes to ``review``, as
      does an "or" tail that is only a fragment ("or with scratches").

    Returns (items kept, review texts, [bool: item built from the first item]).
    """
    out = list(items)
    first = [i == 0 for i in range(len(items))]
    review = []

    def single(x):
        return " " not in x

    def is_amp(x):
        return x.lower() in ("&c", "etc")

    def conj(x):
        return None if x.lower() in _KEEP_CONJ else _CONJ_RE.match(x)

    for k, item in enumerate(items):
        m = conj(item) if k else None
        if not m and not is_amp(item):
            continue
        j0 = k
        while j0 > 0 and out[j0 - 1] is not None and single(items[j0 - 1]) \
                and not is_amp(items[j0 - 1]) and not conj(items[j0 - 1]):
            j0 -= 1
        run = list(range(j0, k))
        p = j0 - 1
        phrase = p >= 0 and out[p] is not None and not single(items[p]) \
            and not is_amp(items[p]) and not conj(items[p])
        if is_amp(item):
            out[k] = None
            if run and phrase:
                prefix = items[p].rsplit(" ", 1)[0]
                for j in run:
                    out[j], first[j] = f"{prefix} {items[j]}", p == 0
            continue
        tail = m.group(1)
        if single(tail) and phrase:
            if tail.lower() == headword.lower():
                review.append(", ".join(items[j0:k + 1]))
                for j in run + [k]:
                    out[j] = None
                continue
            prefix = items[p].rsplit(" ", 1)[0]
            for j in run:
                out[j], first[j] = f"{prefix} {items[j]}", p == 0
            out[k], first[k] = f"{prefix} {tail}", p == 0
        elif not single(tail) and run and tail.split()[0].lower() not in ("a", "an"):
            review.append(", ".join(items[j0:k + 1]))
            for j in run + [k]:
                out[j] = None
        elif tail.split()[0].lower() in _FRAGMENT_START:
            review.append(item)
            out[k] = None
        else:
            out[k] = tail
    kept = [(x, f) for x, f in zip(out, first) if x]
    return [x for x, _ in kept], review, [f for _, f in kept]


def split_senses(body: str, first_n: int | None):
    """Split a run of entry text at sense numbers: returns [(n, text)]."""
    out, last, cur_n = [], 0, first_n
    for m in SENSE_RE.finditer(body):
        chunk = body[last:m.start()]
        if chunk.strip():
            out.append((cur_n or 1, chunk.strip()))
        cur_n, last = int(m.group(1) or m.group(2) or m.group(3)), m.end()
    chunk = body[last:]
    if chunk.strip():
        out.append((cur_n or 1, chunk.strip()))
    return out


# --------------------------------------------------------------------------
# Entry headers
# --------------------------------------------------------------------------
def parse_header(seg: str) -> Header:
    """Parse '~Headword~, _pos._ [labels] ~1.~ ...' up to the first synonym."""
    m = re.match(r"~([^~]*)~", seg)
    hw = m.group(1)
    rest = seg[m.end():]
    labels, notes, pos_parts, repairs = [], [], [], []
    voice, first_n = None, None
    # "~Agreeable to, 1.~" -- the first sense number printed inside the headword.
    mn = re.search(r",?\s*(\d{1,2})\.\s*$", hw)
    if mn:
        first_n, hw = int(mn.group(1)), hw[:mn.start()]
        repairs.append("sense number inside headword")
    hw = hw.strip().rstrip(",").strip()
    hw_body, hw_groups, _ = split_groups(hw)          # "Play the devil (or the deuce) with"
    notes += [strip_markup(g) for g in hw_groups]
    hw = strip_markup(hw_body)                         # "~_Cetacean_~"
    if hw.endswith(".") and "." not in hw[:-1]:       # "Abide by." but "Nem. con."
        hw = hw[:-1].strip()

    while True:
        rest = rest.lstrip(" ,")
        if rest.startswith("_"):
            close = rest.find("_", 1)
            inner = rest[1:close] if close > 0 else ""
            if close > 0 and inner.strip() and POS_TAG_RE.fullmatch(inner):
                pos_parts.append(inner.strip())
                rest = rest[close + 1:]
                if rest.startswith("."):                  # "_v. a_."
                    pos_parts[-1] += "."
                    rest = rest[1:]
                continue
            break
        cm = re.match(r"(or|and|&)\s+(?=_)", rest)        # "_v. a._ & _n._", "_a._ or _pron._"
        if pos_parts and cm:
            pos_parts.append(cm.group(1))
            rest = rest[cm.end():]
            continue
        bm = BARE_POS_RE.match(rest)
        if bm:
            pos_parts.append(bm.group(0).replace("_", ""))
            rest = rest[bm.end():]
            repairs.append("part of speech not (fully) in italic")
            continue
        if rest[:1] in ("(", "["):
            content, after = _take_group(rest)
            mm = re.fullmatch(r"\s*_([^_]+)_\s*missing\?\s*", content)
            if not pos_parts and mm and POS_TAG_RE.fullmatch(mm.group(1)):
                pos_parts.append(mm.group(1).strip())     # "~Mania~, [_n._ missing?]"
                repairs.append("part of speech flagged missing by transcriber")
                rest = after
                continue
            gl, gn = classify_group(content)
            if not pos_parts and voice is None and gl and all(x in VOICE_LABELS for x in gl):
                voice = strip_markup(content).rstrip(".") + "."   # "~Blow up~, [_Active._]"
            else:
                labels += gl
                notes += gn
            rest = after
            continue
        break
    pos_raw = None
    if pos_parts:
        # Tidy spacing and stray commas: "v. a_." / "n," / "v. n," -> "v. a." / "n." / "v. n."
        pos_raw = re.sub(r"\s*\.[\s.]*", ". ", " ".join(pos_parts))
        pos_raw = _squash(pos_raw.replace(" ,", ",").replace(",.", ".")).rstrip(",")
        if pos_raw[-1].isalpha():
            pos_raw += "."
    return Header(hw, pos_raw, first_n, labels, notes, rest, voice, repairs)


def normalise_pos(pos_raw: str | None) -> list:
    """Map Soule's tag to our part-of-speech values, one per part of speech it names."""
    if not pos_raw:
        return []
    if pos_raw == "n. a.":
        # Printed twice (Curve, Vary); both entries are verbs: a slip for "v. a.".
        return ["v"]
    toks = re.findall(r"[a-z]+", pos_raw.lower())
    simple = {"ad": "adv", "adv": "adv", "prep": "prep", "conj": "conj",
              "interj": "interj", "pron": "pron", "art": "art", "part": "adj"}
    out, seen_verb, i = [], False, 0
    while i < len(toks):
        t = toks[i]
        if t in ("and", "or"):
            pass
        elif t == "v":
            seen_verb = True
            out.append("v")
        elif t in ("a", "n", "i") and seen_verb:
            pass                                   # v. a. active, v. n. neuter, v. a. & n.
        elif t == "a":
            out.append("adj")
        elif t == "n":
            out.append("n")
        elif t == "p":
            out.append("adj")                      # p. a. participial adjective, p. participle
            if i + 1 < len(toks) and toks[i + 1] == "a":
                i += 1
        elif t in ("pl", "sing"):
            if not out:
                out.append("n")
        elif t in simple:
            out.append(simple[t])
        i += 1
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------
# Whole book
# --------------------------------------------------------------------------
def parse_book(text: str) -> ParseResult:
    """Parse the dictionary part of the book (first to last ~entry~ paragraph).

    The title page, preface and explanatory table before the first entry, and
    the printer's line after the last, are skipped.
    """
    paras = list(paragraphs(text))
    tilde = [i for i, (_, p, _) in enumerate(paras) if p.startswith("~")]
    first, last = tilde[0], tilde[-1]
    entries, failures, repairs, stats = [], [], [], Counter()
    current = None

    def add_senses(entry, body, first_n, line):
        for m in SENSE_RE.finditer(body):
            if m.group(1) is None:
                repairs.append((line, "mis-set sense number", body))
        for n, chunk in split_senses(body, first_n):
            s = parse_sense_text(n, chunk, strip_markup(entry.headword))
            if s.italic_fallback:
                repairs.append((line, "synonyms printed in italic", chunk))
            entry.senses.append(s)

    for idx in range(first, last + 1):
        line, p, offsets = paras[idx]
        if LETTER_HEADING_RE.fullmatch(p):
            stats["letter_headings"] += 1
            continue
        stats["candidate_paragraphs"] += 1
        # Cut the paragraph where another headword starts inside it.
        glued = {m.start() for m in HW_GLUED_RE.finditer(p)}
        cuts = sorted({0} | {m.start() for m in HW_MID_RE.finditer(p) if m.start() > 0}
                      | {g + 1 for g in glued})
        ok = True
        for k, start in enumerate(cuts):
            end = cuts[k + 1] if k + 1 < len(cuts) else len(p)
            if end - 1 in glued:
                end -= 1                                 # drop the stray "~" of "waver.~~Pave"
            piece = p[start:end].strip()
            if not piece:
                continue
            here = _line_at(offsets, start)
            if k:
                repairs.append((here, "headword run into previous paragraph", piece))
            is_sense = bool(SENSE_RE.match(piece))
            if piece.startswith("~") and not is_sense:
                if "~" not in piece[1:]:
                    failures.append((here, "unclosed headword markup", piece))
                    ok = False
                    continue
                h = parse_header(piece)
                repairs += [(here, r, piece) for r in h.repairs]
                current = Entry(headword=h.headword, pos_raw=h.pos_raw, line=here,
                                labels=h.labels, notes=h.notes, voice=h.voice, text=piece)
                entries.append(current)
                add_senses(current, h.body, h.first_n, here)
            elif is_sense and current is not None:
                current.text += "\n" + piece
                add_senses(current, piece, None, here)
            else:
                failures.append((here, "neither a headword nor a sense", piece))
                ok = False
        if not ok:
            stats["candidate_paragraphs_failed"] += 1
    return ParseResult(entries, failures, repairs, stats)


# --------------------------------------------------------------------------
# Capitalisation
# --------------------------------------------------------------------------
def case_stats(entries):
    """Count, per word, capitalised uses not at the start of a sense, and lower-case uses."""
    cap_mid, low = Counter(), Counter()
    for e in entries:
        for s in e.senses:
            for i, syn in enumerate(s.synonyms):
                if i and syn in s.from_first:
                    continue                  # "Be frustrated": the capital of the first item
                for j, w in enumerate(WORD_RE.findall(syn)):
                    if w[0].islower():
                        low[w] += 1
                    elif (i, j) != (0, 0) and not w.isupper():
                        cap_mid[w] += 1
    return cap_mid, low


def decap(phrase: str, cap_mid: Counter, low: Counter) -> str:
    """Lower-case the first letter Soule capitalised, unless the word is a proper one.

    A word is kept capitalised when it is seen capitalised mid-list at least
    three times as often as in lower case ("Greek", "Indian corn"), or at least
    as often when a later word of the phrase is capitalised too ("Holy Ghost",
    "Lamb of God", but "take French leave").
    """
    m = WORD_RE.match(phrase)
    if not m:
        return phrase
    w = m.group(0)
    if not w[0].isupper() or (w.isupper() and len(w) > 1):
        return phrase
    later = WORD_RE.findall(phrase[m.end():])
    named = any(x[0].isupper() and not x.isupper() for x in later)
    if cap_mid[w] and cap_mid[w] >= (1 if named else 3) * low[w.lower()]:
        return phrase
    return w[0].lower() + phrase[1:]


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
_TITLE_WORDS = {"saint", "st", "mother"}


def headword_case(headword: str, synonyms: list, cap_mid: Counter, low: Counter) -> str:
    """Decide the case of a whole headword at once (Soule capitalises its first word).

    A one-word headword, or one with no capital after its first word, is
    handled by :func:`decap`. When a later word is printed with a capital, each
    such word is judged on its own: it is a common word, and is lower-cased,
    if the entry's own synonyms use it in lower case ("Stick Sulphur": "roll
    sulphur"), or, when the synonyms say nothing either way, if the book
    prints it in lower case more often than capitalised ("Billing and
    Cooing"). It stays a name if the synonyms print it capitalised ("Little
    Bear": "Lesser Bear"), if it comes after "of" or "the" ("house of God",
    "philosophy of the Academy"), or if the headword is just two words ("Black
    Sea"). The first word keeps its capital when a name follows it in a
    two-word headword or after "Saint" or "Mother" ("Davy Jones",
    "Saint-Vitus's dance", "Mother Carey's chicken"). Headwords that start
    with "the" are names ("the Almighty") and keep the printed capitals.
    """
    toks = list(WORD_RE.finditer(headword))
    later = [m for m in toks[1:] if m.group(0)[0].isupper() and not m.group(0).isupper()]
    if not later:
        return decap(headword, cap_mid, low)
    if toks[0].group(0).lower() == "the":
        return "the" + headword[3:]           # "the Almighty", "the Prince of Darkness"
    syn_words = [w for x in synonyms for w in WORD_RE.findall(x)]
    lower_in_syn = {w for w in syn_words if w[0].islower()}
    upper_in_syn = {w for w in syn_words if w[0].isupper()}
    two_words = len(toks) == 2
    chars = list(headword)
    names = set()
    for m in later:
        w = m.group(0)
        prev = headword[:m.start()].split()
        prev = prev[-1].lower().strip("-") if prev else ""
        if w.lower() in lower_in_syn:
            proper = False
        elif w in upper_in_syn or prev in ("of", "the") or two_words:
            proper = True
        else:
            proper = cap_mid[w] >= low[w.lower()]
        if proper:
            names.add(m.start())
        else:
            chars[m.start()] = w[0].lower()
    out = "".join(chars)
    first = toks[0].group(0)
    second_is_name = len(toks) > 1 and toks[1].start() in names
    if second_is_name and (two_words or first.lower() in _TITLE_WORDS):
        return out
    return decap(out, cap_mid, low)


def build_records(result: ParseResult):
    """Turn parsed entries into output records. Returns (records, failures, warnings)."""
    cap_mid, low = case_stats(result.entries)
    records, failures, warnings = [], list(result.failures), []
    for e in result.entries:
        hw = headword_case(e.headword, [x for s in e.senses for x in s.synonyms], cap_mid, low)
        numbers = [s.n for s in e.senses]
        if numbers and numbers != list(range(1, len(numbers) + 1)):
            # Gaps (Spare: 1, 2, 4) are kept as printed; a repeated number
            # (Wit: 1, 2, 3, 3) is a slip for the next one.
            prev = 0
            for s in e.senses:
                if s.n <= prev:
                    s.n = prev + 1
                prev = s.n
            warnings.append((e.line, f"sense numbers {numbers} -> {[s.n for s in e.senses]}", e.text))
        senses = []
        for s in e.senses:
            if s.unbalanced:
                warnings.append((e.line, "unbalanced brackets", s.raw))
            syns = []
            for k, x in enumerate(s.synonyms):
                x = decap(x, cap_mid, low) if k == 0 or x in s.from_first else x
                if x.lower() != hw.lower() and x not in syns:
                    syns.append(x)
            if not syns:
                warnings.append((e.line, f"sense {s.n} has no synonyms; dropped", s.raw))
                continue
            senses.append({"n": s.n, "synonyms": syns, "xrefs": list(s.xrefs),
                           "labels": list(dict.fromkeys(e.labels + s.labels)),
                           "notes": list(dict.fromkeys(e.notes + s.notes)),
                           "review": list(s.review)})
        if not senses:
            failures.append((e.line, "entry lists no synonyms", e.text))
            continue
        pos_list = normalise_pos(e.pos_raw)
        if e.pos_raw and not pos_list:
            failures.append((e.line, f"unknown part of speech {e.pos_raw!r}", e.text))
        if not pos_list and (e.voice or any(set(s["labels"]) & VOICE_LABELS for s in senses)):
            pos_list = ["v"]                  # Active./Neuter. mark phrasal verbs
        for pos in pos_list or [None]:
            records.append({"headword": hw, "pos": pos, "pos_raw": e.pos_raw or e.voice,
                            "senses": senses, "source": SOURCE_ID, "line": e.line})
    return records, failures, warnings


@dataclass
class Report:
    records: list
    entries_printed: int
    candidate_paragraphs: int
    failed_paragraphs: int
    failures: list
    warnings: list
    repairs: list


def parse(text: str | None = None) -> Report:
    """Parse the book (or ``text``, the body as returned by book_text)."""
    if text is None:
        text = book_text(BOOK)
    result = parse_book(text)
    records, failures, warnings = build_records(result)
    return Report(records=records, entries_printed=len(result.entries),
                  candidate_paragraphs=result.stats["candidate_paragraphs"],
                  failed_paragraphs=result.stats["candidate_paragraphs_failed"]
                  + sum(1 for f in failures if f not in result.failures),
                  failures=failures, warnings=warnings, repairs=result.repairs)


def write_jsonl(records, path: Path = OUT) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    blob = "\n".join(lines) + "\n"
    if mentions_gutenberg(blob):
        raise ValueError("output mentions Gutenberg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(blob, encoding="utf-8")


def main(argv=None) -> int:
    rep = parse()
    write_jsonl(rep.records)
    recs = rep.records
    n_senses = sum(len(r["senses"]) for r in recs)
    n_syn = sum(len(s["synonyms"]) for r in recs for s in r["senses"])
    n_xref = sum(len(s["xrefs"]) for r in recs for s in r["senses"])
    no_pos = sum(1 for r in recs if r["pos"] is None)
    by_pos = Counter(r["pos"] for r in recs)
    print(f"wrote {OUT}")
    print(f"entries printed: {rep.entries_printed}  records: {len(recs)} "
          f"(a tag naming two parts of speech gives two records)")
    print(f"senses: {n_senses}  synonyms: {n_syn}  xrefs: {n_xref}")
    print("records by pos: " + ", ".join(f"{k}={v}" for k, v in by_pos.most_common()))
    print(f"records without part of speech (phrase headwords): {no_pos}")
    print(f"candidate paragraphs: {rep.candidate_paragraphs}  failed to parse: {rep.failed_paragraphs}")
    for line, reason, txt in rep.failures:
        print(f"  FAILED line {line}: {reason}: {txt[:100]!r}")
    kinds = Counter(k for _, k, _ in rep.repairs)
    print(f"repaired printing slips: {len(rep.repairs)} " + str(dict(kinds)))
    shown = set()
    for line, kind, txt in rep.repairs:
        if kind not in shown:
            shown.add(kind)
            print(f"  e.g. line {line}: {kind}: {txt[:100]!r}")
    print(f"warnings: {len(rep.warnings)}")
    for line, reason, txt in rep.warnings:
        print(f"  line {line}: {reason}: {txt[:80]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
