# Sources

Everything the English build uses lives in `sources/` and is fetched by
`pipeline/fetch_sources.sh`, which also checks the files against
`pipeline/sources.sha256`. Gutenberg e-texts are always read through
`pipeline.pgtext.book_text()`, which keeps only the book's own text between the
START and END markers. No data file this project produces mentions Project
Gutenberg; the build checks every output file byte for byte. The name appears
only in this file, the download script, and the code and tests that read the
e-texts or check for the word (`pipeline/pgtext.py` and others).

Every row the pipeline writes carries a source id: one of the three below, or
`build` for the rows of `kid_filter` that come from this project's own
blocklist of explicit and offensive words (see `pipeline/build.py`) rather
than from a source.

## Used in the English build

### `oewn2025`: Open English WordNet, 2025 edition

- **File:** `sources/english-wordnet-2025.xml.gz` (WN-LMF 1.3 XML, lexicon id
  `oewn`, version `2025`).
- **Download:** https://github.com/globalwordnet/english-wordnet/releases/download/2025-edition/english-wordnet-2025.xml.gz
- **By:** the Open English WordNet team (John P. McCrae and others), building on
  Princeton WordNet (George A. Miller, Christiane Fellbaum and others).
- **Licence:** CC BY 4.0, with the Princeton WordNet notice. See NOTICE.md for
  the exact wording and how it was checked.
- **Used for:** every definition, example, part of speech, sense order,
  relation and WordNet synonym. `pipeline/oewn.py` loads it into
  `data/oewn.sqlite`. `pipeline/build.py` copies those tables into
  `data/english.sqlite` and adds the other tables. The sense ids in
  `tests/data/wsd_eval.jsonl` are OEWN 2025 synset ids.

### `soule1871`: Richard Soule, *A Dictionary of English Synonymes* (1871)

- **Book:** *A Dictionary of English Synonymes and Synonymous or Parallel
  Expressions, Designed as a Practical Guide to Aptness and Variety of
  Phraseology*. Boston: Little, Brown, and Company, 1871.
- **Author:** Richard Soule (1812–1877).
- **File:** `sources/pg38390.txt`, Gutenberg e-book #38390:
  https://www.gutenberg.org/cache/epub/38390/pg38390.txt. The e-text was
  produced by Betsie Bush, Richard Prairie, Stephen Hope and the Online
  Distributed Proofreading Team (https://www.pgdp.net), from scans in the
  University of Michigan's Making of America collection.
- **Status:** public domain (published 1871; the author died in 1877).
- **Used for:** synonyms. `pipeline/soule.py` parses the numbered senses into
  `data/soule.jsonl` (23,690 records, 35,132 senses). Soule's shorthand is
  undone where it is clear ("not rash, heedless, or headlong" gives "not
  heedless"); 18 runs whose shared words cannot be told apart ("Gentle, mild,
  or soft breeze") are kept in each sense's `review` list instead.
  `pipeline/build.py` matches each sense to one WordNet meaning of the same
  headword and part of speech. Of 34,926 senses with a supported part of
  speech, 11,702 were matched, giving 45,376 rows in `sense_synonyms`; 15,730
  scored too low, 1,720 were ties and 4,742 had no WordNet entry, and these
  are kept in `thesaurus_unaligned` in the review file
  `data/english_audit.sqlite`. 100 senses labelled low, vulgar, cant, in
  contempt or in derision, and 932 senses with no usable word, were dropped
  and are not kept. The 206 senses with a part of speech WordNet lacks
  (prepositions, conjunctions ...) come on top of the 34,926 and are kept in
  `thesaurus_unaligned`. Row `ref` values are `"<line>#<sense>"`, and the line
  number counts lines of the `book_text()` output.

### `roget1911`: *Roget's Thesaurus of English Words and Phrases* (1911 edition)

- **Book:** *Roget's Thesaurus of English Words and Phrases*, 1911 edition.
  The project brief identifies this e-text as the 1911 Crowell edition. The
  e-text itself says only that it is "derived from the version of Roget's
  Thesaurus published in 1911" and names Peter Mark Roget as author. It does
  not name a publisher.
- **Authors:** Peter Mark Roget (1779–1869), who compiled the thesaurus; his son
  John Lewis Roget (1846–1908) and grandson Samuel Romilly Roget (1875–1953),
  who revised and enlarged later editions.
- **File:** `sources/pg22.txt`, Gutenberg e-book #22:
  https://www.gutenberg.org/cache/epub/22/pg22.txt. This is MICRA, Inc.'s
  electronic version. It has been "supplemented with over 1,000 words not
  present in the original 1911 edition", and MICRA "makes no proprietary
  claims regarding this electronic version". If the 1911 work is in the public
  domain, the electronic version "can also be treated as public domain".
- **Status:** public domain. The 1911 edition was published over a century
  ago, and S. R. Roget died in 1953, more than 70 years ago.
- **Used for:** synonyms. `pipeline/roget.py` parses the 1,044 heads into
  `data/roget.jsonl`. It keeps the e-text's three marks apart, as its front
  matter defines them: `|` is the 1911 book's own dagger (obsolete in 1911,
  list `obsolete`), `|!` is the editor's "presently obsolete" (`obsolete_now`)
  and `[obs3]` only says the editor's spelling checker did not know the word
  (`unrecognised`). Obsolete and foreign words are left out; `[obs3]` words are
  used when WordNet has them. Words tagged slang, vulgar or derogatory are
  dropped, because the MICRA additions include some profanity and slurs. For
  each word of each semicolon group, `pipeline/build.py` matches the group to
  one WordNet meaning of that word. 13,012 of 67,328 word-and-group pairs were
  matched; of the rest, 30,462 scored too low, 1,761 were ties, 961 were long
  lists under a word with only one meaning, 20,726 had no WordNet entry and 406
  had an unsupported part of speech, and all are kept in `thesaurus_unaligned`
  in `data/english_audit.sqlite`. A matched group gives the meaning only the
  words WordNet links closely to it (see README.md), 14,936 rows in
  `sense_synonyms`; the 22,272 others are kept in `thesaurus_withheld` in the
  same review file. Row `ref` values are `"<head>:<group index>"`.

## Downloaded but not used in this build

These files are fetched and checksummed but are out of scope for the English
proof build. Nothing is taken from them yet.

| file | work | author / editor | notes |
|---|---|---|---|
| `pg28900.txt` | *English Synonyms and Antonyms* (Funk & Wagnalls, copyright 1896; e-text of the 19th edition) | James Champlin Fernald (1838–1918) | https://www.gutenberg.org/cache/epub/28900/pg28900.txt |
| `pg51155.txt` | *A Complete Dictionary of Synonyms and Antonyms* (1898) | Samuel Fallows (1835–1922) | e-text re-OCRed by Steve Wood (2016); https://www.gutenberg.org/cache/epub/51155/pg51155.txt |
| `pg73237.txt` | *Synonyms and Antonyms* (New York: George Sully and Company, copyright 1913) | Edith B. Ordway (death year not checked) | https://www.gutenberg.org/cache/epub/73237/pg73237.txt |
| `pg37683.txt` | *Chambers's Twentieth Century Dictionary*, part 1 of 4 (A–D) (W. & R. Chambers, 1908) | ed. Thomas Davidson (death year not checked) | https://www.gutenberg.org/cache/epub/37683/pg37683.txt |

Check the public-domain status of the Ordway (1913) and Chambers (1908) texts
for each country where the app will be sold before using them.

## Checksums

`pipeline/sources.sha256` lists the SHA-256 of every file above.
`fetch_sources.sh` runs `sha256sum -c` against it.
