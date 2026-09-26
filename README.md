# Kids' dictionary and thesaurus

An offline dictionary and thesaurus for children aged 6 to 14, with two levels,
Junior and Explorer. It is built for iPad first, since that is what schools
use, and for iPhone too. The languages will be English, French, German and
Italian; this repository is the English build, and it tests the whole data
pipeline.

The main feature: a child scans a page of a book and taps a word. The app reads
the sentence around the word and picks the meaning that fits. It shows that
meaning first, with synonyms for that meaning only and the "clues" in the
sentence that led to it. The other meanings are folded away. Everything runs on
the device from one SQLite file; nothing is looked up online.

Modern definitions come from Open English WordNet 2025. Extra synonyms come
from two public-domain thesauri, Soule (1871) and Roget (1911). Each old-book
synonym group is matched to one WordNet meaning, and groups that can't be
matched with confidence are set aside rather than guessed. See SOURCES.md for
the sources and NOTICE.md for the attribution the app must show.

## Layout

| path | what it is |
|---|---|
| `pipeline/fetch_sources.sh` | downloads the sources into `sources/` and checks their SHA-256 |
| `pipeline/pgtext.py` | reads a downloaded e-text and keeps only the book itself (`book_text`) |
| `pipeline/soule.py` | Soule 1871 → `data/soule.jsonl` |
| `pipeline/roget.py` | Roget 1911 → `data/roget.jsonl` |
| `pipeline/oewn.py` | Open English WordNet XML → `data/oewn.sqlite`, plus a lookup API and lemmatiser |
| `pipeline/build.py` | everything above → **`data/english.sqlite`**, the full English database, and `data/english_audit.sqlite`, for review only |
| `pipeline/appdb.py` | `data/english.sqlite` → **`data/app_en.sqlite`**, the compact file the app ships (about 96 MB), and `app_pick`, the reference for the app's meaning picker |
| `pipeline/context.py` | `pick_sense(conn, sentence, word, at=None)`: the context sense picker |
| `pipeline/entry.py` | `word_entry(conn, word, sentence=None, level=..., at=None)`: the word screen as a dict; writes the demo files |
| `pipeline/evaluate.py` | accuracy of the picker on `tests/data/wsd_eval.jsonl` |
| `data/demo_bank.json`, `data/demo_bright.json` | example screens (committed) |
| `tests/` | unit tests and the sense evaluation set |
| `KidsDictionary/` | the iPad and iPhone app (SwiftUI, iOS 17); `KidsDictionary/Core` is the dictionary logic |
| `KidsDictionary.xcodeproj` | the Xcode project (the folder is a synchronised group: every file in it is built) |
| `Package.swift`, `SwiftTests/` | `KidsDictionary/Core` as a Swift package, tested with `swift test` on a Mac |
| `codemagic.yaml` | Codemagic builds: `check` (tests and an unsigned build) and `testflight` |
| `tools/make_icon.py` | draws the placeholder app icon |

`data/*.sqlite`, `data/*.jsonl` and the build's temporary `data/*.tmp` files
are build outputs and are git-ignored.

## Get the sources

```sh
sh pipeline/fetch_sources.sh      # needs curl and sha256sum
```

This downloads six public-domain e-texts and the WordNet XML into `sources/` and
checks them against `pipeline/sources.sha256`. The English build uses three of
them: `pg38390.txt` (Soule), `pg22.txt` (Roget) and
`english-wordnet-2025.xml.gz`.

## Build

Python 3.11, standard library only. Run from the project root:

```sh
python3 -m pipeline.soule         # data/soule.jsonl
python3 -m pipeline.roget         # data/roget.jsonl
python3 -m pipeline.oewn          # data/oewn.sqlite
python3 -m pipeline.build         # data/english.sqlite and data/english_audit.sqlite (~35 s; rebuilds missing inputs itself)
python3 -m pipeline.entry         # data/demo_bank.json, data/demo_bright.json
```

Each step prints a report. `python3 -m pipeline.entry WORD --sentence "..."
--level explorer --at OFFSET` prints one word screen (`--at`, the character
offset of the tap, is optional).

### What `data/english.sqlite` holds

It has all the WordNet tables from `data/oewn.sqlite`, unchanged, plus these:

