# Sense evaluation set (`wsd_eval.jsonl`)

This set tests the app's main feature. A child taps a word on a scanned page,
and the app uses the sentence around it to choose the meaning that fits. Each
line gives one sentence, marks one word in it, and names the right meaning (or
meanings) of that word in Open English WordNet.

## The sentences

We wrote all 116 sentences for this project. None was copied from a book, a
corpus or a dictionary. They are short and sound like a children's reader or a
classroom (ages 6-14), and most use British spelling. Each sentence is meant
to give enough context for an adult to choose one meaning without doubt. There are 55
ambiguous words (58 dictionary lemmas once inflected forms are counted, for
example "rose" -> *rise*, "saw" -> *see*, "leaves" -> *leaf* / *leave*). Each
word has 2-3 sentences, and each sentence uses a different meaning.

## Fields

One JSON object per line, UTF-8:

| field | meaning |
|---|---|
| `id` | stable id, `<word>-<n>` (e.g. `bank-3`) |
| `sentence` | the sentence as a child would see it on the page |
| `target` | the tapped word exactly as it appears in the sentence (it appears exactly once as a whole word) |
| `lemma` | dictionary form of the target (`barked` -> `bark`, `rose` -> `rise`) |
| `pos` | part of speech of the target in this sentence: `n`, `v`, `a` (adjective, which covers WordNet's `a` and `s` synsets), `r` (adverb) |
| `gold` | list of correct synset ids; a prediction counts as correct if it is **any** of them |
| `gold_definition` | the WordNet definition of the first gold synset, copied word for word |
| `split` | `dev` or `test` |
| `source` | where the sense ids and `gold_definition` come from: always `oewn2025` |

## Sense ids

The `gold` ids and `gold_definition` texts come from **Open English WordNet,
2025 edition** (source id `oewn2025`, licence CC BY 4.0, file
`sources/english-wordnet-2025.xml.gz`). Ids look like `oewn-09236472-n`. The id
changes between WordNet editions. If the pipeline moves to a newer edition,
map the ids across through the synsets' ILI (Interlingual Index) values, then
check the set again as described below.

## How the gold labels were made

1. **Author labels.** The author of each sentence chose the WordNet synset or
   synsets that fit.
2. **Blind re-label.** A second labeller labelled every item independently,
   choosing from the WordNet senses without seeing the author's answer.
3. **Adjudication.** Items where both labellers agreed were kept unchanged. Any
   item where they disagreed would be decided by reading the WordNet definitions,
   with four options: keep the author's label, switch to the blind label,
   accept both as gold, or drop the item. An item is dropped if the sentence
   has more than one reasonable reading or WordNet cannot tell the senses
   apart. Dropping is preferred to guessing.

In this release all 116 items reached adjudication as agreed. The list of
disputed items was empty. So no label was switched or merged, and no item was
dropped.

Three items have two gold synsets because WordNet lists two senses that both
fit the sentence and cannot be told apart: `pupil-1` (enrolled learner / young
schoolchild), `stick-1` (length of wood / small thin branch) and `left-2`
(leave behind unintentionally / go and leave behind).

## Checks run on the final file

* Every gold id exists in OEWN 2025. It is a sense of `lemma` with the given
  part of speech, and its synset has the same part of speech.
* `gold_definition` is exactly the WordNet definition of the first gold synset.
* `target` appears exactly once, as a whole word, in `sentence`.
* No two items share an id.
* **Spot check.** For 10 agreed items drawn at random (seed 20260926), the
  adjudicator read every WordNet sense of the lemma for that part of speech and confirmed
  the gold choice: `bank-3`, `bark-3`, `cross-1`, `match-2`, `note-2`,
  `ring-2`, `sign-2`, `sink-1`, `well-1`, `wind-1`. All 10 were right. The three
  items with two gold synsets and two other hard items (`fire-1`, `plant-1`)
  were also read, and they were right too.

On average a target lemma has 8.7 WordNet senses for its part of speech and
16.7 senses across all parts of speech. For three items (`kind-2`, `row-2`,
`left-1`), the lemma has only one sense in its part of speech. These items test
whether the app picks the right part of speech, not which meaning within it.

## Splits

| split | items | share | n | v | a | r |
|---|---|---|---|---|---|---|
| dev | 36 | 31% | 20 | 12 | 4 | 0 |
| test | 80 | 69% | 52 | 21 | 6 | 1 |
| total | 116 | | 72 | 33 | 10 | 1 |

Use `dev` to tune the sense chooser and `test` only to report results. The ids
are stable. Never renumber or reuse an id. If an item is removed later, its id
is retired, and new items take the next free number for their word.

The split is by item, not by word: 36 of the 55 words have items in both dev
and test, and 38 test items belong to the 19 words that have no dev item. A
picker tuned on dev has therefore seen the words (not the sentences) of most
test items, so `pipeline.evaluate` also reports test accuracy on the 38
items whose word has no dev item, and every accuracy with a 95% interval
(with 80 items the interval is about ±10 points). For the next release, split
by word, so that no word has items in both splits, and add items: 36 dev
items are too few to tune more than a handful of numbers.
