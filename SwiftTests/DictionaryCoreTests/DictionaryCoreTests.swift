import XCTest
@testable import DictionaryCore

/// A tiny dictionary with the same tables as data/app_en.sqlite.
func makeFixture() throws -> WordStore {
    let db = try SQLiteDB(memory: ())
    try db.execute("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE stop(key TEXT PRIMARY KEY);
        CREATE TABLE tok(form TEXT, key TEXT, ord INTEGER);
        CREATE TABLE entry(key TEXT, lemma TEXT, pos TEXT, synset TEXT, rank INTEGER);
        CREATE TABLE meaning(mid INTEGER PRIMARY KEY, id TEXT UNIQUE, pos TEXT, definition TEXT, example TEXT, sigsize INTEGER);
        CREATE TABLE syn(synset TEXT, ord INTEGER, word TEXT, source TEXT);
        CREATE TABLE ant(synset TEXT, word TEXT);
        CREATE TABLE kw(kid INTEGER PRIMARY KEY, key TEXT UNIQUE);
        CREATE TABLE sig(mid INTEGER, kid INTEGER, w INTEGER);
        CREATE TABLE exp(kid INTEGER, ekid INTEGER, w INTEGER);
        CREATE TABLE ipa(key TEXT PRIMARY KEY, ipa TEXT);
        CREATE TABLE sound(skey TEXT, key TEXT);
        INSERT INTO meta VALUES ('prior', '2.0'), ('dist_decay', '0.04'), ('min_clue', '0.8'), ('clue_share', '0.12'),
            ('indirect', '0.35'), ('indirect_max', '2.0'), ('sig_ref', '150'), ('pos_bonus', '4.0'), ('pos_other', '0.5'),
            ('words_determiners', 'a an my the'), ('words_subjects', 'i we they'), ('words_modals', 'can to will'),
            ('words_copulas', 'is was'), ('words_degree', 'so very'), ('words_prepositions', 'into on towards'),
            ('words_generic', 'make thing');
        INSERT INTO stop VALUES ('the'), ('where'), ('were'), ('towards'), ('i'), ('into'), ('my'), ('a'),
                                ('we'), ('can'), ('to'), ('is'), ('was'), ('so'), ('very'), ('it');
        INSERT INTO tok VALUES ('ducks', 'duck', 1), ('drifted', 'drift', 1), ('banks', 'bank', 1),
                               ('paid', 'pay', 1), ('saw', 'saw', 1), ('saw', 'see', 2);
        INSERT INTO entry VALUES
            ('bank', 'bank', 'n', 'money-bank', 1),
            ('bank', 'bank', 'n', 'river-bank', 2),
            ('duck', 'duck', 'n', 'duck-bird', 1),
            ('bright', 'bright', 'a', 'bright-light', 1),
            ('bright', 'bright', 'a', 'bright-clever', 2),
            ('knowledge', 'knowledge', 'n', 'knowledge-n', 1),
            ('bank', 'bank', 'v', 'bank-tilt', 1),
            ('pond', 'pond', 'n', 'pond-n', 1),
            ('torch', 'torch', 'n', 'torch-n', 1);
        INSERT INTO meaning VALUES
            (1, 'money-bank', 'n', 'a place that looks after money', 'I put my money in the bank.', 40),
            (2, 'river-bank', 'n', 'the land along the side of a river', NULL, 40),
            (3, 'duck-bird', 'n', 'a swimming bird', NULL, 10),
            (4, 'bright-light', 'a', 'giving out lots of light', 'a bright torch', 30),
            (5, 'bright-clever', 'a', 'clever and quick to learn', 'a bright pupil', 30),
            (6, 'knowledge-n', 'n', 'what you know and understand', NULL, 10),
            (7, 'bank-tilt', 'v', 'tip sideways while turning, as a plane does', NULL, 40),
            (8, 'pond-n', 'n', 'a small lake', NULL, 10),
            (9, 'torch-n', 'n', 'a light you carry', NULL, 10);
        INSERT INTO syn VALUES ('river-bank', 1, 'bank', 'oewn2025'), ('river-bank', 2, 'shore', 'oewn2025'),
                               ('river-bank', 3, 'riverside', 'soule1871'), ('river-bank', 4, 'Shore', 'roget1911'),
                               ('bright-clever', 1, 'clever', 'oewn2025'), ('bright-clever', 2, 'smart', 'oewn2025');
        INSERT INTO ant VALUES ('bright-light', 'dim'), ('bright-light', 'dull');
        INSERT INTO kw VALUES (1, 'money'), (2, 'pay'), (3, 'boat'), (4, 'duck'), (5, 'river'),
                              (6, 'light'), (7, 'sun'), (8, 'clever'), (9, 'pupil'), (10, 'water'),
                              (11, 'plane'), (12, 'pond'), (13, 'torch');
        INSERT INTO sig VALUES (1, 1, 650), (1, 2, 400),
                               (2, 3, 500), (2, 4, 450), (2, 5, 700), (2, 10, 300),
                               (4, 6, 300), (4, 7, 500), (5, 8, 600), (5, 9, 550),
                               (7, 11, 800);
        INSERT INTO exp VALUES (12, 10, 100), (12, 5, 60), (13, 6, 100);
        INSERT INTO ipa VALUES ('bank', 'bæŋk');
        INSERT INTO sound VALUES ('nlj', 'knowledge'), ('bnk', 'bank'), ('brt', 'bright'), ('dk', 'duck');
        """)
    return WordStore(db: db)
}

final class TextKeysTests: XCTestCase {
    func testLookupKey() {
        XCTAssertEqual(TextKeys.lookupKey("  Grandma’s   Garden "), "grandma's garden")
        XCTAssertEqual(TextKeys.lookupKey("ice_cream"), "ice cream")
    }

    func testTokens() {
        let t = TextKeys.tokens("The shallow-water ducks can't wait, Grandma’s boat!")
        XCTAssertEqual(t.map(\.text), ["The", "shallow", "water", "ducks", "can't", "wait", "Grandma’s", "boat"])
        XCTAssertEqual(t[3].start, 18)
    }

    /// The same cases as pipeline/appdb.py sound_key gives (generated from it).
    func testSoundKeyMatchesDataBuild() {
        let cases: [(String, String)] = [
            ("nolij", "nlj"), ("knowledge", "nlj"), ("fotograf", "ftgrf"), ("photograph", "ftgrf"),
            ("enormus", "anrms"), ("enormous", "anrms"), ("nife", "nf"), ("knife", "nf"),
            ("sircle", "srkl"), ("circle", "srkl"), ("wrong", "rng"), ("rong", "rng"),
            ("edge", "aj"), ("ej", "aj"), ("school", "skl"), ("skool", "skl"),
            ("lamb", "lm"), ("lam", "lm"), ("thumb", "Tm"), ("fum", "fm"),
            ("jiraf", "jrf"), ("giraffe", "jrf"), ("kwiet", "kt"), ("quiet", "kt"),
            ("box", "bks"), ("boks", "bks"), ("Science", "sns"), ("sients", "snts"),
            ("where", "wr"), ("wear", "wr"), ("", ""), ("123", ""), ("Élan", "ln"),
        ]
        for (word, key) in cases {
            XCTAssertEqual(TextKeys.soundKey(word), key, word)
        }
    }

    func testEditDistance() {
        XCTAssertEqual(TextKeys.editDistance("kitten", "sitting"), 3)
        XCTAssertEqual(TextKeys.editDistance("", "abc"), 3)
        XCTAssertEqual(TextKeys.editDistance("same", "same"), 0)
    }

    func testSentenceAroundOffset() {
        let page = "Mia pulled the oars in. The little boat drifted\nslowly towards the bank, where the ducks were waiting. A bell rang."
        let at = (page as NSString).range(of: "bank").location
        XCTAssertEqual(Sentences.sentence(in: page, around: at),
                       "The little boat drifted slowly towards the bank, where the ducks were waiting.")
        XCTAssertEqual(Sentences.sentence(in: "Run! The dog barked.", around: 8), "The dog barked.")
    }
}

final class WordStoreTests: XCTestCase {
    func testKeysResolveInflections() throws {
        let s = try makeFixture()
        XCTAssertEqual(s.keys(for: "Ducks"), ["duck"])
        XCTAssertEqual(s.keys(for: "saw"), ["saw", "see"])
        XCTAssertEqual(s.keys(for: "zebra"), ["zebra"])
    }

    func testMeaningsInOrder() throws {
        let s = try makeFixture()
        let m = s.meanings(of: "banks")
        XCTAssertEqual(m.map(\.id), ["money-bank", "river-bank"])
        XCTAssertEqual(m.first?.partOfSpeech, "noun")
        XCTAssertEqual(m.first?.example, "I put my money in the bank.")
    }

    func testSynonymsSkipTheWordAndRepeats() throws {
        let s = try makeFixture()
        XCTAssertEqual(s.synonyms(of: "river-bank", excluding: "bank", limit: 6), ["shore", "riverside"])
        XCTAssertEqual(s.synonyms(of: "river-bank", excluding: "bank", limit: 1), ["shore"])
        XCTAssertEqual(s.opposites(of: "bright-light"), ["dim", "dull"])
    }

    func testSoundsLikeSuggestion() throws {
        let s = try makeFixture()
        XCTAssertEqual(s.suggestions(for: "nolij").first, Suggestion(key: "knowledge", sameSound: true))
        XCTAssertEqual(s.completions(of: "br"), ["bright"])
        XCTAssertTrue(s.contains("bank"))
        XCTAssertFalse(s.contains("banana"))
    }
}

final class MeaningPickerTests: XCTestCase {
    func testRiverBankFromTheSentence() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(
            sentence: "The little boat drifted slowly towards the bank, where the ducks were waiting.",
            target: "bank")
        XCTAssertEqual(ranked.first?.meaning.id, "river-bank")
        XCTAssertEqual(ranked.first?.clues, ["boat", "ducks"])
    }

    func testMoneyBankFromTheSentence() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(sentence: "I paid the money into my bank.", target: "bank")
        XCTAssertEqual(ranked.first?.meaning.id, "money-bank")
        XCTAssertEqual(ranked.first?.clues, ["money", "paid"])
    }

    func testFirstMeaningWithoutClues() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(sentence: "Look at the bank.", target: "bank")
        XCTAssertEqual(ranked.first?.meaning.id, "money-bank")
        XCTAssertEqual(ranked.first?.clues, [])
    }

    func testVerbAfterModal() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(sentence: "We can bank the plane.", target: "bank")
        XCTAssertEqual(ranked.first?.meaning.id, "bank-tilt")
        XCTAssertEqual(ranked.first?.clues, ["plane"])
        XCTAssertEqual(ranked.first?.score ?? 0, 13.692, accuracy: 0.001)
    }

    /// "pond" is not a clue word of the river bank, but its own meaning talks
    /// about water and rivers, which are.
    func testClueThroughItsOwnMeaning() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(sentence: "The pond is by the bank.", target: "bank")
        XCTAssertEqual(ranked.first?.meaning.id, "river-bank")
        XCTAssertEqual(ranked.first?.clues, ["pond"])
        XCTAssertEqual(ranked.first?.score ?? 0, 6.786, accuracy: 0.001)
    }

    func testAdjectiveAfterDegreeWord() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(sentence: "The torch was so bright.", target: "bright")
        XCTAssertEqual(ranked.first?.meaning.id, "bright-light")
        XCTAssertEqual(ranked.first?.clues, ["torch"])
        XCTAssertEqual(ranked.first?.score ?? 0, 6.972, accuracy: 0.001)
    }

    func testScoresMatchPythonReference() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let river = picker.pick(sentence: "The little boat drifted slowly towards the bank, where the ducks were waiting.",
                                target: "bank")
        XCTAssertEqual(river.map(\.meaning.id), ["river-bank", "money-bank", "bank-tilt"])
        XCTAssertEqual(river[0].score, 13.477, accuracy: 0.001)
        XCTAssertEqual(river[1].score, 6.0, accuracy: 0.001)
    }

    func testTappedWordNeverItsOwnClue() throws {
        let picker = MeaningPicker(store: try makeFixture())
        let ranked = picker.pick(sentence: "The banks of the bank were steep.", target: "bank", at: 17)
        XCTAssertFalse(ranked.contains { $0.clues.contains("banks") })
    }

    func testUnknownWordGivesNothing() throws {
        let picker = MeaningPicker(store: try makeFixture())
        XCTAssertTrue(picker.pick(sentence: "A zebra ran.", target: "zebra").isEmpty)
    }
}
