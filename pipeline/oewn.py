"""Load Open English WordNet (2025 edition) into SQLite and look words up in it.

Run from the project root:

    python3 -m pipeline.oewn            # writes data/oewn.sqlite and prints a report

Source: sources/english-wordnet-2025.xml.gz, WN-LMF 1.3 XML, CC BY 4.0.
Source id: ``oewn2025``. Every synset (definition), example, sense (synonym
membership) and relation row carries ``source = 'oewn2025'``; the licence and
an attribution line are in the ``meta`` table and must be shown in the app.

How the XML is laid out (checked with a full element/attribute survey):

* One ``<Lexicon id="oewn" license=... version="2025" ...>``; its attributes are
  the header and go to ``meta``. The licence is given only as a URL, so a
  human-readable name ("Creative Commons Attribution 4.0 ...") is added.
* ``<LexicalEntry id>`` holds one ``<Lemma writtenForm partOfSpeech>`` (with
  optional ``<Pronunciation variety>`` children), optional ``<Form writtenForm>``
  irregular inflections (``mouse`` -> ``mice``, ``good`` -> ``better``), and
  ``<Sense id synset [subcat] [adjposition]>`` children in sense order. A
  sense's ``rank`` is its 1-based position in the entry. ``<SenseRelation
  relType target>`` hangs off a sense (antonym, derivation, pertainym, ...;
  ``relType="other"`` carries a ``dc:type`` subtype, kept in ``subtype``).
* Homographs are separate entries (``oewn-bass-n-1``, ``oewn-bass-n-2``), and
  case variants too (``march`` vs ``March``).
* ``<Synset id ili members partOfSpeech lexfile [dc:source]>`` holds one or more
  ``<Definition>`` (joined with "; " when there are several), ``<Example
  [dc:source]>`` and ``<SynsetRelation relType target>``. ``members`` lists the
  entry ids in the synset's own word order; it becomes ``senses.member_rank``.
  ``ili="in"`` marks a synset not yet in the Interlingual Index; it is stored as
  NULL so that ``ili`` can be used to link to French/German/Italian wordnets.
* Adjective synsets are ``a`` (head) or ``s`` (satellite); entries are almost
  always ``a`` even when the synset is ``s``. The API treats a/s as one
  "adjective" part of speech.
* The data lists both directions of every paired relation (hypernym/hyponym,
  holo_part/mero_part, ...), so a lookup only ever needs the ``src`` column.

Nothing in the output may mention "Gutenberg": one example sentence does
("Gutenberg's reproduction of holy texts ...") and is dropped (counted in the
report). A lemma, form or definition that mentioned it would stop the build.
The finished file is checked byte-for-byte.

Python API (all lookups are case-insensitive, all take an open connection):

    open_db(path=None) -> sqlite3.Connection         # read-only by default
    senses_for(conn, lemma, pos=None) -> list[dict]  # ordered by pos, entry, rank
    synset(conn, synset_id) -> dict | None
    members(conn, synset_id) -> list[str]
    related(conn, synset_id, rel) -> list[dict]
    antonyms(conn, sense_or_synset_id, indirect=False) -> list[dict]
    lemmatize(conn, word) -> list[tuple[str, str]]   # [(lemma, pos), ...]
    normalise_pos(pos) -> tuple[str, ...]            # 'adj' -> ('a', 's'), ...
    lookup_key(text) -> str                          # the case-insensitive key

Querying the tables directly: look lemmas up with ``entries.lemma_key =
lookup_key(word)`` and irregular forms with ``forms.form_key`` (both indexed;
the key is casefolded, with curly apostrophes and underscores normalised, which
SQLite's NOCASE cannot do for non-ASCII letters). ``senses.rank`` orders the
senses of an entry and ``senses.member_rank`` the words of a synset. Relation
tables are keyed by (src, rel, ...) so ``WHERE src = ? AND rel = ?`` is an
index lookup.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pipeline.pgtext import mentions_gutenberg

SOURCE_ID = "oewn2025"
SOURCE_NAME = "Open English WordNet"
ROOT = Path(__file__).resolve().parent.parent
XML = ROOT / "sources" / "english-wordnet-2025.xml.gz"
OUT = ROOT / "data" / "oewn.sqlite"
SCHEMA_VERSION = "1"

DC = "{https://globalwordnet.github.io/schemas/dc/}"

LICENCE_NAMES = {
    "creativecommons.org/licenses/by/4.0":
        "Creative Commons Attribution 4.0 International (CC BY 4.0)",
}

# Order parts of speech are listed in when no pos is asked for.
POS_ORDER = {"n": 0, "v": 1, "a": 2, "s": 2, "r": 3}
POS_ALIASES = {
    "n": ("n",), "noun": ("n",),
    "v": ("v",), "verb": ("v",),
    "a": ("a", "s"), "s": ("a", "s"), "adj": ("a", "s"), "adjective": ("a", "s"),
    "r": ("r",), "adv": ("r",), "adverb": ("r",),
}

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE synsets(
    id TEXT PRIMARY KEY,
    pos TEXT NOT NULL,            -- n v a s r (s = adjective satellite)
    ili TEXT,                     -- Interlingual Index id, NULL if not yet assigned
    lexfile TEXT,                 -- e.g. noun.object, adj.all
    definition TEXT NOT NULL,
    origin TEXT,                  -- dc:source of the synset (e.g. 'Colloquial WordNet'), rarely set
    source TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE examples(
    synset_id TEXT NOT NULL,
    seq INTEGER NOT NULL,         -- 1-based order within the synset
    text TEXT NOT NULL,
    origin TEXT,                  -- dc:source of the quotation (e.g. 'Charles Dickens'), rarely set
    source TEXT NOT NULL,
    PRIMARY KEY (synset_id, seq)
) WITHOUT ROWID;
CREATE TABLE entries(
    id TEXT PRIMARY KEY,
    lemma TEXT NOT NULL,
    lemma_key TEXT NOT NULL,      -- case-insensitive lookup key, see lookup_key()
    pos TEXT NOT NULL,
    source TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE forms(
    entry_id TEXT NOT NULL,
    form TEXT NOT NULL,
    form_key TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE pronunciations(
    entry_id TEXT NOT NULL,
    variety TEXT,                 -- GB, US, CA, ... or NULL
    ipa TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE senses(
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    lemma TEXT NOT NULL,
    pos TEXT NOT NULL,            -- the entry's part of speech
    synset_id TEXT NOT NULL,
    rank INTEGER NOT NULL,        -- order of the sense within its entry, 1 = first listed
    member_rank INTEGER,          -- order of the lemma within the synset's members, 1 = first
    adjposition TEXT,             -- a / p / ip for adjectives that only go in one position
    source TEXT NOT NULL
);
CREATE TABLE sense_relations(
    src_sense TEXT NOT NULL,
    rel TEXT NOT NULL,
    dst_sense TEXT NOT NULL,
    subtype TEXT NOT NULL DEFAULT '',  -- dc:type of relType="other" (agent, result, ...), else ''
    source TEXT NOT NULL,
    PRIMARY KEY (src_sense, rel, dst_sense, subtype)
) WITHOUT ROWID;
CREATE TABLE synset_relations(
    src TEXT NOT NULL,
    rel TEXT NOT NULL,
    dst TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (src, rel, dst)
) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX entries_key ON entries(lemma_key, pos);
CREATE INDEX forms_key ON forms(form_key);
CREATE INDEX forms_entry ON forms(entry_id);
CREATE INDEX pron_entry ON pronunciations(entry_id);
CREATE INDEX senses_entry ON senses(entry_id, rank);
CREATE INDEX senses_synset ON senses(synset_id, member_rank);
"""

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "_": " "})


