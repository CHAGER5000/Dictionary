"""Build the compact database the iPad/iPhone app ships with (data/app_en.sqlite),
and a reference meaning-picker that works only from that database.

The app cannot run the full picker in pipeline/context.py (it follows WordNet
relations at lookup time). Instead this build precomputes, for every meaning,
its clue words with their weights (definition, examples, synonyms, related
meanings, old-book synonyms, each already multiplied by the word's rarity), so
the app only has to add up the clue words it finds in the sentence.

app_pick() below is the specification of the Swift MeaningPicker: same tables,
same arithmetic. pipeline/evaluate.py-style numbers for it come from
"python3 -m pipeline.appdb --eval".

Run: python3 -m pipeline.appdb            (build data/app_en.sqlite)
     python3 -m pipeline.appdb --eval     (build if missing, then score the test set)
"""
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from pipeline import context, oewn

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "app_en.sqlite"
EVAL = ROOT / "tests" / "data" / "wsd_eval.jsonl"
SCHEMA_VERSION = "1"

SIG_KEEP = 60          # clue words kept per meaning (highest weight first)
SYN_KEEP = 12          # synonyms kept per meaning
PRIOR = context.PRIOR
DIST_DECAY = context.DIST_DECAY
MIN_CLUE = context.MIN_CLUE
CLUE_SHARE = context.CLUE_SHARE
INDIRECT = context.INDIRECT
INDIRECT_MAX = context.INDIRECT_MAX
SIG_REF = context.INDIRECT_SIG_REF
POS_BONUS = context.POS_BONUS
POS_OTHER = context.POS_OTHER
EXP_KEEP = 40          # linked words kept per clue word
WORD_LISTS = {
    "determiners": context._DETERMINERS, "subjects": context._SUBJECTS, "modals": context._MODALS,
    "copulas": context._COPULAS, "degree": context._DEGREE, "prepositions": context._PREPOSITIONS,
    "generic": context.GENERIC,
}

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE stop(key TEXT PRIMARY KEY) WITHOUT ROWID;
-- a word as written (lookup key) -> the dictionary words it can stand for, most likely first
CREATE TABLE tok(form TEXT NOT NULL, key TEXT NOT NULL, ord INTEGER NOT NULL,
                 PRIMARY KEY (form, ord)) WITHOUT ROWID;
-- dictionary word -> its meanings in WordNet order
CREATE TABLE entry(key TEXT NOT NULL, lemma TEXT NOT NULL, pos TEXT NOT NULL,
                   synset TEXT NOT NULL, rank INTEGER NOT NULL,
                   PRIMARY KEY (key, synset)) WITHOUT ROWID;