| table | rows | contents |
|---|---|---|
| `sense_synonyms(synset_id, word, word_key, source, score, ref)` | 243,228 | synonyms for one meaning: WordNet members (182,916, `oewn2025`), Soule (45,376, `soule1871`) and Roget (14,936, `roget1911`); `score` is the alignment score and `ref` points back to the book line or Roget head |
| `thesaurus_alignments` | 24,714 | each old-book group that was matched, with its score and the runner-up's score |
| `gloss_keys`, `lemma_idf` | 107,519 / 46,127 | lemmatised definitions and examples, and word weights, used by the picker |
| `kid_filter` | 1,963 | meanings (953) and words (1,010) never shown to children: WordNet's offensive, obscene and slur labels, modern slang, and this build's blocklist (source id `build`) |

The `meta` table holds the full Open English WordNet attribution
(`attribution`), the Princeton notice and licence text (`princeton_notice`,
`princeton_licence`), the licence (`licence`, `licence_url`) and the list of
changes this build makes to the WordNet data (`changes`).

The old-book synonyms reach 9,153 WordNet meanings, and 40,066 of the
(meaning, word) pairs are new, meaning WordNet does not list that word for that
meaning. The file is 121 MB (`oewn.sqlite` alone is 89 MB). Every row carries
a source id. The build also checks, byte for byte, that neither output file
names the e-book site the old texts were downloaded from.

`data/english_audit.sqlite` (13 MB) is for people checking the data and is
**not shipped to the app**. It holds `thesaurus_unaligned`, the 76,714 book
groups that were set aside, exactly as printed (so offensive words included),
with the reason (`below_threshold`, `tie`, `long_group`, `no_wordnet_entry` or
`unsupported_pos`), and `thesaurus_withheld`, the 22,272 words of matched
Roget groups that were not made synonyms, and why.

**How a group is matched.** Take a Soule numbered sense, or a Roget semicolon
group, under a headword with a given part of speech. Each WordNet meaning of
that headword with the same part of speech is scored by how well the group's
words match it. A word that is a member of that meaning's synset scores 1.0.
A member of a similar or also-see synset scores 0.8, of a hypernym 0.6, and of
a hyponym 0.4. A word that appears in the definition scores 0.5. The group is
matched only if the best meaning scores at least 1.0 and beats every other
meaning by 0.2, and beats a meaning that WordNet lists as commoner by a factor
of 1.5 as well (so Soule's "Time: period, age, era, epoch" is no longer read as
a prison term). A Roget group of ten or more words under a headword with only
one meaning needs 0.3 points per word.

**Which Roget words become synonyms.** Roget's groups are often lists of kinds
of a thing (horses, shops, containers) rather than synonyms. So a word of a
matched Roget group becomes a synonym only if WordNet links it closely to the
meaning: the same synset, a similar one, its direct hypernym, or, for verbs and
adjectives, a direct hyponym. A word WordNet does not know at all (mostly a
phrase such as "tongue of land") is also kept, unless the group looks like a
list. So "colt" no longer gets nag, pony and palfrey, and "shoe shop" no longer
gets "bookstore". Soule's senses are real synonym lists and keep all their
words.

In a by-eye check of 50 random rows that the books add (words WordNet does not
already have for that meaning; seed 20260926), Roget had 39 right, 8 partly
right and 3 wrong. The same check before the rule above found 27 right, 5
partly right and 18 wrong. Soule had 38 right, 11 partly right (near-synonyms
such as "agreeable" → "delectable") and 1 wrong. The docstring of
`pipeline/build.py` gives the full rules. Kept synonyms must be WordNet lemmas
of the right part of speech, or short English phrases.

## Test

```sh
python3 -m unittest discover -s tests -v
```

The suite has 178 tests (all pass) and takes about 50 s. The build, picker and
screen tests read `data/english.sqlite` and `data/english_audit.sqlite`. If
either is missing, `tests/dbfixture.py` builds both once in a temporary
directory (about a minute more), so a fresh checkout runs every test instead of
skipping some.

## Evaluate the sense picker

```sh
python3 -m pipeline.evaluate                 # dev and test, baselines, every failed test item
python3 -m pipeline.evaluate --split dev     # use only this while tuning
python3 -m pipeline.evaluate --no-indirect   # the same with the indirect clue channel off
```

`tests/data/wsd_eval.jsonl` has 116 hand-labelled sentences (see
`tests/data/README.md`): 36 in dev and 80 in test. **All tuning used dev
only.** The first version of the picker was frozen before test was looked at.
A review then read the test failures. This second version makes changes that
the review suggested: stronger part-of-speech cues, keeping the tapped word's
inflected forms out of its clues, and a weaker indirect clue channel. Its
numbers were again chosen on dev only. On a grid, many settings tied for the
best dev score, and the one that leans least on the indirect channel was
taken. Test was then run once, with the picker frozen. Even so, the test score
is no longer a fully blind estimate, because some of the changes were
prompted by test items.