def lookup_key(text: str) -> str:
    """Case- and apostrophe-insensitive key used for every lemma/form lookup."""
    return " ".join(text.translate(_APOSTROPHES).casefold().split())


def normalise_pos(pos) -> tuple:
    """Map 'n'/'noun', 'v'/'verb', 'a'/'s'/'adj', 'r'/'adv' to the WordNet codes."""
    if pos is None:
        return ()
    try:
        return POS_ALIASES[pos.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown part of speech {pos!r}") from None


def licence_name(url: str) -> str:
    bare = re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/")
    return LICENCE_NAMES.get(bare, url or "unknown")


# --------------------------------------------------------------------------- build

class GutenbergMention(ValueError):
    pass


def _check(text: str, what: str) -> str:
    if mentions_gutenberg(text):
        raise GutenbergMention(f"{what} mentions the forbidden word: {text!r}")
    return text


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_into(conn: sqlite3.Connection, xml_path: Path) -> dict:
    """Stream the WN-LMF file into the (empty) tables. Returns header + counters."""
    header: dict = {}
    stats = Counter()
    senses = []            # rows are completed with member_rank once synsets are read
    member_rank = {}       # (entry_id, synset_id) -> rank in synset members
    batch = {"entries": [], "forms": [], "pron": [], "srel": [], "synsets": [],
             "examples": [], "yrel": []}
    sql = {
        "entries": "INSERT INTO entries VALUES (?,?,?,?,?)",
        "forms": "INSERT INTO forms VALUES (?,?,?,?)",
        "pron": "INSERT INTO pronunciations VALUES (?,?,?,?)",
        "srel": "INSERT INTO sense_relations VALUES (?,?,?,?,?)",
        "synsets": "INSERT INTO synsets VALUES (?,?,?,?,?,?,?)",
        "examples": "INSERT INTO examples VALUES (?,?,?,?,?)",
        "yrel": "INSERT INTO synset_relations VALUES (?,?,?,?)",
    }

    def flush(force=False):
        for k, rows in batch.items():
            if rows and (force or len(rows) >= 20000):
                conn.executemany(sql[k], rows)
                rows.clear()

    lexicon = None
    lexicons = 0
    S = SOURCE_ID
    with gzip.open(xml_path, "rb") as f:
        for event, el in ET.iterparse(f, events=("start", "end")):
            tag = el.tag
            if event == "start":
                if tag == "Lexicon":
                    lexicons += 1
                    if lexicons > 1:
                        raise ValueError("more than one Lexicon in the file")
                    lexicon = el
                    header = dict(el.attrib)
                continue

            if tag == "LexicalEntry":
                eid = el.get("id")
                lem = el.find("Lemma")
                lemma = _check(lem.get("writtenForm"), f"lemma of {eid}")
                pos = lem.get("partOfSpeech")
                batch["entries"].append((eid, lemma, lookup_key(lemma), pos, S))
                stats["entries"] += 1
                for p in lem.iter("Pronunciation"):
                    if p.text and p.text.strip():
                        batch["pron"].append((eid, p.get("variety"), p.text.strip(), S))
                        stats["pronunciations"] += 1
                for fm in el.iter("Form"):
                    form = _check(fm.get("writtenForm"), f"form of {eid}")
                    batch["forms"].append((eid, form, lookup_key(form), S))
                    stats["forms"] += 1
                n_senses = 0
                for n_senses, sn in enumerate(el.iter("Sense"), 1):
                    sid = sn.get("id")
                    senses.append([sid, eid, lemma, pos, sn.get("synset"), n_senses, None,
                                   sn.get("adjposition"), S])
                    for r in sn.iter("SenseRelation"):
                        batch["srel"].append((sid, r.get("relType"), r.get("target"),
                                              r.get(DC + "type") or "", S))
                        stats["sense_relations"] += 1
                stats["senses"] += n_senses
                if not n_senses:
                    stats["entries_without_senses"] += 1

            elif tag == "Synset":
                yid = el.get("id")
                defs = [" ".join(d.text.split()) for d in el.findall("Definition")
                        if d.text and d.text.strip()]
                if len(defs) > 1:
                    stats["synsets_with_several_definitions"] += 1
                if not defs:
                    stats["synsets_without_definition"] += 1
                definition = _check("; ".join(defs), f"definition of {yid}")
                ili = el.get("ili")
                if ili in ("", "in"):
                    ili = None
                    stats["synsets_without_ili"] += 1
                lexfile = el.get("lexfile") or el.get(DC + "subject")
                batch["synsets"].append((yid, el.get("partOfSpeech"), ili, lexfile,
                                         definition, el.get(DC + "source"), S))
                stats["synsets"] += 1
                for i, m in enumerate((el.get("members") or "").split(), 1):
                    member_rank[(m, yid)] = i
                seq = 0
                for ex in el.findall("Example"):
                    text = " ".join((ex.text or "").split())
                    if not text:
                        continue
                    if mentions_gutenberg(text):
                        stats["examples_dropped_gutenberg"] += 1
                        continue
                    seq += 1
                    batch["examples"].append((yid, seq, text, ex.get(DC + "source"), S))
                    stats["examples"] += 1
                for r in el.findall("SynsetRelation"):
                    batch["yrel"].append((yid, r.get("relType"), r.get("target"), S))
                    stats["synset_relations"] += 1

            elif tag == "SyntacticBehaviour":
                pass
            else:
                continue                 # inner element: wait for its parent to end
            if lexicon is not None:
                lexicon.clear()          # drop finished top-level elements
            flush()
    flush(force=True)

    for row in senses:
        row[6] = member_rank.get((row[1], row[4]))
        if row[6] is None:
            stats["senses_not_in_synset_members"] += 1
    conn.executemany("INSERT INTO senses VALUES (?,?,?,?,?,?,?,?,?)", senses)
    return {"header": header, "stats": stats}


def build(xml_path: Path = XML, db_path: Path = OUT) -> dict:
    """Build the SQLite file from scratch (atomically replaces db_path). Returns a report.

    Nothing is left behind if the build fails: the half-written file is removed
    and an existing db_path is untouched.
    """
    t0 = time.time()
    xml_path, db_path = Path(xml_path), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        report = _write(conn, xml_path, t0)
        conn.close()
        if b"gutenberg" in tmp.read_bytes().lower():
            raise GutenbergMention("built database contains the forbidden word")
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, db_path)
    report = {"db": str(db_path), "db_bytes": db_path.stat().st_size,
              "seconds_total": round(time.time() - t0, 1), **report}
    return report


