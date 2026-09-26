# Notices

The English data this project builds (`data/english.sqlite`, the review file
`data/english_audit.sqlite` and the demo files `data/demo_*.json`) contains
material from the sources below, and so does the committed evaluation set
`tests/data/wsd_eval.jsonl`, whose `gold_definition` fields are Open English
WordNet definitions copied word for word. The app must show the Open English
WordNet attribution and the Princeton WordNet notice (for example on an "About
this dictionary" screen). The `meta` table of `data/english.sqlite` holds the
attribution below (`attribution`), the Princeton notice (`princeton_notice`)
and the full Princeton licence text (`princeton_licence`), the licence
(`licence`, `licence_url`) and the list of changes (`changes`), so the app can
read them from the data. Every word screen carries the attribution in its
`sources`.

## Open English WordNet, 2025 edition (source id `oewn2025`)

Definitions, examples, parts of speech, sense order, relations (antonyms,
hypernyms and so on) and the WordNet synonyms come from:

> Open English Wordnet (2025 edition), by the Open English WordNet team,
> https://github.com/globalwordnet/english-wordnet, licensed under the
> Creative Commons Attribution 4.0 International License (CC BY 4.0),
> https://creativecommons.org/licenses/by/4.0/.
> Copyright (c) 2019-present, The Open English WordNet Team.
>
> This work is based on or incorporates elements of the Princeton University
> WordNet database.

**What the licence information says, and where it was checked.**

- The WN-LMF header of `sources/english-wordnet-2025.xml.gz` gives only
  `license="https://creativecommons.org/licenses/by/4.0"`, `version="2025"`,
  `label="Open English Wordnet"` and the project URL. It has no copyright line
  and does not mention Princeton. `data/oewn.sqlite` (table `meta`) records the
  same values.
- The Open English WordNet repository was checked on 2026-09-26 at the tag
  `2025-edition`. Its `LICENSE.md` says the resource "is derived from Princeton
  WordNet under the WordNet License and further developed under the Creative
  Commons Attribution 4.0 International License", and that you may share and
  adapt it "providing attribution is given to both Princeton WordNet and the
  Open English Wordnet team", and it carries the copyright line "Copyright (c)
  2019-present, The Open English WordNet Team.", which the attribution above
  keeps, as CC BY 4.0 section 3(a)(1)(A)(ii) asks. It points to
  `WNDB_License.txt` for the Princeton
  terms. That file asks that its notice and disclaimer appear on all copies,
  so the notice is reproduced in full below. The file names **WordNet 3.1**, not
  3.0. (Checksums of the files read: `LICENSE.md` sha256
  `672cc8b5663e8dc74c4b07a9dcf477193853575b119908fd3dc0aeeb60a9dbbb`,
  `WNDB_License.txt` sha256
  `df30ec18fbabcdaf031b79ea026d3e6b959010cffe6dd7be9ac137822175b904`. The file
  itself is stored with line numbers, which are left out here.)

**Princeton WordNet licence notice**, as given in the Open English WordNet
repository (`WNDB_License.txt`):

```
This software and database is being provided to you, the LICENSEE, by
the Open English Wordnet team under the Creative Commons Attribution 4.0
International License (CC-BY 4.0).

Open English Wordnet 2023 Copyright 2023 by the Open English Wordnet team.

Permission to use, copy, modify and distribute this software and
database and its documentation for any purpose and without fee or
royalty is hereby granted, provided that you agree to comply with
the following copyright notice and statements, including the disclaimer,
and that the same appear on ALL copies of the software, database and
documentation, including modifications that you make for internal
use or for distribution.

WordNet 3.1 Copyright 2011 by Princeton University.  All rights reserved.

THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PRINCETON
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF MERCHANT-
ABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE
OF THE LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT
INFRINGE ANY THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR
OTHER RIGHTS.

The name of Princeton University or Princeton may not be used in
advertising or publicity pertaining to distribution of the software
and/or database.  Title to copyright in this software, database and
any associated documentation shall at all times remain with
Princeton University and LICENSEE agrees to preserve same.
```

**Changes made to the WordNet data.** CC BY 4.0 asks adapters to say that they
changed the material. We changed it as follows (the same list is in the
`changes` row of `meta`):

- Synsets with more than one definition have them joined with "; " (4 synsets).
- One example sentence was removed because it contains a word this project
  keeps out of its data.
- Definitions and examples were lemmatised into search keys (`gloss_keys`),
  and word weights were computed from them (`lemma_idf`).
- Synonyms from two public-domain thesauri (below) were matched to WordNet
  meanings and added to `sense_synonyms` (45,376 Soule rows and 14,936 Roget
  rows; the matches are in `thesaurus_alignments`).
- 647 meanings WordNet marks as disparaging, obscene or ethnic slurs, and 306
  modern slang meanings from Colloquial WordNet, are flagged in `kid_filter`
  and never shown to children. They have no `sense_synonyms` rows, and their
  words are not offered as synonyms.
- 147 WordNet synonyms that are on this build's blocklist of explicit and
  offensive words ("fuckup", "jack off") were left out of `sense_synonyms`;
  the blocked words are listed in `kid_filter` with source id `build`.
- For the app screen, some American spellings are replaced by British ones
  from the same synset (for example "colourless" for "colorless").

Canonical citation (from the project README): John P. McCrae, Alexandre
Rademaker, Francis Bond, Ewa Rudnicka and Christiane Fellbaum (2019), *English
WordNet 2019 – An Open-Source WordNet for English*, Proceedings of the 10th
Global WordNet Conference (GWC 2019), Wrocław.

## Public-domain books (source ids `soule1871`, `roget1911`)

The old-book synonyms come from two works in the public domain: Richard
Soule's *A Dictionary of English Synonymes* (Boston, 1871) and *Roget's
Thesaurus of English Words and Phrases* (1911 edition). The Roget text used is
the electronic edition by MICRA, Inc. It adds about 1,000 words to the 1911
text. MICRA "makes no proprietary claims regarding this electronic version"
and says it "can also be treated as public domain" wherever the 1911 work is.
SOURCES.md gives the editions, authors and how each book is used.
