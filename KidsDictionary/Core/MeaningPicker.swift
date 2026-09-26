import Foundation

/// A meaning ranked for one sentence, with the sentence words that pointed to it.
struct PickedMeaning: Identifiable, Hashable {
    let meaning: Meaning
    let score: Double
    /// Words from the sentence, as written, that support this meaning, strongest first.
    let clues: [String]
    var id: String { meaning.id }
}

/// Picks the meaning of a tapped word from the words around it.
///
/// This is pipeline/appdb.py app_pick and pos_cues, step for step. Every meaning
/// has clue words with weights precomputed by the data build (from its
/// definition, examples, synonyms and related meanings). A sentence word counts
/// for a meaning directly when it is one of those clue words, or through its own
/// meanings ("boat" talks about water, and water is a clue word of the river
/// bank). Nearer words count a little more; the words just before the tapped
/// word hint at noun, verb or adjective; meanings WordNet lists first get a
/// small head start.
struct MeaningPicker {
    let store: WordStore
    private(set) var prior = 2.0
    private(set) var distanceDecay = 0.04
    private(set) var minClue = 0.8
    private(set) var clueShare = 0.12
    private(set) var indirect = 0.35
    private(set) var indirectMax = 2.0
    private(set) var signatureRef = 150.0
    private(set) var posBonus = 4.0
    private(set) var posOther = 0.5

    init(store: WordStore) {
        self.store = store
        func number(_ key: String, _ fallback: Double) -> Double {
            store.meta(key).flatMap(Double.init) ?? fallback
        }
        prior = number("prior", prior)
        distanceDecay = number("dist_decay", distanceDecay)
        minClue = number("min_clue", minClue)
        clueShare = number("clue_share", clueShare)
        indirect = number("indirect", indirect)
        indirectMax = number("indirect_max", indirectMax)
        signatureRef = number("sig_ref", signatureRef)
        posBonus = number("pos_bonus", posBonus)
        posOther = number("pos_other", posOther)
    }

    private struct Clue {
        let word: String
        let keys: [String]
        let weight: Double
    }

    /// Ranks the meanings of `target` in `sentence`, best first. `at` is the
    /// character offset of the tapped word, for a word that appears twice.
    func pick(sentence: String, target: String, at: Int? = nil) -> [PickedMeaning] {
        let tokens = TextKeys.tokens(sentence)
        let low = tokens.map { TextKeys.lookupKey($0.text) }
        let targetKey = TextKeys.lookupKey(target)
        var ti = tokens.indices.first { j in
            guard low[j] == targetKey else { return false }
            guard let at else { return true }
            return tokens[j].start <= at && at < tokens[j].start + tokens[j].text.count
        }
        if ti == nil {
            let targetLemmas = Set(store.keys(for: target))
            ti = tokens.indices.first { !targetLemmas.isDisjoint(with: store.keys(for: tokens[$0].text)) }
        }
        let tappedWord = ti.map { tokens[$0].text } ?? target
        let lemmaKeys = store.keys(for: tappedWord)

        var candidates: [Meaning] = []
        var seen = Set<String>()
        for key in lemmaKeys {
            for m in store.meanings(ofKey: key) where seen.insert(m.id).inserted {
                candidates.append(m)
            }
        }
        if candidates.isEmpty { return [] }

        // the tapped word and all its forms never count as clues
        var drop = Set(lemmaKeys)
        drop.insert(TextKeys.lookupKey(tappedWord))
        for key in lemmaKeys {
            for form in store.db.rows("SELECT form FROM tok WHERE key = ?", [key], { $0.text(0) }) {
                drop.insert(form)
            }
        }

        // one clue per dictionary word; the nearest occurrence wins
        var order: [String] = []
        var clues: [String: Clue] = [:]
        for (j, token) in tokens.enumerated() where j != ti {
            if store.isStop(token.text) { continue }
            let keys = store.keys(for: token.text).filter { !store.stopWords.contains($0) }
            guard let first = keys.first else { continue }
            if drop.contains(low[j]) || keys.contains(where: { drop.contains($0) }) { continue }
            let distance = ti.map { abs(j - $0) } ?? 5
            let weight = 1.0 / (1.0 + distanceDecay * Double(max(distance - 1, 0)))
            if let current = clues[first] {
                if weight > current.weight { clues[first] = Clue(word: token.text, keys: keys, weight: weight) }
            } else {
                clues[first] = Clue(word: token.text, keys: keys, weight: weight)
                order.append(first)
            }
        }
        let generic = store.list("generic")
        var linking: [String: [String]] = [:]
        for first in order {
            linking[first] = Array(clues[first]!.keys.prefix(2)).filter { !generic.contains($0) }
        }

        let ordered = ti.map { posCues(low, $0) } ?? []
        var cues: [String: Double] = [:]
        for (pos, v) in ordered { cues[pos] = v }
        // the strongest hint; on a tie the one found first, as in the Python version
        var top: (key: String, value: Double)?
        for (pos, v) in ordered where top == nil || v > top!.value { top = (pos, v) }

        var out: [PickedMeaning] = []
        for meaning in candidates {
            let (signature, size) = store.clueWeights(of: meaning.id)
            let sizeFactor = size > 0 ? min(1.0, (signatureRef / Double(size)).squareRoot()) : 1.0
            var total = 0.0
            var supporting: [(points: Double, word: String, general: Bool)] = []
            for first in order {
                guard let clue = clues[first] else { continue }
                let direct = clue.keys.map { signature[$0] ?? 0 }.max() ?? 0
                var through = 0.0
                for key in linking[first] ?? [] {
                    var sum = 0.0
                    for (word, w) in store.links(of: key) {
                        if let s = signature[word] { sum += s * w }
                    }
                    through = max(through, sum)
                }
                through = min(indirect * through * sizeFactor, indirectMax)
                let best = max(direct, through) * clue.weight
                if best > 0 {
                    total += best
                    supporting.append((best, clue.word, generic.contains(first)))
                }
            }
            if let top, meaning.pos != top.key, (cues[meaning.pos] ?? 0) < top.value - 0.5 {
                total *= 1.0 - (1.0 - posOther) * min(top.value, 1.0)
            }
            let score = total + prior / Double(meaning.rank) + posBonus * (cues[meaning.pos] ?? 0)
            let shown = supporting
                .sorted { $0.points > $1.points }
                .filter { $0.points >= minClue && $0.points >= clueShare * total && !$0.general }
                .map(\.word)
            var unique: [String] = []
            for w in shown where !unique.contains(w) { unique.append(w) }
            out.append(PickedMeaning(meaning: meaning, score: score, clues: unique))
        }
        return out.sorted {
            $0.score != $1.score ? $0.score > $1.score : $0.meaning.rank < $1.meaning.rank
        }
    }