def _write(conn: sqlite3.Connection, xml_path: Path, t0: float) -> dict:
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(SCHEMA)
    with conn:
        res = parse_into(conn, xml_path)
    t_parse = time.time() - t0
    with conn:
        conn.executescript(INDEXES)
    header, stats = res["header"], res["stats"]
    checks = integrity(conn)
    lic_url = header.get("license", "")
    lic = licence_name(lic_url)
    version = header.get("version", "")
    label = header.get("label") or SOURCE_NAME
    changes = ["the text is unchanged except as listed here",
               "synsets with several definitions have them joined with '; '"]
    if stats["examples_dropped_gutenberg"]:
        changes.append(f"{stats['examples_dropped_gutenberg']} example sentence(s) removed")
    meta = {
        "source_id": SOURCE_ID,
        "source_name": label,
        "lexicon_id": header.get("id", ""),
        "language": header.get("language", ""),
        "version": version,
        "licence_url": lic_url,
        "licence": f"{lic} {lic_url}".strip(),
        "attribution": f"{label} ({version} edition), {header.get('url', '')}, "
                       f"licensed under {lic}.",
        "changes": "; ".join(changes),
        "url": header.get("url", ""),
        "email": header.get("email", ""),
        "source_file": xml_path.name,
        "source_sha256": _sha256(xml_path),
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("synsets", "examples", "entries", "forms", "pronunciations",
                        "senses", "sense_relations", "synset_relations")}
    meta["counts"] = json.dumps(counts, sort_keys=True)
    with conn:
        conn.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
    conn.execute("ANALYZE")
    conn.commit()
    conn.execute("VACUUM")
    return {
        "seconds_parse": round(t_parse, 1),
        "counts": counts,
        "stats": dict(sorted(stats.items())),
        "integrity": checks,
        "meta": {k: v for k, v in meta.items() if k != "counts"},
    }