## Current metrics

The picker's top meaning counts as correct if it is one of the item's gold
synsets. The picker gets only the sentence and the tapped word, so it must
also work out the part of speech. The baselines are given the correct lemma and
part of speech. The intervals are 95% Wilson intervals.

| split | items | picker | 95% interval | gold in picker's top 3 | first-sense baseline (rank 1, given pos) | first sense, no pos |
|---|---|---|---|---|---|---|
| dev | 36 | **77.8%** (28) | 61.9–88.3% | 94.4% | 47.2% (17) | 25.0% (9) |
| test | 80 | **62.5%** (50) | 51.5–72.3% | 85.0% | 41.2% (33) | 28.7% (23) |

36 of the 55 words have items in both splits. On the 38 test items whose word
has no dev item, the picker gets 63.2% (24; interval 47.3–76.6%). On the other
42 it gets 61.9% (26). So the words shared with dev do not seem to inflate the
test score, although the samples are small.

With the indirect clue channel off (`--no-indirect`), dev falls to 63.9% (23)
and test is 63.7% (51). The channel is worth five items on dev and nothing on
test, so it has not been shown to generalise. It is kept, much weaker than
before (`INDIRECT` 0.35, at most 2.0 points per clue), because it gives the
"boat → water" clues.

The first version also scored 50/80 on test, but 11 of its 30 failures picked
the wrong part of speech. Now 4 of the 30 do: "The sun rose over the hills"
(read as the flower), "the bus leaves", "Turn left" and "what does the word
mean". The other 26 get the part of speech right but pick a neighbouring
sense: the hand saw vs. the power saw, a king's seal vs. sealing wax, "his
watch" read as a ship's watch. `python3 -m pipeline.evaluate` lists every
failed test item with the meaning it chose and its clues.

**Speed** on the build machine, a server CPU and not an iPad: when the
picker's caches are emptied before each item, a call takes 13–14 ms on average
(median 13 ms, 90th percentile 20 ms, at most 31 ms). When the caches are
shared across items after `context.warm_up()`, the mean is 9–10 ms. A repeated
call takes about 1 ms. In all three the database file stays in the operating
system's cache. Nothing has been timed on iPad-class hardware yet, so these
figures are only a guide to the 50 ms budget.

## Using the data in code

```python
from pipeline.context import open_english, pick_sense, warm_up
from pipeline.entry import word_entry

conn = open_english()                   # data/english.sqlite, read-only
warm_up(conn)
pick_sense(conn, "I paid the money into the bank.", "bank")[0]["definition"]
# 'a financial institution that accepts deposits and channels the money into lending activities'
word_entry(conn, "ducks", sentence="The ducks were waiting by the bank.", level="junior")
s = "The bat flew out of the cave as Tom swung his cricket bat."
word_entry(conn, "bat", sentence=s, at=s.rindex("bat"))    # the second "bat" was tapped
```

`word_entry` returns the screen: the headword and part of speech, then the
meanings in order. With a sentence, the best-fitting meaning comes first,
open, with its `clues` and `clue_links` (for example "boat → water"). A clue
is shown only if it matched a word of that meaning directly, or matched
through its own meaning by a word that can be named. Very general words
("took", "old") are never shown as clues. The other meanings follow in WordNet
order, folded. Each meaning has a definition, one example, up to 6 synonyms
(Junior) or 10 (Explorer), each with its sources, and any antonyms. Junior
shows at most 5 meanings, and only synonyms of one or two words. `at`, the
character offset of the tap, tells the picker which occurrence of a repeated
word was tapped.

## Known limitations

