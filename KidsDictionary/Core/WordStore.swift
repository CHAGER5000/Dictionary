import Foundation

/// One meaning of a word, as the app shows it.
struct Meaning: Identifiable, Hashable {
    let id: String            // WordNet synset id, e.g. "oewn-09236472-n"
    let lemma: String         // the dictionary word as WordNet writes it
    let pos: String           // n v a r
    let rank: Int             // WordNet's order for this word, 1 = first
    let definition: String
    let example: String?

    var partOfSpeech: String {
        switch pos {
        case "n": return "noun"
        case "v": return "verb"
        case "a": return "adjective"
        case "r": return "adverb"
        default: return pos
        }
    }
}

/// A suggestion for what the child may have meant ("nolij" -> "knowledge").
struct Suggestion: Hashable {
    let key: String
    let sameSound: Bool
}

/// Read-only access to the bundled dictionary database (data/app_en.sqlite,
/// built by pipeline/appdb.py). All lookups are on the device.
final class WordStore {
    let db: SQLiteDB
    let stopWords: Set<String>
    /// Word lists for the part-of-speech hints (determiners, modals, ...), from meta.
    let wordLists: [String: Set<String>]
    private var keyCache: [String: [String]] = [:]
    private var posCache: [String: Set<String>] = [:]
    private var linkCache: [String: [String: Double]] = [:]
    private let cacheLock = NSLock()

    init(db: SQLiteDB) {
        self.db = db
        self.stopWords = Set(db.rows("SELECT key FROM stop") { $0.text(0) })
        var lists: [String: Set<String>] = [:]
        for (key, value) in db.rows("SELECT key, value FROM meta WHERE key LIKE 'words_%'", [], { ($0.text(0), $0.text(1)) }) {
            lists[String(key.dropFirst("words_".count))] = Set(value.split(separator: " ").map(String.init))
        }
        self.wordLists = lists
    }

    func list(_ name: String) -> Set<String> { wordLists[name] ?? [] }

    convenience init(path: String) throws {
        try self.init(db: SQLiteDB(path: path))
    }

    // MARK: words

    /// Dictionary words a written word can stand for, most likely first:
    /// "ducks" -> ["duck"], "saw" -> ["saw", "see"]. Unknown words stand for themselves.
    func keys(for word: String) -> [String] {
        let k = TextKeys.lookupKey(word)
        cacheLock.lock()
        if let hit = keyCache[k] { cacheLock.unlock(); return hit }
        cacheLock.unlock()
        var got = db.rows("SELECT key FROM tok WHERE form = ? ORDER BY ord", [k]) { $0.text(0) }
        if got.isEmpty { got = [k] }
        cacheLock.lock(); keyCache[k] = got; cacheLock.unlock()
        return got
    }

    func isStop(_ word: String) -> Bool {
        let k = TextKeys.lookupKey(word)
        return k.count < 2 || stopWords.contains(k)
    }

    /// Is this exact key a dictionary word?
    func contains(_ key: String) -> Bool {
        !db.rows("SELECT 1 FROM entry WHERE key = ? LIMIT 1", [key]) { $0.int(0) }.isEmpty
    }

    /// Meanings of a dictionary word in WordNet order.
    func meanings(ofKey key: String) -> [Meaning] {
        db.rows("""
            SELECT m.id, e.lemma, e.pos, e.rank, m.definition, m.example
            FROM entry e JOIN meaning m ON m.id = e.synset
            WHERE e.key = ? ORDER BY e.rank, e.pos
            """, [key]) {
            Meaning(id: $0.text(0), lemma: $0.text(1), pos: $0.text(2), rank: $0.int(3),
                    definition: $0.text(4), example: $0.optionalText(5))
        }
    }

    /// Meanings for a word as written (inflections resolved): "drifted" -> drift.
    func meanings(of word: String) -> [Meaning] {
        var seen = Set<String>()
        var out: [Meaning] = []
        for key in keys(for: word) {
            for m in meanings(ofKey: key) where seen.insert(m.id).inserted {
                out.append(m)
            }
        }
        return out
    }

    /// Words that mean the same in this meaning, best first, without the word itself.
    func synonyms(of meaningID: String, excluding word: String, limit: Int) -> [String] {
        let skip = TextKeys.lookupKey(word)
        var out: [String] = []
        for w in db.rows("SELECT word FROM syn WHERE synset = ? ORDER BY ord", [meaningID], { $0.text(0) }) {
            let k = TextKeys.lookupKey(w)
            if k != skip && !out.contains(where: { TextKeys.lookupKey($0) == k }) {
                out.append(w)
            }
            if out.count >= limit { break }
        }
        return out
    }