def integrity(conn: sqlite3.Connection) -> dict:
    """Dangling-reference counts (all expected to be 0 for this edition)."""
    q = {
        "senses_to_missing_synset":
            "SELECT COUNT(*) FROM senses s LEFT JOIN synsets y ON y.id=s.synset_id WHERE y.id IS NULL",
        "sense_relations_to_missing_sense":
            "SELECT COUNT(*) FROM sense_relations r LEFT JOIN senses s ON s.id=r.dst_sense WHERE s.id IS NULL",
        "synset_relations_to_missing_synset":
            "SELECT COUNT(*) FROM synset_relations r LEFT JOIN synsets y ON y.id=r.dst WHERE y.id IS NULL",
        "synsets_without_senses":
            "SELECT COUNT(*) FROM synsets y WHERE NOT EXISTS (SELECT 1 FROM senses s WHERE s.synset_id=y.id)",
    }
    return {k: conn.execute(v).fetchone()[0] for k, v in q.items()}


# --------------------------------------------------------------------------- lookup API

def open_db(path=None, readonly: bool = True) -> sqlite3.Connection:
    """Open data/oewn.sqlite (or path). Read-only unless readonly=False."""
    path = Path(path) if path else OUT
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run: python3 -m pipeline.oewn")
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _in(n: int) -> str:
    return ",".join("?" * n)


