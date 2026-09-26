import Foundation

/// Junior (about 6-9) and Explorer (about 10-14).
enum ReadingLevel: String, CaseIterable, Identifiable {
    case junior, explorer
    var id: String { rawValue }
    var title: String { self == .junior ? "Junior" : "Explorer" }
    var ages: String { self == .junior ? "6–9" : "10–14" }
    /// How many similar words to show for a meaning.
    var synonymLimit: Int { self == .junior ? 6 : 10 }
    /// How many other meanings to list below the one that fits.
    var otherMeaningLimit: Int { self == .junior ? 3 : 12 }
}

enum Sentences {
    /// The sentence of `text` that contains the character offset `offset`.
    /// Sentences end at . ! ? or a blank line; a line break alone does not end one,
    /// because printed lines wrap in the middle of sentences.
    static func sentence(in text: String, around offset: Int) -> String {
        let chars = Array(text)
        let r = range(in: text, around: offset)
        guard !r.isEmpty else { return "" }
        let s = String(chars[r])
        return s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
            .trimmingCharacters(in: CharacterSet(charactersIn: " \"“”"))
    }

    /// Character offsets of the sentence of `text` that contains `offset`.
    static func range(in text: String, around offset: Int) -> Range<Int> {
        let chars = Array(text)
        guard !chars.isEmpty else { return 0..<0 }
        let at = min(max(offset, 0), chars.count - 1)
        func isEnd(_ i: Int) -> Bool {
            let c = chars[i]
            if c == "." || c == "!" || c == "?" {
                // a full stop followed by a lower-case word ("e.g. a boat") does not end a sentence
                var j = i + 1
                while j < chars.count, chars[j] == "\"" || chars[j] == "”" || chars[j] == "’" || chars[j] == ")" { j += 1 }
                while j < chars.count, chars[j] == " " || chars[j] == "\n" { j += 1 }
                return j >= chars.count || !chars[j].isLowercase
            }
            return c == "\n" && i + 1 < chars.count && chars[i + 1] == "\n"
        }
        var start = at
        while start > 0 && !isEnd(start - 1) { start -= 1 }
        var end = at
        while end < chars.count - 1 && !isEnd(end) { end += 1 }
        return start..<(end + 1)
    }
}
