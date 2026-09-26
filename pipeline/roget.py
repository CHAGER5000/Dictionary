"""Parse Roget's Thesaurus (1911 edition e-text, sources/pg22.txt) into heads and word groups.

Run ``python3 -m pipeline.roget`` to write data/roget.jsonl and print a parse report.

One JSON object per head (category), in book order::

    {"head": 220 | "59a", "name": "Exteriority", "name_notes": [...],
     "class": "Words relating to space", "class_num": 2,
     "division": null, "section": "Dimensions", "section_num": 2,
     "subsections": ["Centrical dimensions", "General"],
     "antonym_heads": [...],
     "groups": [{"pos": "n", "para": 0, "label": null,
                 "words": [...], "unrecognised": [...], "foreign": [...],
                 "obsolete": [...], "obsolete_now": [...],
                 "labels": {"word": ["U.S."]},
                 "xrefs": [{"word": "skin", "head": 223}]}],
     "phrases": [...], "foreign_phrases": [...], "prefixes": [...],
     "source": "roget1911", "line": 5012}

How the text is read
--------------------
* A head starts with ``#220. Exteriority.—N. ...`` (one head, 252a, has no ``#``).
  Lines that are only ``#`` are stray and skipped; a leading ``#`` on a continuation
  line is dropped.
* Part-of-speech sections start with ``N.``, ``V.``, ``Adj.``, ``Adv.``, ``Int.``,
  ``Pron.``, ``Pref.`` and ``Phr.`` (lower-case ``v.``, ``adj.``, ``phr.`` ... count only
  right after a full stop or semicolon).  ``&c. Adj.`` is read as a filler unless no
  later ``Adj.`` marker exists in the head.  ``Pref.`` words go to ``prefixes``.
* Headings between heads (CLASS, DIVISION, SECTION, "1. ...", "_General._", "(i) ...",
  "Internal conditions") give ``class``/``division``/``section``/``subsections``;
  footnote blocks ("[1] ...") are skipped.
* Inside a section a full stop ends a paragraph (``para``), a semicolon ends a tight
  group, a comma (or ``!``/``?`` in interjections) separates words.  Each group of
  the output is one semicolon group; ``para`` tells which paragraph it came from.
* Three marks are kept apart, as the e-text's front matter defines them:
  ``word|`` is the 1911 book's own dagger (obsolete in 1911) and goes to
  ``obsolete``, as do the few words tagged ``[obsolete]`` or ``[archaic]``;
  ``word|!`` is the e-text editor's "presently obsolete" and goes to
  ``obsolete_now``; ``word[obs3]`` only says that the editor's spelling checker
  did not know the word and that a college-sized dictionary lacks it or calls
  it archaic, so it goes to ``unrecognised`` (many are current words:
  anaesthetize, artilleryman, minster).  ``[Lat]``, ``[Fr]`` ... and italics
  mark foreign words, which go to ``foreign``.  Register and domain tags such as
  ``[U.S.]``, ``[Med]`` or a trailing ``*`` (slang) keep the word in ``words`` and are
  listed in ``labels``.  A bracket at the start of a paragraph (``[parts of a church:
  list] chancel, ...``) is a sense label and is copied to every group of that paragraph.
* ``skin &c. (covering) 223`` keeps ``skin`` and records the xref {word, head}; the
  ``&c.``, ``&c. adj.`` style fillers are dropped.
* Quotations found among the words go to ``phrases``.

``line`` is the 1-based line of the head in ``book_text("pg22.txt")``.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.pgtext import book_text, mentions_gutenberg

SOURCE_ID = "roget1911"
SOURCE_FILE = "pg22.txt"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "roget.jsonl"

# ---------------------------------------------------------------------------
# tag vocabularies (bracket contents, compared lower-case, before any ':')
# ---------------------------------------------------------------------------
OBSOLETE_TAGS = {"obs", "obsolete", "archaic"}
UNRECOGNISED_TAGS = {"obs3", "obs2", "obs1"}      # the editor's spelling-checker flag
FOREIGN_TAGS = {
    "lat", "latin", "fr", "french", "it", "ital", "italian", "ger", "german", "&german",
    "grk", "greek", "gr", "sp", "span", "spanish", "russ", "russian", "jap", "japanese",
    "afrikaans", "dutch", "arab", "arabic", "heb", "hebrew", "hindi", "hind", "sanskrit",
    "skr", "pers", "persian", "turk", "turkish", "chinese", "port", "portuguese",
    "yiddish", "hindustani", "malay", "anglo-indian", "hawaiian", "swedish", "norwegian",
    "danish", "welsh", "gaelic", "scandinavian",
}
LABEL_TAGS = {
    "u.s.": "U.S.", "u. s.": "U.S.", "u.s": "U.S.", "us": "U.S.", "u.s terminology": "U.S.",
    "local u. s.": "U.S.", "western us": "U.S.", "n. am.": "N. Am.", "u.s. slang": "slang",
    "brit": "Brit", "great britain": "Brit", "england": "Brit", "scot": "Scot", "irish": "Irish",
    "ireland": "Irish", "can.": "Canada", "canada": "Canada", "louisiana": "U.S.",
    "coll.": "colloquial", "coll": "colloquial", "colloq": "colloquial", "slang": "slang",
    "all slang": "slang", "vulg.": "vulgar", "vulg": "vulgar", "vulgar": "vulgar", "rude": "vulgar",
    "contr": "contraction", "abbr": "abbreviation", "fig": "figurative", "ironically": "ironic",
    "transitive": "transitive", "intransitive": "intransitive", "intrans": "intransitive",
    "intrans.": "intransitive",
    "med": "medicine", "med.": "medicine", "law": "law", "chem": "chemistry",
    "anat": "anatomy", "biol": "biology", "gram": "grammar", "microbiol": "microbiology",
    "microb": "microbiology", "phys": "physics", "geom": "geometry", "math": "mathematics",
    "math, statistics": "mathematics", "math, comp": "mathematics", "comp": "computing",
    "engin": "engineering", "physiol": "physiology", "arch": "architecture",
    "heraldry": "heraldry", "oceanography": "oceanography", "genet": "genetics",
    "biology, genetics": "genetics", "phil": "philosophy", "astron": "astronomy",
    "biochem": "biochemistry", "bioch": "biochemistry", "mus": "music", "military": "military",
    "semant": "semantics", "topography": "topography", "golf": "golf", "theol": "theology",
    "dial.": "dialect", "dial": "dialect", "us dialect": "dialect", "joc.": "jocular",
    "derogatory": "derogatory", "ironical": "ironic", "all coll.": "colloquial", "u. s.*": "U.S.",
    "scottish": "Scot", "aust.": "Australia", "nfld.": "Newfoundland", "rural u.s.": "U.S.",
    "parl.": "parliamentary", "parliamentary": "parliamentary", "medical": "medicine",
    "trans": "transitive",
}

POS_OF_MARKER = {
    "n": "n", "v": "v", "adj": "adj", "adv": "adv", "int": "int",
    "pron": "pron", "pref": "pref", "phr": "phr",
}
# usual order of the sections inside a head; used to resolve "&c. Adj." ambiguity
POS_RANK = {"n": 0, "v": 1, "adj": 2, "pref": 2.5, "pron": 2.5, "adv": 3, "int": 4, "phr": 5}

# placeholders used while splitting (never survive into the output)
B_OPEN, B_CLOSE = "\x01", "\x02"      # \x01<n>\x02 stands for protected [bracket] or "quote" n
P_OPEN = "\x07"                       # \x07<n>\x02 stands for a protected (parenthesis) n
FILLER = "\x03"                       # an "&c." style filler
FILLER_END = "\x06"                   # a filler that ended with a full stop ("&c. adj.")
ITALIC = "\x04"                       # start of an item that was in italics
OBSBAR = "\x05"                       # the book's | (obsolete in 1911) marker
OBSBAR_NOW = "\x08"                   # the e-text editor's |! ("presently obsolete")

# where each kind of item goes, and which kind wins when an item has two marks
KIND_LIST = {"word": "words", "unrecognised": "unrecognised", "foreign": "foreign",
             "obsolete_now": "obsolete_now", "obsolete": "obsolete"}
KIND_RANK = {"word": 0, "unrecognised": 1, "foreign": 2, "obsolete_now": 3, "obsolete": 4}
WORD_LISTS = tuple(KIND_LIST.values())

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}

HEAD_RE = re.compile(r"^#(\d+)([a-z]?)\.\s*(.*)$")
HEAD_NOHASH_RE = re.compile(r"^\s*(\d+)([a-z])\.?\s+([A-Z][^—]{0,60}—.*)$")
CLASS_RE = re.compile(r"^CLASS ([IVX]+)\s+(.+)$")
DIVISION_RE = re.compile(r"^DIVISION \((I+)\)\s*(.+)$")
SECTION_RE = re.compile(r"^SECTION ([IVX]+)\.\s*(.+)$")
FOOTNOTE_RE = re.compile(r"^(\[\d+\]|\[note\b|\[Note\b|\d+[A-Z][a-z])")

ABBREVIATIONS = {
    "st", "mr", "mrs", "dr", "messrs", "mt", "no", "jr", "sr", "viz", "vs", "cf", "esq",
    "rev", "capt", "col", "gen", "gov", "lt", "sgt", "prof", "hon", "mme", "mlle",
    "oz", "lb", "lbs", "pp", "ca", "etc", "ft", "co", "ltd",
}


class ParseLog:
    """Collects counted problems with a few examples of each."""

    def __init__(self):
        self.counts: Counter = Counter()
        self.examples: dict[str, list] = defaultdict(list)

    def add(self, kind: str, example, keep: int = 8):
        self.counts[kind] += 1
        if len(self.examples[kind]) < keep:
            self.examples[kind].append(example)


# ---------------------------------------------------------------------------
# small text helpers
# ---------------------------------------------------------------------------

def _clean_title(text: str) -> str:
    """Heading text -> tidy title: drop footnote marks, underscores, numbering, shouting."""
    t = re.sub(r"\[\d+\]", "", text).replace("_", "")
    t = re.sub(r"\s+", " ", t).strip().rstrip(".").strip()
    t = re.sub(r"^(\d+\.|\(\d+\)|\([ivx]+\))\s*", "", t)
    if t.isupper():
        t = t[0] + t[1:].lower()
    return t


def _join_lines(lines: list[str]) -> str:
    out = ""
    for raw in lines:
        s = raw.strip()
        if not s or s == "#":
            continue
        if s.startswith("#") and not re.match(r"#\d", s):
            s = s[1:].strip()
        if re.search(r"[A-Za-z]-$", out):   # word broken across lines: linsey-/woolsey
            out += s
        else:
            out = f"{out} {s}" if out else s
    return out


def _protect(text: str, store: list) -> str:
    """Replace [brackets] and "quotations" by placeholders so their punctuation is inert."""
    def keep(m):
        store.append(m.group(0))
        return f"{B_OPEN}{len(store) - 1}{B_CLOSE}"

    text = re.sub(r'"[^"\w\s]{1,2}"', keep, text)          # quoted symbols: "[", "]", "("
    text = re.sub(r"\[[^\[\]]*\]", keep, text)
    if text.count('"') % 2 == 0:
        text = re.sub(r'"[^"]*"', keep, text)
    def keep_paren(m):
        store.append(m.group(0))
        return f"{P_OPEN}{len(store) - 1}{B_CLOSE}"

    # parentheticals, innermost first: "(render, equal) 27", "(see little, small &c. 193)"
    while True:
        new = re.sub(r"\([^()]*\)", keep_paren, text)
        if new == text:
            return text
        text = new


def _unprotect(text: str, store: list) -> str:
    pat = re.compile(f"[{B_OPEN}{P_OPEN}](\\d+){B_CLOSE}")
    while pat.search(text):
        text = pat.sub(lambda m: store[int(m.group(1))], text)
    return text


def _tag_kind(content: str):
    """Classify the inside of a [bracket]:
    ('obsolete'|'unrecognised'|'foreign'|'label'|None, label)."""
    key = content.split(":")[0].strip().lower()
    key_nodot = key.rstrip(".")
    if key in UNRECOGNISED_TAGS or key_nodot in UNRECOGNISED_TAGS:
        return "unrecognised", None
    if key in OBSOLETE_TAGS or key_nodot in OBSOLETE_TAGS:
        return "obsolete", None
    if key in FOREIGN_TAGS or key_nodot in FOREIGN_TAGS:
        return "foreign", None
    if key in LABEL_TAGS:
        return "label", LABEL_TAGS[key]
    if key_nodot in LABEL_TAGS:
        return "label", LABEL_TAGS[key_nodot]
    return None, None


# ---------------------------------------------------------------------------
# splitting a head body into POS sections
# ---------------------------------------------------------------------------
# "&c.", "&c. adj.", "&c.v.", "&cv." ... ("*c." is a typo in the e-text)
FILLER_RE = re.compile(r"[&*]c(?:\.|\b|(?=(?:adj|adv|n|v)\b))(?:\s*,?\s*(?:adj|adv|n|v|phr)\b\.?){0,2}", re.I)
MARKER_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s.;,!?—" + B_CLOSE + r"]))"
    r"(N|V|Adj|Adv|Int|Phr|Pron|Pref|adj|adv|int|phr|n|v)(?:\.(?=[\s—\"" + B_OPEN + r"]|$|[a-z])|(?=" + B_OPEN + "))"
)
FILLER_BEFORE_RE = re.compile(r"[&*]c?\s*[.,]?\s*$")


def _split_sections(body: str, log: ParseLog, head_id) -> list[tuple[str, str]]:
    """body (protected) -> [(pos, text)].  Text before any marker is taken as 'n'."""
    cands = []
    for m in MARKER_RE.finditer(body):
        name = m.group(1)
        if name.islower():
            # lower-case markers only count at a paragraph or group boundary
            before = body[:m.start()].rstrip()
            if before and not before.endswith((".", ";", B_CLOSE)) or FILLER_BEFORE_RE.search(body[:m.start()]):
                continue
        cands.append((m.start(), m.end(), POS_OF_MARKER[name.lower()],
                      bool(FILLER_BEFORE_RE.search(body[:m.start()]))))

    chosen = []
    rank = -1.0
    for i, (start, end, pos, after_filler) in enumerate(cands):
        if after_filler:
            later_same = any(c[2] == pos and not c[3] for c in cands[i + 1:])
            if later_same or POS_RANK[pos] <= rank:
                continue            # "&c. Adj." filler
        if POS_RANK[pos] < rank:
            log.add("pos_out_of_order", f"{head_id}: {pos} after rank {rank}")
        rank = POS_RANK[pos]
        chosen.append((start, end, pos))

    sections = []
    pos, pos_end = "n", 0
    first = True
    for start, end, new_pos in chosen:
        chunk = body[pos_end:start]
        if chunk.strip(" .;,—") or not first:
            sections.append((pos, chunk))
        first = False
        pos, pos_end = new_pos, end
    sections.append((pos, body[pos_end:]))
    return [(p, t) for p, t in sections if t.strip(" .;,—")]


# a word after "&c." that shows the phrase goes on, so the full stop is the filler's own
_FILLER_GOES_ON = {"of", "with", "for", "to", "from", "in", "on", "at", "by", "into", "upon",
                   "about", "all", "itself", "oneself", "himself", "herself", "themselves"}


def _split_paragraphs(text: str, store: list) -> list[str]:
    """Split a section at full stops that end a paragraph (not abbreviations).

    "&c. adj. extincteur" also ends a paragraph: the filler's own full stop doubles as
    the paragraph's, unless a number, a parenthesis or a tag follows ("&c. 4",
    "&c.[obs3]"); a [sense label] after it does start a new paragraph. (A plain
    "&c." before "of", "with", "all" ... is marked FILLER by _filler_mark and
    never ends one.)"""
    def after_filler(m):
        nxt = re.match(r"\s*(?:([" + B_OPEN + r"])(\d+)" + B_CLOSE + r"|(.))?", text[m.end():])
        if nxt.group(1):
            content = store[int(nxt.group(2))]
            ends = not (content.startswith("[") and _tag_kind(content[1:-1])[0] is not None)
        else:
            ch = nxt.group(3)
            ends = ch is None or not (ch.isdigit() or ch in "(;,:." + P_OPEN)
        return FILLER + ("." if ends else "")

    text = re.sub(FILLER_END, after_filler, text)
    paras, last = [], 0
    for m in re.finditer(r"\.(?=\s|$)", text):
        before = text[last:m.start()]
        tok = re.search(r"([^\s,;(]*)$", before).group(1)
        low = tok.lower()
        if low in ABBREVIATIONS or re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", tok) \
                or re.fullmatch(r"[A-Z]", tok):
            continue
        paras.append(text[last:m.start()])
        last = m.end()
    paras.append(text[last:])
    return [p for p in paras if p.strip(" ;,.")]


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------
XREF_RE = re.compile(r"^(.*?)\s*(?:\(([^()]*)\))?\s*(\d{1,4}[a-d]?)$")


def _head_value(ref: str):
    return int(ref) if ref.isdigit() else ref


def _parse_item(item: str, store: list, log: ParseLog, head_id):
    """One comma item -> dict(word, kind, labels, xref, quote, lead_label).

    kind is a key of KIND_LIST; xref is a head number (or "59a") or None; quote is
    set when the item is a quotation; lead_label is a leading [sense label];
    slot is the text before a "-" slot mark ("in -turn" -> "in") and lead_dash
    says the item starts with one ("- its turn")."""
    res = {"word": None, "kind": "word", "labels": [], "xref": None,
           "quote": None, "lead_label": None, "slot": None, "lead_dash": False}

    def mark(kind):
        if KIND_RANK[kind] > KIND_RANK[res["kind"]]:
            res["kind"] = kind

    s = item.strip()
    # leading bracket = a sense label for the paragraph
    m = re.match(f"^{B_OPEN}(\\d+){B_CLOSE}", s)
    if m:
        content = store[int(m.group(1))]
        if content.startswith("[") and _tag_kind(content[1:-1])[0] is None:
            res["lead_label"] = re.sub(r"\s+", " ", content[1:-1]).strip()
            s = s[m.end():].strip()

    # resolve the remaining placeholders (parentheticals come back as text)
    def repl(mm):
        content = store[int(mm.group(1))]
        if content.startswith("("):
            return content
        if content.startswith('"'):
            res["quote"] = content
            return " "
        kind, label = _tag_kind(content[1:-1])
        if kind in ("obsolete", "unrecognised", "foreign"):
            mark(kind)
        elif kind == "label":
            res["labels"].append(label)
        return " "

    placeholder = re.compile(f"[{B_OPEN}{P_OPEN}](\\d+){B_CLOSE}")

    def has_text(t):
        return bool(re.search(r"[^\W\d_]", placeholder.sub(" ", t)))

    # a quotation inside an item: 'call a spade "a spade"' and 'say "bo" to a goose'
    # are one phrase; in '"hence loathed melancholy!" begone dull care' the
    # quotation is a cry of its own and the words after it are the item
    for qm in placeholder.finditer(s):
        content = store[int(qm.group(1))]
        if not content.startswith('"'):
            continue
        before, after = s[:qm.start()], s[qm.end():]
        inner = content.strip('"').strip()
        if has_text(before) and len(inner.split()) <= 4:
            if inner[-1:] in "!?,":
                after = ""                  # 'cry "wolf!" dissemble' (a comma is missing)
            s = f"{before} {inner.rstrip('!?,')} {after}"
        elif has_text(before):
            res["quote"] = content          # 'walk the earth "strut and fret ..."'
            s = before
        elif has_text(after):
            res["quote"] = content
            s = after
        break
    while placeholder.search(s):
        s = placeholder.sub(repl, s)
    if res["quote"] and not has_text(s):
        return res
    if OBSBAR in s:
        mark("obsolete")
    if OBSBAR_NOW in s:
        mark("obsolete_now")
    if ITALIC in s:
        mark("foreign")
    if "*" in s.replace("*c", ""):
        res["labels"].append("slang")
    s = s.replace(OBSBAR, " ").replace(OBSBAR_NOW, " ").replace(ITALIC, " ").replace("_", " ")
    s = re.sub(r"(?<![&\w])\*(?=c\b)", "&", s)       # "*c." typo for "&c."
    s = s.replace("*", " ")
    s = re.sub(r"@[\w.:]+", " ", s)                  # embryonic @2.3.2 references
    s = re.sub(r"\{[^{}]*\}", " ", s) if re.search(r"\{(ant|opp)", s) else s
    if FILLER in s:
        s = re.sub(FILLER + r"(.*)\s(?:adj|adv|n|v)\.?\s*$", FILLER + r"\1", s)   # "be in love &c. with adj."
    s = s.replace(FILLER, " ")
    s = FILLER_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")

    if re.match(r"(?i:see) [A-Z]", s):                 # "&c., see Answer 462"
        s = s[4:].strip()
        res["see"] = True
    s = s.rstrip(" -")
    xm = XREF_RE.match(s)
    if xm and (xm.group(2) is not None or xm.group(1) == "" or re.search(r"[A-Za-z]$", xm.group(1))):
        ref = xm.group(3)
        if int(re.match(r"\d+", ref).group(0)) <= 1000:
            res["xref"] = _head_value(ref)
            s = xm.group(1).strip()
        elif FILLER in item or FILLER_END in item or "&c" in item:
            log.add("xref_number_out_of_range", f"{head_id}: {s}")
            s = xm.group(1).strip()
    else:
        # the number inside the parenthesis: "smallest (see little, small 193)"
        pm = re.match(r"^(.*?)\s*\(([^()]*?)\s(\d{1,4}[a-d]?)\s*\)?$", s)
        if pm and int(re.match(r"\d+", pm.group(3)).group(0)) <= 1000:
            res["xref"] = _head_value(pm.group(3))
            s = pm.group(1).strip()
    s = re.sub(r"\([^()]*\)", " ", s)                # other parentheticals
    s = re.sub(r"\s*\([^)]*$", " ", s)               # unclosed "(large 192"
    s = re.sub(r"\s&[a-z]?\.?$", " ", s)             # broken fillers "daring &", "&v", "&e"
    # "-" marks a slot: "in -turn, - its turn" is "in turn, in its turn"
    s = s.strip()
    res["lead_dash"] = bool(re.match(r"-\s*\w", s))
    slot = re.search(r"\s-\s*(?=\w)", s)
    if slot and not res["lead_dash"]:
        res["slot"] = re.sub(r"\s+", " ", s[:slot.start()]).strip(" ,.;:") or None
    s = re.sub(r"(?:(?<=\s)|^)-\s*(?=\w)", "", s)    # "be -violent", "want of - intellect" (slot marker)
    s = re.sub(r"(\w)- (\w)", r"\1-\2", s)          # "over- the-counter"
    s = re.sub(r"\s+", " ", s).strip(" ,.;:!?'\"-—")
    if res.get("see") and re.fullmatch(r"[A-Z][a-z]+", s):
        s = s.lower()
    res["word"] = s or None
    return res


BAD_CHARS = re.compile(r"[\[\]&{}()#@|*_\"—" + B_OPEN + B_CLOSE + P_OPEN + FILLER + FILLER_END + ITALIC
                       + OBSBAR + OBSBAR_NOW + "]")


def _valid_word(w: str) -> bool:
    return bool(w) and bool(re.search(r"[^\W\d_]", w)) and not BAD_CHARS.search(w)


def _split_items(group_text: str, store: list = ()) -> list[str]:
    # keep [tags] that follow ! or ? with the item before them ("euge[Ger]!"), but
    # not a quotation: 'woe betide! "quis talia ..."' is two items
    def move(m):
        marks = re.findall(f"{B_OPEN}\\d+{B_CLOSE}", m.group(2))
        if any(store[int(x[1:-1])].startswith('"') for x in marks if store):
            return m.group(0)
        return m.group(2) + m.group(1)

    t = re.sub(f"([!?])((?:\\s*{B_OPEN}\\d+{B_CLOSE})+)", move, group_text)
    return [x for x in re.split(r"[,!?]", t) if x.strip()]


FUNCTION_WORDS = {
    "a", "an", "the", "of", "after", "to", "in", "on", "with", "for", "up", "out", "off", "into",
    "upon", "about", "by", "at", "from", "down", "over", "away", "back", "oneself", "one's",
    "as", "like", "than", "together", "through", "round", "around", "forth", "along",
}
_NUMBER_SPLIT_RE = re.compile(
    "((?:" + FILLER + "|" + B_CLOSE + r")\s*\d{1,4}[a-d]?-?(?:\s*" + B_OPEN + r"\d+" + B_CLOSE + r")*)\s+(?=\S)")


def _number_pieces(items: list[str]):
    """Split "compromise &c. 774 neutralization" (a comma is missing after the xref).

    Yields (text, mode): mode "join" means the piece continues the previous word
    ("seize &c. (take) 789 an opportunity" -> "seize an opportunity")."""
    for it in items:
        pieces = _NUMBER_SPLIT_RE.split(it)
        # re.split with one group: [text, sep, text, sep, text ...]
        texts = []
        for i in range(0, len(pieces), 2):
            chunk = pieces[i] + (pieces[i + 1] if i + 1 < len(pieces) else "")
            texts.append(chunk)
        for j, t in enumerate(texts):
            if not t.strip():
                continue
            first = re.sub(r"[\x00-\x08]\d*", " ", t).split()
            join = j > 0 and bool(first) and first[0].lower() in FUNCTION_WORDS
            yield t, ("join" if join else "new")


def _remove_word(group: dict, word: str):
    for key in WORD_LISTS:
        if word in group[key]:
            group[key].remove(word)
    group["labels"].pop(word, None)


def _mark_italics(text: str) -> str:
    """Put ITALIC at the start of every item inside an _italic span_ (foreign words)."""
    def mark(m):
        pieces = re.split(r"([;,])", m.group(1))
        return "".join(p if p in ",;" or not p.strip() else ITALIC + p for p in pieces)
    return re.sub(r"_([^_]+)_", mark, text)


# ---------------------------------------------------------------------------
# a whole head
# ---------------------------------------------------------------------------

def _parse_header(header: str, log: ParseLog):
    m = re.match(r"^#?(\d+)([a-z]?)\.?\s*(.*)$", header.strip())
    num, letter, rest = m.group(1), m.group(2), m.group(3)
    head_id = int(num) if not letter else f"{num}{letter}"
    antonyms = [_head_value(x) for b in re.findall(r"\{(?:ant|opp)\.?\s*(?:of |to )?([^}]*)\}", rest)
                for x in re.findall(r"\d+[a-z]?", b)]
    rest = re.sub(r"\{[^}]*\}", " ", rest)
    rest = re.sub(r"\[\d+\]", " ", rest)                       # footnote marks
    notes = [re.sub(r"\s+", " ", n).strip().rstrip(".") for n in re.findall(r"\[([^\]]*)\]", rest)]
    rest = re.sub(r"\[[^\]]*\]", " ", rest)
    if "[" in rest:                                             # unclosed bracket (#454)
        before, _, inside = rest.partition("[")
        words = inside.strip().rstrip(".").split()
        notes.append(" ".join(words[:-1]).strip(" ,"))
        rest = before + " " + (words[-1] if words else "")
        log.add("header_unclosed_bracket", header[:80])
    name = re.sub(r"\s+", " ", rest).strip()
    name = re.sub(r"[.,]?\s*\d+$", "", name).strip(" .,")      # "Chance. 2"
    name = re.sub(r"\.\s+", ", ", name)                         # "Gulf. Lake" -> "Gulf, Lake"
    name = re.sub(r"[,\s]*&c\.?$", "", name).replace("|", "").strip(" .,")  # "Five, &c", "Vegetability|"
    name = name[:1].upper() + name[1:]
    if name.isupper():
        name = name.capitalize()
    return head_id, name, [n for n in notes if n], antonyms


def _filler_mark(m) -> str:
    """The placeholder for one "&c." style filler: FILLER_END if its full stop
    may also end the paragraph, else FILLER. A plain "&c." followed by a word
    that carries the phrase on ("beat &c. all others", "holder &c. of the legal
    estate", "contend &c. with") never ends it."""
    text = m.group(0)
    if not text.endswith("."):
        return f" {FILLER} "
    word = re.match(r"\s*([a-z']+)\b", m.string[m.end():])
    if re.fullmatch(r"[&*]c\.", text.strip()) and word and word.group(1) in _FILLER_GOES_ON:
        return f" {FILLER} "
    return f" {FILLER_END} "


def parse_head(lines: list[str], line_no: int, log: ParseLog, hierarchy: dict) -> dict:
    text = _join_lines(lines)
    dash = text.find("—")
    if dash < 0:
        log.add("head_without_dash", text[:80])
        header, body = text, ""
    else:
        header, body = text[:dash], text[dash + 1:]
    pm = re.search(r"\s(N|V|Adj|Adv)\.\s*$", header)          # "Topic. N.—subject..."
    if pm:
        body = header[pm.start():].strip() + " " + body
        header = header[:pm.start()]
    head_id, name, notes, antonyms = _parse_header(header, log)

    for b in re.findall(r"\{(?:ant|opp)\.?\s*(?:of |to )?([^}]*)\}", body):
        antonyms += [_head_value(x) for x in re.findall(r"\d+[a-z]?", b)]
    body = re.sub(r"\{(?:ant|opp)\.?[^}]*\}", " ", body)
    body = re.sub(r"(?<![&\w])\*c\b", "&c", body)
    body = re.sub(r"(?<!\w)7c\.", "&c.", body)                    # "convergence 7c. 290"
    body = re.sub(r"(?<=\w)\.\.(?!\.)", ".", body)                  # "N.. wind", "Adj..escaping"
    body = re.sub(r"(?<=\d)\.(?=[A-Za-z])", ". ", body)              # "515.point of view"
    body = re.sub(r"(?<=[a-z])\.\d(?=[,;])", "", body)                # "misbeliever.1," (footnote mark)
    body = re.sub(r"(?<=[;,])\d(?= [a-z])", "", body)                  # "&c. n.;1 beneath notice"
    body = re.sub(r"&\.", "&c.", body)                                 # "daring &. v."
    body = re.sub(r"(?<=\d)O(?=\d)", "0", body)                        # "4O8" (letter O for zero)
    body = re.sub(r"(?<=\d)l(?=[ab]?\b)", "1", body)                   # "90la" -> "901a"
    body = re.sub(r"(?<=\s)c\[obs3\]\.", "&c.", body)                  # "floridness c[obs3]. adj."

    store: list = []
    body = _mark_italics(body)
    body = _protect(body, store)
    body = body.replace("|!", OBSBAR_NOW).replace("|", OBSBAR)

    record = {
        "head": head_id, "name": name, "name_notes": notes,
        **hierarchy,
        "antonym_heads": sorted(set(antonyms), key=str),
        "groups": [], "phrases": [], "foreign_phrases": [], "prefixes": [],
        "source": SOURCE_ID, "line": line_no,
    }

    for pos, sec in _split_sections(body, log, head_id):
        if pos == "phr":
            _add_phrases(record, sec, store)
            continue
        sec = FILLER_RE.sub(_filler_mark, sec)
        prev_word, prev_group = None, None
        for p_index, para in enumerate(_split_paragraphs(sec, store)):
            para_label = None
            for g_text in re.split(r"[;:]", para):
                items = _split_items(g_text, store)
                group = {"pos": pos, "para": p_index, "label": None,
                         **{k: [] for k in WORD_LISTS}, "labels": {}, "xrefs": []}
                last = None          # (result, word) of the previous piece, for "seize &c. 789 an opportunity"
                dash_base = None     # what a leading "-" stands for ("in -turn, - its turn")
                for it, mode in _number_pieces(items):
                    r = _parse_item(it, store, log, head_id)
                    if r["lead_label"] and para_label is None:
                        para_label = r["lead_label"]
                    if r["quote"]:
                        _add_phrases(record, r["quote"], [], foreign=r["kind"] == "foreign")
                        if not r["word"]:
                            continue
                    w = r["word"]
                    if pos == "pref":
                        if w:
                            record["prefixes"].append(w + ("-" if not w.endswith("-") else ""))
                        continue
                    if mode == "join" and w:
                        prev_r, prev_w = last if last else (None, None)
                        if prev_w:
                            _remove_word(group, prev_w)
                            if prev_w.split()[-1] == w:          # "under the head of &c. 75 of"
                                w = prev_w
                            else:
                                w = f"{prev_w} {w}"
                            for x in group["xrefs"]:                 # the xref follows the word
                                if x["word"] == prev_w:
                                    x["word"] = w
                            r["labels"] = prev_r["labels"] + r["labels"]
                            if KIND_RANK[prev_r["kind"]] > KIND_RANK[r["kind"]]:
                                r["kind"] = prev_r["kind"]
                        elif w.lower() in FUNCTION_WORDS:
                            continue                             # "basket of, &c. 191 of"
                    last = None
                    if not w:
                        if r["xref"] is not None and prev_word:
                            # "(indicative) 550" or "; (contract) 195": another xref of the last word
                            prev_group["xrefs"].append({"word": prev_word, "head": r["xref"]})
                        elif r["xref"] is not None:
                            log.add("xref_without_word", f"{head_id}: {_unprotect(it, store).strip()[:60]}")
                        continue
                    if w in ("n", "v", "adj", "adv", "N", "V", "Adj", "Adv", "Phr"):
                        log.add("pos_fragment_dropped", f"{head_id}: {_unprotect(it, store).strip()[:50]!r}")
                        continue
                    if not _valid_word(w):
                        log.add("bad_word", f"{head_id}: {_unprotect(it, store).strip()[:70]!r} -> {w!r}")
                        continue
                    if r["lead_dash"]:
                        if dash_base:
                            w = f"{dash_base} {w}"         # "clean, - as a whistle", "knock-out, -blow"
                        else:
                            log.add("dash_without_base", f"{head_id}: {w}")
                    elif r["slot"]:
                        dash_base = r["slot"]              # "by regular -steps, -gradations"
                    else:
                        dash_base = w
                    if len(w) > 60:
                        log.add("long_word", f"{head_id}: {w[:70]}")
                    target = KIND_LIST[r["kind"]]
                    if w not in group[target]:
                        group[target].append(w)
                    if r["labels"] and target in ("words", "unrecognised"):
                        group["labels"].setdefault(w, [])
                        for lab in r["labels"]:
                            if lab not in group["labels"][w]:
                                group["labels"][w].append(lab)
                    if r["xref"] is not None:
                        group["xrefs"].append({"word": w, "head": r["xref"]})
                    prev_word, prev_group = w, group
                    last = (r, w)
                if any(group[k] for k in WORD_LISTS):
                    record["groups"].append(group)
            if para_label:
                for g in record["groups"]:
                    if g["pos"] == pos and g["para"] == p_index:
                        g["label"] = para_label
    return record


def _split_phrases(text: str) -> list[str]:
    """Split a Phr. section on ';' and on sentence full stops, never inside quotes."""
    parts, buf, inq = [], "", False
    for i, ch in enumerate(text):
        if ch == '"':
            inq = not inq
        if not inq and (ch == ";" or (ch == "." and text[i + 1:i + 2] in (" ", "")
                                      and re.search(r"(?:^|[\s\"])(?:[a-z]{2,}|[A-Z][a-z]+|\S*[\x02\]\"])\s*$",
                                                    buf)
                                      and not re.search(r"(?:^|\s)(?:%s)$" % "|".join(ABBREVIATIONS),
                                                        buf, re.I))):
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts


def _phrase_segments(p: str) -> list[str]:
    """Split a phrase that holds a foreign tag at quotation marks and after a
    language tag followed by a comma, so that English quotations and phrases
    next to a foreign one are not taken for foreign ('"a fellow feeling makes
    one wondrous kind" onor di bocca ...[It]', "horresco referens[Latin], one's
    heart failing one ...")."""
    tags = [_tag_kind(b)[0] for b in re.findall(r"\[([^\]]*)\]", p)]
    if "foreign" not in tags or p.count('"') % 2:
        return [p]
    out = []
    for part in re.split(r'("[^"]*"(?:\s*\[[^\]]*\])*)', p):
        if not part.strip(" ,;.") or part.lstrip().startswith('"'):
            if part.strip(" ,;."):
                out.append(part)
            continue
        last = 0
        for m in re.finditer(r"\[([^\]]*)\]\s*,", part):
            if _tag_kind(m.group(1))[0] == "foreign":
                out.append(part[last:m.end() - 1])
                last = m.end()
        out.append(part[last:])
    return [x for x in out if x.strip(" ,;.")]


def _add_phrases(record: dict, sec: str, store: list, foreign: bool = False):
    text = _unprotect(sec, store) if store else sec
    text = FILLER_RE.sub(" ", text)
    for p in (seg for whole in _split_phrases(text) for seg in _phrase_segments(whole)):
        tags = [_tag_kind(b)[0] for b in re.findall(r"\[([^\]]*)\]", p)]
        is_foreign = foreign or ITALIC in p or "foreign" in tags
        p = re.sub(r"\[[^\]]*\]", " ", p)
        p = p.replace(ITALIC, " ").replace(OBSBAR, " ").replace(OBSBAR_NOW, " ").replace("_", " ")
        p = p.replace("*", " ")
        p = re.sub(r"@[\w.:]+", " ", p)
        p = re.sub(r"\s+", " ", p).strip(" ,.;:")
        if p.count('"') % 2 or (p.startswith('"') and p.endswith('"') and p.count('"') == 2):
            p = p.replace('"', "")
        p = re.sub(r"\s+", " ", p).strip(" ,.;:")
        if not re.search(r"[^\W\d_]", p) or "[" in p or "]" in p:
            continue
        dest = record["foreign_phrases"] if is_foreign else record["phrases"]
        if p not in dest:
            dest.append(p)


# ---------------------------------------------------------------------------
# the whole book
# ---------------------------------------------------------------------------

def _blocks(lines: list[str], start: int):
    """Yield (first_line_index, [line indexes]) for runs of non-blank lines."""
    cur = []
    for i in range(start, len(lines)):
        if lines[i].strip():
            cur.append(i)
        elif cur:
            yield cur
            cur = []
    if cur:
        yield cur


def _heading(block_text: list[str]):
    """Classify a block: ('class'|'division'|'section'|('sub', rank)|'footnote', value) or None."""
    first = block_text[0].strip()
    joined = re.sub(r"\s+", " ", " ".join(s.strip() for s in block_text))
    if HEAD_RE.match(first) or HEAD_NOHASH_RE.match(block_text[0]):
        return None
    m = CLASS_RE.match(joined)
    if m and len(block_text) <= 2:
        return "class", (ROMAN.get(m.group(1)), _clean_title(m.group(2)))
    m = DIVISION_RE.match(joined)
    if m:
        return "division", (len(m.group(1)), _clean_title(m.group(2)))
    m = SECTION_RE.match(joined)
    if m and len(block_text) == 1:
        return "section", (ROMAN.get(m.group(1)), _clean_title(m.group(2)))
    if FOOTNOTE_RE.match(first):
        return "footnote", joined
    if len(block_text) != 1 or len(first) > 70 or ";" in first or "&" in first:
        return None
    if re.match(r"^\d+\.\s+[A-Z]", first):
        return ("sub", 1), _clean_title(first)
    if first.startswith("_"):
        inner = first.strip("_").strip()
        if re.match(r"^\d+\.", inner):
            return ("sub", 2), _clean_title(inner)
        if re.match(r"^\(\d+\)", inner):
            return ("sub", 3), _clean_title(inner)
        return ("sub", 5), _clean_title(inner)
    if re.match(r"^\([ivx]+\)\s+[A-Z]", first):
        return ("sub", 4), _clean_title(first)
    if re.match(r"^[A-Z][A-Za-z ]*(\[\d+\])?$", first) and not re.match(
            r"^(N|V|Adj|Adv|Int|Phr)\.", first):
        return ("sub", 5), _clean_title(first)
    return None


def parse(text: str | None = None):
    """Parse the book text (default: book_text('pg22.txt')). Returns (records, ParseLog)."""
    if text is None:
        text = book_text(SOURCE_FILE)
    lines = text.split("\n")
    log = ParseLog()

    first_head = next(i for i, l in enumerate(lines) if l.startswith("#1. "))
    start = max(i for i in range(first_head) if re.match(r"^CLASS [IVX]+\s*$", lines[i]))

    hierarchy = {"class": None, "class_num": None, "division": None, "section": None,
                 "section_num": None, "subsections": []}
    sub_stack: list[tuple[int, str]] = []
    records = []
    cur_lines: list[str] = []
    cur_start = None
    cur_hier = None

    def close():
        nonlocal cur_lines, cur_start
        if cur_start is not None:
            records.append(parse_head(cur_lines, cur_start + 1, log, cur_hier))
        cur_lines, cur_start = [], None

    for block in _blocks(lines, start):
        btext = [lines[i] for i in block]
        kind = _heading(btext)
        if kind is not None:
            what, value = kind
            if what == "footnote":
                log.add("footnote_skipped", value[:70])
                continue
            close()
            if what == "class":
                hierarchy.update({"class_num": value[0], "class": value[1], "division": None,
                                  "section": None, "section_num": None})
                sub_stack = []
            elif what == "division":
                hierarchy.update({"division": value[1], "section": None, "section_num": None})
                sub_stack = []
            elif what == "section":
                hierarchy.update({"section_num": value[0], "section": value[1]})
                sub_stack = []
            else:
                rank = what[1]
                sub_stack = [s for s in sub_stack if s[0] < rank] + [(rank, value)]
            hierarchy["subsections"] = [s[1] for s in sub_stack]
            continue
        for i in block:
            line = lines[i]
            if HEAD_RE.match(line) or HEAD_NOHASH_RE.match(line):
                close()
                if not line.startswith("#"):
                    log.add("head_without_hash", line.strip()[:60])
                cur_start = i
                cur_hier = {k: (list(v) if isinstance(v, list) else v) for k, v in hierarchy.items()}
                cur_lines = [line.strip()]
            elif cur_start is not None:
                cur_lines.append(line)
            elif line.strip() != "#":
                log.add("text_outside_head", line.strip()[:60])
    close()

    known = {r["head"] for r in records}
    for r in records:
        for g in r["groups"]:
            for x in g["xrefs"]:
                if x["head"] not in known:
                    log.add("xref_unknown_head", f"{r['head']}: {x}")
        if not r["groups"]:
            log.add("head_without_groups", f"{r['head']} {r['name']}")
    return records, log


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------
_CACHE: dict = {}


def load(path: Path | str | None = None) -> list[dict]:
    """Records from data/roget.jsonl (or a fresh parse if the file is missing)."""
    path = Path(path) if path else OUT_PATH
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    return parse()[0]


def index(records: list[dict] | None = None, include_obsolete: bool = False) -> dict:
    """word (lower-case) -> list of (head, group index) where the word is listed.

    Group index is the position in that head's "groups" list.  Only main words are
    indexed unless include_obsolete is True (then every other list too: unrecognised,
    foreign, obsolete and obsolete_now words).  A test and debugging helper; the
    build does not use it."""
    if records is None:
        if "records" not in _CACHE:
            _CACHE["records"] = load()
        records = _CACHE["records"]
    idx: dict[str, list] = defaultdict(list)
    for r in records:
        for gi, g in enumerate(r["groups"]):
            words = list(g["words"])
            if include_obsolete:
                words += [w for k in WORD_LISTS if k != "words" for w in g.get(k, [])]
            for w in words:
                key = w.lower()
                entry = (r["head"], gi)
                if entry not in idx[key]:
                    idx[key].append(entry)
    return dict(idx)


def stats(records: list[dict]) -> dict:
    groups = [g for r in records for g in r["groups"]]
    return {
        "heads": len(records),
        "groups": len(groups),
        "words": sum(len(g["words"]) for g in groups),
        "distinct_words": len({w.lower() for g in groups for w in g["words"]}),
        "unrecognised": sum(len(g["unrecognised"]) for g in groups),
        "obsolete": sum(len(g["obsolete"]) for g in groups),
        "obsolete_now": sum(len(g["obsolete_now"]) for g in groups),
        "foreign": sum(len(g["foreign"]) for g in groups),
        "xrefs": sum(len(g["xrefs"]) for g in groups),
        "phrases": sum(len(r["phrases"]) for r in records),
        "foreign_phrases": sum(len(r["foreign_phrases"]) for r in records),
        "labelled_words": sum(len(g["labels"]) for g in groups),
    }


def write(records: list[dict], path: Path | str = OUT_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    if mentions_gutenberg(out):
        raise ValueError("output mentions Gutenberg; refusing to write")
    path.write_text(out, encoding="utf-8")
    return path


def main(argv=None) -> int:
    records, log = parse()
    path = write(records)
    print(f"wrote {path} ({len(records)} heads)")
    for k, v in stats(records).items():
        print(f"  {k}: {v}")
    print("parse log:")
    for kind, n in log.counts.most_common():
        print(f"  {kind}: {n}")
        for ex in log.examples[kind][:5]:
            print(f"      {ex}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