def _examples(conn, synset_ids) -> dict:
    out = {s: [] for s in synset_ids}
    ids = list(out)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for sid, text in conn.execute(
                f"SELECT synset_id, text FROM examples WHERE synset_id IN ({_in(len(chunk))}) "
                "ORDER BY synset_id, seq", chunk):
            out[sid].append(text)
    return out


def _members(conn, synset_ids) -> dict:
    out = {s: [] for s in synset_ids}
    ids = list(out)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for sid, lemma in conn.execute(
                f"SELECT synset_id, lemma FROM senses WHERE synset_id IN ({_in(len(chunk))}) "
                "ORDER BY synset_id, member_rank IS NULL, member_rank, lemma", chunk):
            out[sid].append(lemma)
    return out


def _synset_dicts(conn, synset_ids) -> list:
    """Synset dicts in the order of synset_ids (unknown ids are skipped)."""
    ids = list(dict.fromkeys(synset_ids))
    if not ids:
        return []
    rows = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                "SELECT id, pos, ili, lexfile, definition, source FROM synsets "
                f"WHERE id IN ({_in(len(chunk))})", chunk):
            rows[r[0]] = r
    ex, mem = _examples(conn, rows), _members(conn, rows)
    return [{"synset_id": r[0], "pos": r[1], "ili": r[2], "lexfile": r[3],
             "definition": r[4], "examples": ex[r[0]], "members": mem[r[0]],
             "source": r[5]}
            for r in (rows.get(i) for i in ids) if r is not None]


def synset(conn, synset_id: str):
    """One synset as a dict (synset_id, pos, ili, lexfile, definition, examples, members, source)."""
    found = _synset_dicts(conn, [synset_id])
    return found[0] if found else None


def senses_for(conn, lemma: str, pos=None) -> list:
    """Every sense of a lemma (case-insensitive), optionally for one part of speech.

    Ordered by part of speech (n, v, adj, r), then the exact-case entry before
    case variants ("march" before "March"), then homograph entry, then rank.
    Each dict: sense_id, entry_id, lemma, pos (entry pos; 's' entries report 'a'),
    synset_id, synset_pos (n v a s r), rank, definition, examples, members
    (all lemmas of the synset in order), synonyms (members minus this lemma),
    lexfile, ili, source.
    """
    poss = normalise_pos(pos)
    where = "e.lemma_key = ?"
    args = [lookup_key(lemma)]
    if poss:
        where += f" AND e.pos IN ({_in(len(poss))})"
        args += list(poss)
    rows = conn.execute(
        "SELECT s.id, s.entry_id, e.lemma, e.pos, s.synset_id, y.pos, s.rank, "
        "y.definition, y.lexfile, y.ili, y.source "
        "FROM entries e JOIN senses s ON s.entry_id = e.id JOIN synsets y ON y.id = s.synset_id "
        f"WHERE {where} "
        "ORDER BY CASE e.pos WHEN 'n' THEN 0 WHEN 'v' THEN 1 WHEN 'a' THEN 2 "
        "WHEN 's' THEN 2 ELSE 3 END, e.lemma = ? DESC, e.id, s.rank",
        args + [lemma.strip()]).fetchall()
    ids = [r[4] for r in rows]
    ex, mem = _examples(conn, ids), _members(conn, ids)
    out = []
    for r in rows:
        own = lookup_key(r[2])
        out.append({
            "sense_id": r[0], "entry_id": r[1], "lemma": r[2],
            "pos": "a" if r[3] == "s" else r[3], "synset_id": r[4], "synset_pos": r[5],
            "rank": r[6], "definition": r[7], "examples": ex[r[4]], "members": mem[r[4]],
            "synonyms": [m for m in mem[r[4]] if lookup_key(m) != own],
            "lexfile": r[8], "ili": r[9], "source": r[10],
        })
    return out


