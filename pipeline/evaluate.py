"""Evaluate the context sense picker on tests/data/wsd_eval.jsonl.

    python3 -m pipeline.evaluate                 # dev and test accuracy, baselines, test failures
    python3 -m pipeline.evaluate --split dev     # one split only (use this while tuning)
    python3 -m pipeline.evaluate --no-indirect   # the same with the indirect clue channel off
    python3 -m pipeline.evaluate --json          # the same numbers as JSON

A prediction is correct if the picker's top meaning is any of the item's gold
synsets. The picker is told only the sentence and the tapped word; it has to
work out the part of speech itself.

Baselines (they are given the item's lemma and part of speech, which the picker
is not):
* first sense: WordNet's rank-1 sense of the gold lemma with the gold part of
  speech (the usual "most frequent sense" baseline);
* first sense, no part of speech: the first sense of the first lemma the word
  form lemmatises to, which is what the app would show with no sentence.

Accuracy comes with a 95% Wilson interval. For test it is also given on the
items whose word has no item in dev, since 36 of the 55 words have items in
both splits and the picker was tuned on dev.

Time per call is measured three ways: "fresh" empties the picker's caches
before each item (the database file stays in the operating system's cache),
"shared" keeps the caches filled by earlier items (after warm_up), and
"repeat" calls the same item again.

The parameters were tuned on the dev split only; test is for reporting.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import statistics
import sys
import time
from pathlib import Path

from pipeline import context, oewn
from pipeline.context import open_english, pick_sense, warm_up

EVAL = Path(__file__).resolve().parent.parent / "tests" / "data" / "wsd_eval.jsonl"


def load_items(path=EVAL, split=None) -> list:
    with open(path, encoding="utf-8") as fh:
        items = [json.loads(line) for line in fh if line.strip()]
    return [it for it in items if split is None or it["split"] == split]


def first_sense(conn, lemma, pos):
    s = oewn.senses_for(conn, lemma, pos)
    return s[0]["synset_id"] if s else None


def first_sense_no_pos(conn, word):
    for lemma, pos in oewn.lemmatize(conn, word):
        s = oewn.senses_for(conn, lemma, pos)
        if s:
            return s[0]["synset_id"]
    return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson score interval for k successes out of n."""
    if not n:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(centre - half, 4), round(centre + half, 4))


def _fresh_ms(conn, it) -> float:
    """One call with the picker's caches emptied first."""
    context._STORES.clear()
    context._LEXICONS.clear()
    gc.collect()
    t0 = time.perf_counter()
    pick_sense(conn, it["sentence"], it["target"])
    return (time.perf_counter() - t0) * 1000


def _summary(ms: list) -> dict:
    if not ms:
        return {"mean": 0.0, "median": 0.0, "p90": 0.0, "max": 0.0}
    q = statistics.quantiles(ms, n=10) if len(ms) > 1 else ms * 9
    return {"mean": round(statistics.mean(ms), 2), "median": round(statistics.median(ms), 2),
            "p90": round(q[8], 2), "max": round(max(ms), 2)}


def evaluate(conn, items, timing: bool = True) -> dict:
    """Accuracy of pick_sense and the baselines on items, plus per-item results."""
    gc.unfreeze()                                     # an earlier warm_up may have frozen it
    fresh = [_fresh_ms(conn, it) for it in items] if timing else []
    for it in items[:3]:                              # warm-up (caches, page cache)
        pick_sense(conn, it["sentence"], it["target"])
    warm_up(conn)                                     # ... and freeze the GC's view of them
    rows, times = [], []
    for i, it in enumerate(items):
        t0 = time.perf_counter()
        ranked = pick_sense(conn, it["sentence"], it["target"])
        times.append((time.perf_counter() - t0) * 1000)
        again = time.perf_counter()
        pick_sense(conn, it["sentence"], it["target"])
        warm = (time.perf_counter() - again) * 1000
        top = ranked[0] if ranked else None
        gold = set(it["gold"])
        pos_rank = next((i + 1 for i, r in enumerate(ranked) if r["synset_id"] in gold), None)
        rows.append({
            "id": it["id"], "sentence": it["sentence"], "target": it["target"],
            "gold": it["gold"], "gold_definition": it["gold_definition"],
            "predicted": top["synset_id"] if top else None,
            "predicted_definition": top["definition"] if top else None,
            "clues": top["clues"] if top else [],
            "correct": bool(top and top["synset_id"] in gold),
            "gold_rank_in_list": pos_rank, "candidates": len(ranked),
            "baseline_first_sense": first_sense(conn, it["lemma"], it["pos"]) in gold,
            "baseline_first_sense_no_pos": first_sense_no_pos(conn, it["target"]) in gold,
            "ms_fresh_call": round(fresh[i], 2) if fresh else None,
            "ms_shared_cache_call": round(times[-1], 2), "ms_repeat_call": round(warm, 2),
            "lemma": it["lemma"],
        })
    n = len(rows) or 1
    correct = sum(r["correct"] for r in rows)
    return {
        "items": len(rows),
        "accuracy": correct / n,
        "correct": correct,
        "accuracy_95ci": wilson(correct, len(rows)),
        "in_top3": sum(1 for r in rows if r["gold_rank_in_list"] and r["gold_rank_in_list"] <= 3) / n,
        "baseline_first_sense": sum(r["baseline_first_sense"] for r in rows) / n,
        "baseline_first_sense_no_pos": sum(r["baseline_first_sense_no_pos"] for r in rows) / n,
        "ms_fresh": _summary(fresh),
        "ms_shared_cache": _summary(times),
        "ms_repeat": _summary([r["ms_repeat_call"] for r in rows]),
        "rows": rows,
    }