    func opposites(of meaningID: String) -> [String] {
        db.rows("SELECT word FROM ant WHERE synset = ? ORDER BY word", [meaningID]) { $0.text(0) }
    }

    func pronunciation(of key: String) -> String? {
        db.rows("SELECT ipa FROM ipa WHERE key = ?", [key]) { $0.text(0) }.first
    }

    /// Clue words of a meaning with their weights, and how many clue words the
    /// meaning had before the build trimmed the list (see pipeline/appdb.py).
    func clueWeights(of meaningID: String) -> (weights: [String: Double], size: Int) {
        let size = db.rows("SELECT sigsize FROM meaning WHERE id = ?", [meaningID]) { $0.int(0) }.first ?? 0
        var out: [String: Double] = [:]
        for (key, w) in db.rows("""
            SELECT kw.key, sig.w FROM meaning m JOIN sig ON sig.mid = m.mid
            JOIN kw ON kw.kid = sig.kid WHERE m.id = ?
            """, [meaningID], { ($0.text(0), Double($0.int(1)) / 100) }) {
            out[key] = w
        }
        return (out, size)
    }

    /// What a clue word's own meanings talk about: word -> weight 0...1
    /// ("boat" -> water, vessel, ...).
    func links(of key: String) -> [String: Double] {
        cacheLock.lock()
        if let hit = linkCache[key] { cacheLock.unlock(); return hit }
        cacheLock.unlock()
        var out: [String: Double] = [:]
        for (k, w) in db.rows("""
            SELECT e.key, x.w FROM kw c JOIN exp x ON x.kid = c.kid JOIN kw e ON e.kid = x.ekid
            WHERE c.key = ?
            """, [key], { ($0.text(0), Double($0.int(1)) / 100) }) {
            out[k] = w
        }
        cacheLock.lock(); linkCache[key] = out; cacheLock.unlock()
        return out
    }

    /// Parts of speech (n v a r) a written word can be.
    func partsOfSpeech(of word: String) -> Set<String> {
        let k = TextKeys.lookupKey(word)
        cacheLock.lock()
        if let hit = posCache[k] { cacheLock.unlock(); return hit }
        cacheLock.unlock()
        var out = Set<String>()
        for key in keys(for: k) {
            out.formUnion(db.rows("SELECT DISTINCT pos FROM entry WHERE key = ?", [key]) { $0.text(0) })
        }
        cacheLock.lock(); posCache[k] = out; cacheLock.unlock()
        return out
    }

    // MARK: search

    /// Words starting with what was typed, shortest first.
    func completions(of typed: String, limit: Int = 8) -> [String] {
        let k = TextKeys.lookupKey(typed)
        guard !k.isEmpty else { return [] }
        return db.rows("""
            SELECT DISTINCT key FROM entry WHERE key >= ? AND key < ?
            ORDER BY length(key), key LIMIT ?
            """, [k, k + "\u{10FFFF}", limit]) { $0.text(0) }
    }

    /// "Did you mean" for a word that is not in the dictionary: words that sound
    /// the same, then close spellings among them, best first.
    func suggestions(for typed: String, limit: Int = 5) -> [Suggestion] {
        let k = TextKeys.lookupKey(typed)
        let sk = TextKeys.soundKey(k)
        guard !sk.isEmpty else { return [] }
        let same = db.rows("SELECT key FROM sound WHERE skey = ?", [sk]) { $0.text(0) }
        let ranked = same.sorted {
            let a = TextKeys.editDistance(k, $0), b = TextKeys.editDistance(k, $1)
            return a != b ? a < b : $0.count < $1.count
        }
        var out = ranked.prefix(limit).map { Suggestion(key: $0, sameSound: true) }
        if out.count < limit {
            // near spellings: same first letter, similar length, small edit distance
            let near = db.rows("""
                SELECT key FROM sound WHERE key >= ? AND key < ? AND length(key) BETWEEN ? AND ?
                """, [String(k.prefix(1)), String(k.prefix(1)) + "\u{10FFFF}", max(1, k.count - 2), k.count + 2]) {
                $0.text(0)
            }
            let close = near.map { ($0, TextKeys.editDistance(k, $0)) }
                .filter { $0.1 <= max(1, k.count / 3) && !same.contains($0.0) }
                .sorted { $0.1 != $1.1 ? $0.1 < $1.1 : $0.0 < $1.0 }
            for (w, _) in close where out.count < limit {
                out.append(Suggestion(key: w, sameSound: false))
            }
        }
        return out
    }

    func meta(_ key: String) -> String? {
        db.rows("SELECT value FROM meta WHERE key = ?", [key]) { $0.text(0) }.first
    }
}