def members(conn, synset_id: str) -> list:
    """Lemmas of a synset in the synset's own order."""
    return _members(conn, [synset_id])[synset_id]


def related(conn, synset_id: str, rel: str) -> list:
    """Synsets linked from synset_id by a synset relation (hypernym, hyponym,
    similar, also, mero_part, holo_part, attribute, entails, causes, domain_topic, ...).
    Returns synset dicts (see synset()), ordered by synset id."""
    dst = [r[0] for r in conn.execute(
        "SELECT dst FROM synset_relations WHERE src = ? AND rel = ? ORDER BY dst",
        (synset_id, rel))]
    return _synset_dicts(conn, dst)


def _sense_dicts(conn, sense_ids, via=None) -> list:
    ids = list(dict.fromkeys(sense_ids))
    if not ids:
        return []
    rows = {r[0]: r for r in conn.execute(
        "SELECT s.id, s.lemma, s.pos, s.synset_id, y.definition, s.source FROM senses s "
        f"JOIN synsets y ON y.id = s.synset_id WHERE s.id IN ({_in(len(ids))})", ids)}
    return [{"sense_id": r[0], "lemma": r[1], "pos": "a" if r[2] == "s" else r[2],
             "synset_id": r[3], "definition": r[4], "source": r[5],
             "indirect": bool(via and r[0] in via)}
            for r in (rows.get(i) for i in ids) if r is not None]


def antonyms(conn, ref: str, indirect: bool = False) -> list:
    """Antonyms of a sense id, or of every sense in a synset id.

    Antonymy is between senses (words), not synsets. Each result: sense_id,
    lemma, pos, synset_id, definition, source, indirect. With indirect=True an
    adjective satellite with no antonym of its own also gets the antonyms of
    the head synsets it is 'similar' to (bright 'intelligent' -> 'unintelligent').
    """
    if conn.execute("SELECT 1 FROM senses WHERE id = ?", (ref,)).fetchone():
        src = [ref]
        synset_ids = [conn.execute("SELECT synset_id FROM senses WHERE id = ?",
                                   (ref,)).fetchone()[0]]
    else:
        src = [r[0] for r in conn.execute(
            "SELECT id FROM senses WHERE synset_id = ? ORDER BY member_rank", (ref,))]
        synset_ids = [ref]
    direct = _antonym_ids(conn, src)
    via = set()
    if indirect and not direct:
        heads = [r[0] for r in conn.execute(
            f"SELECT r.dst FROM synset_relations r JOIN synsets y ON y.id = r.dst "
            f"WHERE r.src IN ({_in(len(synset_ids))}) AND r.rel = 'similar' AND y.pos = 'a'",
            synset_ids)]
        for h in heads:
            hs = [r[0] for r in conn.execute(
                "SELECT id FROM senses WHERE synset_id = ? ORDER BY member_rank", (h,))]
            via.update(_antonym_ids(conn, hs))
    ordered = direct + [v for v in sorted(via) if v not in direct]
    return _sense_dicts(conn, ordered, via=set(via) - set(direct))


def _antonym_ids(conn, sense_ids) -> list:
    out = []
    for sid in sense_ids:              # keep the order of the source senses
        out += [r[0] for r in conn.execute(
            "SELECT dst_sense FROM sense_relations WHERE src_sense = ? AND rel = 'antonym' "
            "ORDER BY dst_sense", (sid,))]
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------- lemmatizer