def word_of(item_id: str) -> str:
    """The word an item tests: the id without its number ("bank-3" -> "bank")."""
    return item_id.rsplit("-", 1)[0]


def unseen_words(result: dict, dev_items: list) -> dict:
    """Accuracy on the items whose word has no item in dev_items."""
    seen = {word_of(it["id"]) for it in dev_items}
    rows = [r for r in result["rows"] if word_of(r["id"]) not in seen]
    k = sum(r["correct"] for r in rows)
    return {"items": len(rows), "correct": k, "accuracy": k / len(rows) if rows else 0.0,
            "accuracy_95ci": wilson(k, len(rows))}


def _pct(x):
    return f"{100 * x:5.1f}%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=["dev", "test"], help="evaluate one split only")
    ap.add_argument("--db", help="path to english.sqlite")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--all-failures", action="store_true", help="also list dev failures")
    ap.add_argument("--no-indirect", action="store_true",
                    help="turn the indirect clue channel off (INDIRECT = 0)")
    args = ap.parse_args(argv)
    conn = open_english(args.db)
    if args.no_indirect:
        context.INDIRECT = 0.0
    splits = [args.split] if args.split else ["dev", "test"]
    results = {s: evaluate(conn, load_items(split=s)) for s in splits}
    if "test" in results:
        results["test"]["words_not_in_dev"] = unseen_words(results["test"],
                                                           load_items(split="dev"))
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if args.no_indirect:
        print("(indirect clue channel off)")
    for s in splits:
        r = results[s]
        lo, hi = r["accuracy_95ci"]
        print(f"{s:5s} n={r['items']:3d}  picker accuracy {_pct(r['accuracy'])} "
              f"({r['correct']}/{r['items']}, 95% CI {_pct(lo).strip()}-{_pct(hi).strip()})  "
              f"gold in top 3 {_pct(r['in_top3'])}  "
              f"first-sense baseline {_pct(r['baseline_first_sense'])}  "
              f"first sense, no pos {_pct(r['baseline_first_sense_no_pos'])}")
        if "words_not_in_dev" in r:
            u = r["words_not_in_dev"]
            lo, hi = u["accuracy_95ci"]
            print(f"      words with no dev item: {_pct(u['accuracy'])} ({u['correct']}/{u['items']}, "
                  f"95% CI {_pct(lo).strip()}-{_pct(hi).strip()})")
    for s in splits:
        r = results[s]
        f, sh, rp = r["ms_fresh"], r["ms_shared_cache"], r["ms_repeat"]
        print(f"{s:5s} ms per call: fresh caches mean {f['mean']}, median {f['median']}, "
              f"p90 {f['p90']}, max {f['max']}; shared caches mean {sh['mean']}, "
              f"max {sh['max']}; repeat mean {rp['mean']}")
    show = [s for s in splits if s == "test" or args.all_failures or args.split]
    for s in show:
        fails = [x for x in results[s]["rows"] if not x["correct"]]
        print(f"\nFailed {s} items ({len(fails)}):")
        for x in fails:
            print(f"- {x['id']}: {x['sentence']}")
            print(f"    target {x['target']!r}; gold {x['gold'][0]}: {x['gold_definition']}")
            print(f"    picked {x['predicted']}: {x['predicted_definition']}  "
                  f"(clues {x['clues']}; gold ranked {x['gold_rank_in_list']} of {x['candidates']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