    /// Part-of-speech hints from the words just before and after token i, in the
    /// order they were found: "the bank" (noun), "we plant" (verb), "very cross"
    /// (adjective).
    func posCues(_ low: [String], _ i: Int) -> [(String, Double)] {
        let determiners = store.list("determiners"), degree = store.list("degree")
        let prev: String? = i > 0 ? low[i - 1] : nil
        let next: String? = i + 1 < low.count ? low[i + 1] : nil
        let canBe = store.partsOfSpeech(of: low[i])
        let nextIsNoun = next.map { !store.stopWords.contains($0) && store.partsOfSpeech(of: $0).contains("n") } ?? false
        let possessive = prev.map { $0.hasSuffix("'s") || $0.hasSuffix("s'") } ?? false
        var cues: [(String, Double)] = []
        func cue(_ pos: String, _ v: Double) {
            if let k = cues.firstIndex(where: { $0.0 == pos }) { cues[k].1 += v } else { cues.append((pos, v)) }
        }

        func determinerBefore(reach: Int = 3) -> Bool {
            if i < 2 || store.stopWords.contains(low[i - 1]) || degree.contains(low[i - 1]) { return false }
            var j = i - 2
            while j >= 0 && j > i - 2 - reach {
                if determiners.contains(low[j]) { return true }
                if store.stopWords.contains(low[j]) { return false }
                j -= 1
            }
            return false
        }

        if let p = prev, determiners.contains(p) || possessive {
            cue(nextIsNoun && canBe.contains("a") ? "a" : "n", 1.0)
        } else if determinerBefore() {
            cue(nextIsNoun && canBe.contains("a") ? "a" : "n", 0.8)
        } else if let p = prev, store.list("prepositions").contains(p) {
            cue("n", 0.6)
        }
        if let p = prev, store.list("modals").contains(p) || store.list("subjects").contains(p) || p == "please" {
            cue("v", 1.0)
        }
        if let p = prev, store.list("copulas").contains(p) {
            if low[i].hasSuffix("ing") { cue("v", 0.6) }
            cue("a", 0.8)
        }
        if let p = prev, degree.contains(p) {
            cue("a", 1.0)
        }
        return cues.map { ($0.0, min($0.1, 1.5)) }
    }
}