-- sigsize: how many clue words the meaning has before trimming (weakens links
-- through clues' own meanings for meanings with very many words)
CREATE TABLE meaning(mid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, pos TEXT NOT NULL,
                     definition TEXT NOT NULL, example TEXT, sigsize INTEGER NOT NULL DEFAULT 0);
CREATE TABLE syn(synset TEXT NOT NULL, ord INTEGER NOT NULL, word TEXT NOT NULL,
                 source TEXT NOT NULL, PRIMARY KEY (synset, ord)) WITHOUT ROWID;
CREATE TABLE ant(synset TEXT NOT NULL, word TEXT NOT NULL,
                 PRIMARY KEY (synset, word)) WITHOUT ROWID;
-- clue words of a meaning, stored as numbers to keep the file small:
-- mid = meaning.mid, kid = kw.kid, w = weight x rarity (idf) x 100, rounded
CREATE TABLE kw(kid INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE sig(mid INTEGER NOT NULL, kid INTEGER NOT NULL, w INTEGER NOT NULL,
                 PRIMARY KEY (mid, kid)) WITHOUT ROWID;
-- what a clue word's own meanings talk about (kid -> ekid, w = weight 0..1 x 100):
-- "boat" -> water, vessel, ... lets "boat" point to the river bank through "water"
CREATE TABLE exp(kid INTEGER NOT NULL, ekid INTEGER NOT NULL, w INTEGER NOT NULL,
                 PRIMARY KEY (kid, ekid)) WITHOUT ROWID;
CREATE TABLE ipa(key TEXT PRIMARY KEY, ipa TEXT NOT NULL) WITHOUT ROWID;
-- "sounds like" search: sound key -> word
CREATE TABLE sound(skey TEXT NOT NULL, key TEXT NOT NULL,
                   PRIMARY KEY (skey, key)) WITHOUT ROWID;
"""

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


# ---------------------------------------------------------------- sound keys

def sound_key(word: str) -> str:
    """A rough 'how it sounds' key, so children can type words as they hear them
    ("nolij" and "knowledge" both give "nlj"). The Swift SoundKey must give the
    same result for the same input; tests/data/sound_keys.json holds shared cases.

    Steps: lower-case letters only; common spellings of one sound folded
    (kn gn wr ph gh ck dge tch qu x c/g before e i y, sh ch th); then the first
    letter kept and later vowels (and y, w, h) dropped; doubled letters collapsed.
    """
    w = "".join(ch for ch in word.lower() if "a" <= ch <= "z")
    if not w:
        return ""
    for a, b in (("^kn", "n"), ("^gn", "n"), ("^wr", "r"), ("^ps", "s"), ("^wh", "w"),
                 ("mb$", "m"), ("dge", "j"), ("tch", "ch"), ("ck", "k"), ("ph", "f"),
                 ("gh", ""), ("qu", "kw"), ("x", "ks"), ("sch", "sk"),
                 ("c(?=[eiy])", "s"), ("g(?=[eiy])", "j"), ("c", "k"), ("q", "k"),
                 ("sh", "S"), ("ch", "C"), ("th", "T"), ("z", "s"), ("v", "f")):
        w = re.sub(a, b, w)
    if not w:
        return ""
    head, rest = w[0], w[1:]
    rest = re.sub(r"[aeiouyhw]", "", rest)
    if head in "aeiouy":
        head = "a"
    out = head
    for ch in rest:
        if ch != out[-1]:
            out += ch
    return out


# ---------------------------------------------------------------- build

def _kid_safe_synsets(conn):
    unfit = {k for k, in conn.execute("SELECT key FROM kid_filter WHERE kind = 'synset'")}
    bad_words = {k for k, in conn.execute("SELECT key FROM kid_filter WHERE kind = 'word'")}
    return unfit, bad_words


def build(src=None, out: Path = OUT, log=print) -> dict:
    t0 = time.time()
    conn = context.open_english(src)
    st = context._store(conn)
    lex = st.lex
    unfit, bad_words = _kid_safe_synsets(conn)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)

    # words: lower-case lemmas of at most two words, not on the blocklist
    rows = conn.execute(
        "SELECT e.lemma, e.lemma_key, e.pos, s.synset_id, s.rank FROM entries e "
        "JOIN senses s ON s.entry_id = e.id ORDER BY e.lemma_key, s.rank").fetchall()
    entries, keys = [], {}
    for lemma, key, pos, sid, rank in rows:
        if sid in unfit or key in bad_words or lemma[:1].isupper() or key.count(" ") > 1:
            continue
        p = "a" if pos == "s" else pos
        entries.append((key, lemma, p, sid, rank))
        keys.setdefault(key, set()).add(p)
    # a word can be listed under n and v with separate rank sequences; keep the lowest
    seen = {}
    for e in entries:
        k = (e[0], e[3])
        if k not in seen or e[4] < seen[k][4]:
            seen[k] = e
    entries = sorted(seen.values(), key=lambda e: (e[0], e[4]))
    db.executemany("INSERT INTO entry VALUES (?,?,?,?,?)", entries)
    sids = sorted({e[3] for e in entries})
    log(f"words {len(keys)}, meanings {len(sids)}")

    # meanings: definition and first example
    info = {}
    for chunk in range(0, len(sids), 900):
        part = sids[chunk:chunk + 900]
        q = ",".join("?" * len(part))
        for sid, pos, d in conn.execute(f"SELECT id, pos, definition FROM synsets WHERE id IN ({q})", part):
            info[sid] = [pos, d, None]
        for sid, text in conn.execute(
                f"SELECT synset_id, text FROM examples WHERE synset_id IN ({q}) AND seq = 1", part):
            info[sid][2] = text
    mids = {sid: i + 1 for i, sid in enumerate(sids)}
    db.executemany("INSERT INTO meaning (mid, id, pos, definition, example) VALUES (?,?,?,?,?)",
                   [(mids[s], s, "a" if v[0] == "s" else v[0], v[1], v[2]) for s, v in info.items()])
    kids = {}
    sizes = {}

    # synonyms per meaning: WordNet members first, then the old books by score
    syn_rows = []
    for chunk in range(0, len(sids), 900):
        part = sids[chunk:chunk + 900]
        q = ",".join("?" * len(part))
        cur = {}
        for sid, word, src_id, score in conn.execute(
                f"SELECT synset_id, word, source, score FROM sense_synonyms WHERE synset_id IN ({q}) "
                "ORDER BY synset_id, source != 'oewn2025', score DESC, word", part):
            lst = cur.setdefault(sid, [])
            if len(lst) < SYN_KEEP and oewn.lookup_key(word) not in bad_words:
                lst.append((word, src_id))
        for sid, lst in cur.items():
            syn_rows += [(sid, i + 1, w, s) for i, (w, s) in enumerate(lst)]
    db.executemany("INSERT INTO syn VALUES (?,?,?,?)", syn_rows)

    # antonyms (sense-level in WordNet)
    ant = conn.execute(
        "SELECT DISTINCT a.synset_id, b.lemma FROM sense_relations r "
        "JOIN senses a ON a.id = r.src_sense JOIN senses b ON b.id = r.dst_sense "
        "WHERE r.rel = 'antonym'").fetchall()
    sidset = set(sids)
    db.executemany("INSERT OR IGNORE INTO ant VALUES (?,?)",
                   [(s, w) for s, w in ant if s in sidset and oewn.lookup_key(w) not in bad_words])

    # clue words per meaning, weight x idf, strongest SIG_KEEP
    t1 = time.time()
    sig_rows = 0
    batch = []
    for i in range(0, len(sids), 400):
        part = sids[i:i + 400]
        st.load(part)
        st.book_words(part)
        rels = st.relations(part)
        st.load([d for sid in part for _, d in rels[sid]])
        sigs = {sid: st.signature(sid, frozenset()) for sid in part}
        idf = st.idf_of(list({k for s in sigs.values() for k in s}))
        for sid, sig in sigs.items():
            sizes[sid] = len(sig)
            scored = sorted(((w * idf[k], k) for k, (w, _) in sig.items()), reverse=True)[:SIG_KEEP]
            for v, k in scored:
                if v >= 0.05:
                    kid = kids.setdefault(k, len(kids) + 1)
                    batch.append((mids[sid], kid, round(v * 100)))
        if len(batch) > 200000:
            db.executemany("INSERT INTO sig VALUES (?,?,?)", batch)
            sig_rows += len(batch)
            batch = []
        st.sig.clear()
        if i % 20000 == 0:
            log(f"  clue words: {i}/{len(sids)} meanings, {time.time() - t1:.0f}s")
    db.executemany("INSERT INTO sig VALUES (?,?,?)", batch)
    sig_rows += len(batch)
    db.executemany("UPDATE meaning SET sigsize = ? WHERE mid = ?", [(n, mids[sid]) for sid, n in sizes.items()])

    # clue-word links: for every one-word dictionary word, what its own meanings talk about
    t2 = time.time()
    one_word = sorted(k for k in keys if " " not in k and k not in context.STOP_WORDS and k not in context.GENERIC)
    exp_rows = 0
    batch = []
    for i in range(0, len(one_word), 2000):
        part = one_word[i:i + 2000]
        st.prefetch(part)
        for k in part:
            mean, _ = st.expand(k)
            if not mean:
                continue
            kid = kids.setdefault(k, len(kids) + 1)
            for ek, w in sorted(mean.items(), key=lambda kv: -kv[1])[:EXP_KEEP]:
                if round(w * 100) > 0:
                    batch.append((kid, kids.setdefault(ek, len(kids) + 1), round(w * 100)))
        st.expansion.clear()
        if len(batch) > 200000:
            db.executemany("INSERT INTO exp VALUES (?,?,?)", batch)
            exp_rows += len(batch)
            batch = []
    db.executemany("INSERT INTO exp VALUES (?,?,?)", batch)
    exp_rows += len(batch)
    log(f"  clue links: {exp_rows} rows, {time.time() - t2:.0f}s")
    db.executemany("INSERT INTO kw VALUES (?,?)", [(v, k) for k, v in kids.items()])

    # word forms -> dictionary words (what the app looks up for each word it reads)
    forms = set(keys)
    for key, poss in keys.items():
        for p in poss:
            forms |= context._inflections(key, p)
    forms |= {f for f, in conn.execute("SELECT form_key FROM forms")}
    tok_rows = []
    for f in sorted(forms):
        ks = [k for k in lex.keys(f) if k in keys]
        if ks and ks != [f]:          # a word that stands only for itself needs no row
            tok_rows += [(f, k, i + 1) for i, k in enumerate(ks)]
    db.executemany("INSERT INTO tok VALUES (?,?,?)", tok_rows)
    db.executemany("INSERT INTO stop VALUES (?)", [(k,) for k in sorted(context.STOP_WORDS)])

    # pronunciation (British first) and sound keys
    ipa = {}
    for key, variety, text in conn.execute(
            "SELECT e.lemma_key, p.variety, p.ipa FROM pronunciations p JOIN entries e ON e.id = p.entry_id "
            "ORDER BY p.variety != 'GB', p.variety IS NULL"):
        if key in keys and key not in ipa:
            ipa[key] = text
    db.executemany("INSERT INTO ipa VALUES (?,?)", sorted(ipa.items()))
    db.executemany("INSERT OR IGNORE INTO sound VALUES (?,?)",
                   [(sound_key(k), k) for k in keys if " " not in k and sound_key(k)])

    meta = {
        "schema_version": SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "language": "en",
        "prior": str(PRIOR), "dist_decay": str(DIST_DECAY),
        "min_clue": str(MIN_CLUE), "clue_share": str(CLUE_SHARE),
        "indirect": str(INDIRECT), "indirect_max": str(INDIRECT_MAX), "sig_ref": str(SIG_REF),
        "pos_bonus": str(POS_BONUS), "pos_other": str(POS_OTHER),
    }
    for name, words in WORD_LISTS.items():
        meta["words_" + name] = " ".join(sorted(words))
    for k in ("attribution", "attribution_soule1871", "attribution_roget1911",
              "princeton_notice", "princeton_licence", "licence_url"):
        v = conn.execute("SELECT value FROM meta WHERE key = ?", (k,)).fetchone()
        if v:
            meta[k] = v[0]
    db.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
    db.commit()
    db.execute("ANALYZE")
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    stats = {"words": len(keys), "meanings": len(sids), "clue_rows": sig_rows, "link_rows": exp_rows,
             "form_rows": len(tok_rows), "synonym_rows": len(syn_rows),
             "mb": round(out.stat().st_size / 1e6, 1), "seconds": round(time.time() - t0)}
    log(json.dumps(stats))
    return stats


# ---------------------------------------------------------------- reference picker

class AppLexicon:
    """Lookups against the app database only (what the Swift code can see)."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.stop = {k for k, in db.execute("SELECT key FROM stop")}
        meta = dict(db.execute("SELECT key, value FROM meta"))
        self.lists = {n: set(meta.get("words_" + n, "").split()) for n in WORD_LISTS}
        self.num = {k: float(meta[k]) for k in ("prior", "dist_decay", "min_clue", "clue_share", "indirect",
                                                "indirect_max", "sig_ref", "pos_bonus", "pos_other") if k in meta}
        self._keys = {}
        self._pos = {}
        self._kid = {}

    def keys(self, word: str) -> list:
        k = oewn.lookup_key(word)
        got = self._keys.get(k)
        if got is None:
            got = [r for r, in self.db.execute("SELECT key FROM tok WHERE form = ? ORDER BY ord", (k,))]
            if not got:
                got = [k]
            self._keys[k] = got
        return got

    def is_stop(self, word: str) -> bool:
        k = oewn.lookup_key(word)
        return k in self.stop or len(k) < 2

    def parts_of_speech(self, word: str) -> set:
        """Parts of speech a written word can be (n v a r), from its dictionary words."""
        k = oewn.lookup_key(word)
        got = self._pos.get(k)
        if got is None:
            got = set()
            for key in self.keys(k):
                got |= {p for p, in self.db.execute("SELECT DISTINCT pos FROM entry WHERE key = ?", (key,))}
            self._pos[k] = got
        return got

    def links(self, key: str) -> dict:
        """What a clue word's own meanings talk about: key -> weight 0..1."""
        got = self._kid.get(key)
        if got is None:
            got = {k: w / 100 for k, w in self.db.execute(
                "SELECT e.key, x.w FROM kw c JOIN exp x ON x.kid = c.kid JOIN kw e ON e.kid = x.ekid "
                "WHERE c.key = ?", (key,))}
            self._kid[key] = got
        return got


def pos_cues(lex: AppLexicon, low: list, i: int) -> dict:
    """Part-of-speech hints from the words just before and after token i
    ({pos: strength}); a smaller version of pipeline/context.py pos_cues."""
    L = lex.lists
    prev = low[i - 1] if i > 0 else None
    nxt = low[i + 1] if i + 1 < len(low) else None
    can_be = lex.parts_of_speech(low[i])
    next_is_noun = nxt is not None and nxt not in lex.stop and "n" in lex.parts_of_speech(nxt)
    possessive = bool(prev and (prev.endswith("'s") or prev.endswith("s'")))
    cues = {}

    def cue(p, v):
        cues[p] = cues.get(p, 0.0) + v

    def determiner_before(reach=3):
        if i < 2 or low[i - 1] in lex.stop or low[i - 1] in L["degree"]:
            return False
        for j in range(i - 2, max(i - 2 - reach, -1), -1):
            if low[j] in L["determiners"]:
                return True
            if low[j] in lex.stop:
                return False
        return False

    if prev in L["determiners"] or possessive:
        cue("a" if next_is_noun and "a" in can_be else "n", 1.0)      # "the bank", "the right answer"
    elif determiner_before():
        cue("a" if next_is_noun and "a" in can_be else "n", 0.8)      # "the old stone well"
    elif prev in L["prepositions"]:
        cue("n", 0.6)
    if prev in L["modals"] or prev in L["subjects"] or prev == "please":
        cue("v", 1.0)                                                  # "to wind", "we plant"
    if prev in L["copulas"]:
        if low[i].endswith("ing"):
            cue("v", 0.6)
        cue("a", 0.8)                                                  # "isn't fair"
    if prev in L["degree"]:
        cue("a", 1.0)                                                  # "very cross"
    return {p: min(v, 1.5) for p, v in cues.items()}


def app_pick(db: sqlite3.Connection, sentence: str, target: str, at=None, lex=None) -> list:
    """Rank the meanings of target in sentence. The Swift MeaningPicker does exactly this."""
    lex = lex or AppLexicon(db)
    n = lex.num
    tokens = [(m.group(0), m.start()) for m in _TOKEN_RE.finditer(sentence)]
    low = [oewn.lookup_key(t) for t, _ in tokens]
    tkey = oewn.lookup_key(target)
    ti = -1
    for j, (tok, start) in enumerate(tokens):
        if low[j] == tkey and (at is None or start <= at < start + len(tok)):
            ti = j
            break
    if ti < 0:
        for j, (tok, _) in enumerate(tokens):
            if set(lex.keys(tok)) & set(lex.keys(target)):
                ti = j
                break
    tword = tokens[ti][0] if ti >= 0 else target
    lemma_keys = lex.keys(tword)
    cands, seen = [], set()
    for lk in lemma_keys:
        for lemma, pos, sid, rank in db.execute(
                "SELECT lemma, pos, synset, rank FROM entry WHERE key = ? ORDER BY rank, pos", (lk,)):
            if sid not in seen:
                seen.add(sid)
                cands.append((sid, lemma, pos, rank))
    if not cands:
        return []
    drop = {oewn.lookup_key(tword), *lemma_keys}
    q = ",".join("?" * len(lemma_keys))
    drop |= {f for f, in db.execute(f"SELECT form FROM tok WHERE key IN ({q})", lemma_keys)}

    clues = {}
    for j, (tok, _) in enumerate(tokens):
        if j == ti or lex.is_stop(tok):
            continue
        ks = [k for k in lex.keys(tok) if k not in lex.stop]
        if not ks or any(k in drop for k in ks) or low[j] in drop:
            continue
        dist = abs(j - ti) if ti >= 0 else 5
        dw = 1.0 / (1.0 + n["dist_decay"] * max(dist - 1, 0))
        if ks[0] not in clues or dw > clues[ks[0]][2]:
            clues[ks[0]] = (tok, ks, dw)
    generic = lex.lists["generic"]
    linking = {first: [k for k in ks[:2] if k not in generic] for first, (_, ks, _) in clues.items()}

    cues = pos_cues(lex, low, ti) if ti >= 0 else {}
    top_pos, top_cue = max(cues.items(), key=lambda kv: kv[1]) if cues else (None, 0.0)

    out = []
    for sid, lemma, pos, rank in cands:
        mid, d, size = db.execute("SELECT mid, definition, sigsize FROM meaning WHERE id = ?", (sid,)).fetchone()
        sig = {k: w / 100 for k, w in db.execute(
            "SELECT kw.key, sig.w FROM sig JOIN kw ON kw.kid = sig.kid WHERE sig.mid = ?", (mid,))}
        size_factor = min(1.0, (n["sig_ref"] / size) ** 0.5) if size else 1.0
        total, per = 0.0, []
        for first, (tok, ks, dw) in clues.items():
            direct = max((sig.get(k, 0.0) for k in ks), default=0.0)
            ind = 0.0
            for k in linking[first]:
                bag = lex.links(k)
                avg = sum(sig[e] * w for e, w in bag.items() if e in sig)
                ind = max(ind, avg)
            ind = min(n["indirect"] * ind * size_factor, n["indirect_max"])
            best = max(direct, ind) * dw
            if best > 0:
                total += best
                per.append((best, tok, first in generic))
        if top_pos and pos != top_pos and cues.get(pos, 0.0) < top_cue - 0.5:
            total *= 1.0 - (1.0 - n["pos_other"]) * min(top_cue, 1.0)
        score = total + n["prior"] / rank + n["pos_bonus"] * cues.get(pos, 0.0)
        per.sort(key=lambda x: -x[0])
        shown = [t for v, t, general in per
                 if v >= n["min_clue"] and v >= n["clue_share"] * total and not general]
        out.append({"synset_id": sid, "definition": d, "score": round(score, 3),
                    "clues": list(dict.fromkeys(shown)), "lemma": lemma, "pos": pos, "rank": rank})
    out.sort(key=lambda x: (-x["score"], x["rank"]))
    return out


def evaluate(out: Path = OUT) -> dict:
    db = sqlite3.connect(out)
    lex = AppLexicon(db)
    items = [json.loads(l) for l in EVAL.read_text().splitlines() if l.strip()]
    res = {}
    for split in ("dev", "test"):
        sel = [i for i in items if i["split"] == split]
        ok = top3 = first = 0
        for it in sel:
            ranked = app_pick(db, it["sentence"], it["target"], lex=lex)
            ids = [r["synset_id"] for r in ranked]
            ok += bool(ids) and ids[0] in it["gold"]
            top3 += any(i in it["gold"] for i in ids[:3])
            by_rank = sorted(ranked, key=lambda r: r["rank"])
            first += bool(by_rank) and by_rank[0]["synset_id"] in it["gold"]
        n = len(sel)
        res[split] = {"n": n, "accuracy": round(ok / n, 3), "top3": round(top3 / n, 3),
                      "first_sense": round(first / n, 3)}
    return res


if __name__ == "__main__":
    if "--eval" in sys.argv:
        if not OUT.exists():
            build()
        print(json.dumps(evaluate(), indent=1))
    else:
        build()