- **Part of speech** comes from a few rules about neighbouring words ("the …",
  "the old stone …", "to …", "is …"). One known bug: after "were" or "is", an
  "-ing" word is preferred as an adjective over the verb ("the ducks were
  waiting" gives *waiting* adj). It is not fixed yet.
- **The whole sentence is one bag of clues.** The tap position picks the word,
  but the clues come from the whole sentence. In "The bat flew out of the cave
  as Tom swung his cricket bat", both bats are read as the cricket bat.
- **Old-book coverage** is uneven, and smaller for Roget than before: its
  groups now give only words WordNet links closely (14,936 rows instead of
  43,901). Some common meanings get no old-book synonyms because no group
  passed the threshold. The river bank is one: Roget's "bank, lea" is set
  aside. When a meaning has fewer than three synonyms, the screen shows
  "related" words instead (riverbank, waterside, slope). Soule's words are not
  checked one by one, and about one in five of the words Soule adds is only a
  near-synonym.
- **Kid safety** uses WordNet's own usage labels, Roget's tags, Soule's
  vulgar, low, cant and contempt labels, and a blocklist. The blocklist is
  matched against every word of a phrase ("fuck off", "dog shit"), and a few
  stems also inside words ("fuckup"), with an allowlist for innocent compounds
  ("cock-a-doodle-doo", "bastard toadflax"). This is not a full review. The
  1911 thesaurus and its MICRA additions contain dated and offensive language,
  so the content needs a human review before it ships to schools.
- **Size:** 121 MB is fine for an iPad but large for a phone.
- Soule's American spellings stay as printed ("labor"). The screen swaps only
  WordNet's own American spellings for British ones.

## The app

`KidsDictionary/` is the app, iPad first and iPhone too, working fully offline.

- **Read**: snap a book page with the document camera, or choose a photo. The
  words are read on the device (Vision), every word on the page can be tapped
  (finger or Apple Pencil), and the meaning that fits its sentence appears beside
  the page on iPad or in a sheet on iPhone, with the clues, a similar-words list
  for that meaning only, and the other meanings folded away.
- **Look up**: type, dictate, or write with Apple Pencil (Scribble works in the
  search field). A misspelt word gets "Did you mean" from how it sounds
  ("nolij" finds knowledge) and from close spellings.
- **Word page**: all meanings, each in its own colour, with an example, similar
  words and opposites for the chosen one; Junior shows fewer meanings and words.
- **My Words**: saved words with the meaning and sentence they came from
  (SwiftData, only on the device; on a shared school iPad, per child).
- **Settings**: Junior or Explorer, and the credits the WordNet licence requires.

The app opens `KidsDictionary/Resources/app_en.sqlite` read-only. That file is not
in git: build it with `python3 -m pipeline.appdb` and copy it there, as the
Codemagic build does:

```
sh pipeline/fetch_sources.sh
python3 -m pipeline.build
python3 -m pipeline.appdb --eval        # also prints the picker's accuracy
cp data/app_en.sqlite KidsDictionary/Resources/
```

### The on-device meaning picker

The full picker in `pipeline/context.py` follows WordNet relations at lookup
time, which is too much for the app. `pipeline/appdb.py` precomputes instead:

- for every meaning, its 60 strongest clue words with their weights (from the
  definition, examples, synonyms, related meanings and old-book synonyms, each
  multiplied by how rare the word is);
- for every word, what its own meanings talk about ("boat" → water, vessel …),
  so a sentence word can point to a meaning through its own meaning;
- the word lists for simple part-of-speech hints ("the bank" is a noun, "we can
  bank" a verb, "so bright" an adjective).

`app_pick` in `pipeline/appdb.py` is the specification, and
`KidsDictionary/Core/MeaningPicker.swift` does the same arithmetic. Both are
tested on one shared fixture (the SQL in
`SwiftTests/DictionaryCoreTests/DictionaryCoreTests.swift`, which
`tests/test_appdb.py` reads), with the same expected scores.

On the test sentences (`python3 -m pipeline.appdb --eval`, never tuned on):

| | right meaning first | right meaning in top 3 |
|---|---|---|
| app picker (test, 80 sentences) | 63.7% | 82.5% |
| app picker (dev, 36 sentences) | 55.6% | 86.1% |
| full Python picker (test) | 62.5% | 85.0% |

### Build and TestFlight

The `check` workflow runs the Python tests, builds the database, runs
`swift test` and makes an unsigned build. The code was written without a
compiler at hand, so run `check` first and expect a round of fixes. Then:

1. Register the App ID `com.chager5000.dictionary` in the Apple Developer site.
2. Create the app in App Store Connect with that bundle ID. The display name is
   a placeholder ("Dictionary") until the name is chosen; change
   `INFOPLIST_KEY_CFBundleDisplayName` in the project.
3. In Codemagic, add this repository and give it the `appstore` group with the
   same four secret variables as LocalLedger and ReceiptVault.
4. Run the `testflight` workflow.

App privacy in App Store Connect: Data Not Collected. For the Kids category the
app has no ads, analytics or links out.