_VOWELS = set("aeiou")

# (suffix, replacement) tried per part of speech, WordNet "morphy" style plus
# doubled consonants (stopped, bigger), -ied/-ier/-iest -> y and -ying -> ie.
_RULES = {
    "n": [("s", ""), ("ses", "s"), ("xes", "x"), ("zes", "z"), ("ches", "ch"),
          ("shes", "sh"), ("ies", "y"), ("oes", "o"), ("ves", "f"), ("ves", "fe"),
          ("men", "man")],
    "v": [("s", ""), ("es", ""), ("es", "e"), ("ies", "y"), ("ed", ""), ("ed", "e"),
          ("ied", "y"), ("ing", ""), ("ing", "e"), ("ying", "ie")],
    "a": [("er", ""), ("est", ""), ("er", "e"), ("est", "e"), ("ier", "y"), ("iest", "y")],
    "r": [("er", ""), ("est", ""), ("ier", "y"), ("iest", "y")],
}
_DOUBLING = {"v": ("ed", "ing"), "a": ("er", "est"), "r": ("er", "est")}
_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def _rule_candidates(word: str):
    """(lemma, pos) guesses from suffix rules, most likely first, not yet checked."""
    for pos, rules in _RULES.items():
        for suf, rep in rules:
            if word.endswith(suf) and len(word) - len(suf) + len(rep) >= 2:
                yield word[: len(word) - len(suf)] + rep, pos
        for suf in _DOUBLING.get(pos, ()):
            stem = word[: -len(suf)] if word.endswith(suf) else ""
            if (len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in _VOWELS
                    and stem[-1] not in "sy"):
                yield stem[:-1], pos          # stopped -> stop, bigger -> big


def lemmatize(conn, word: str) -> list:
    """Dictionary lemmas a word form can belong to: [(lemma, pos), ...].

    pos is n, v, a (adjective, including satellites) or r. Order: the word
    itself where it is a lemma, then irregular forms listed in the data
    (mice -> mouse, better -> good/well), then suffix rules (ducks -> duck,
    drifted -> drift, waiting -> wait, stopping -> stop, brighter -> bright).
    Possessive 's and surrounding punctuation are stripped first. Only lemmas
    that exist in the database are returned.
    """
    w = lookup_key(_EDGE_PUNCT.sub("", word or ""))
    if not w:
        return []
    forms = [w]
    for poss in ("'s", "s'"):
        if w.endswith(poss) and len(w) > len(poss):
            forms.append(w[: -len(poss)] + ("s" if poss == "s'" else ""))
    out = []

    def add(rows):
        for lemma, pos in rows:
            item = (lemma, "a" if pos == "s" else pos)
            if item not in out:
                out.append(item)

    def lemmas(key, pos=None):
        if pos is None:
            q = "SELECT lemma, pos FROM entries WHERE lemma_key = ?"
            args = (key,)
        else:
            q = f"SELECT lemma, pos FROM entries WHERE lemma_key = ? AND pos IN ({_in(len(POS_ALIASES[pos]))})"
            args = (key, *POS_ALIASES[pos])
        rows = conn.execute(q + " ORDER BY lemma != ?, id", (*args, key)).fetchall()
        return sorted(rows, key=lambda r: POS_ORDER.get(r[1], 9))

    for f in forms:
        add(lemmas(f))
    for f in forms:
        add(sorted(conn.execute(
            "SELECT e.lemma, e.pos FROM forms f JOIN entries e ON e.id = f.entry_id "
            "WHERE f.form_key = ? ORDER BY e.id", (f,)).fetchall(),
            key=lambda r: POS_ORDER.get(r[1], 9)))
    for f in forms:
        for cand, pos in _rule_candidates(f):
            add(lemmas(cand, pos))
    return out


# --------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    xml_path = Path(argv[0]) if argv else XML
    db_path = Path(argv[1]) if len(argv) > 1 else OUT
    report = build(xml_path, db_path)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
