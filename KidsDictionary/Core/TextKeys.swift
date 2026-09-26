import Foundation

/// The same text normalisation the data build uses (pipeline/oewn.py lookup_key
/// and pipeline/appdb.py sound_key), so words typed or read in the app match the
/// keys stored in the database.
enum TextKeys {
    /// Case- and apostrophe-insensitive lookup key: "Grandma’s" -> "grandma's".
    static func lookupKey(_ text: String) -> String {
        var s = ""
        s.reserveCapacity(text.count)
        for ch in text {
            switch ch {
            case "’", "‘", "ʼ": s.append("'")
            case "_": s.append(" ")
            default: s.append(ch)
            }
        }
        return s.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    struct Token: Equatable {
        let text: String
        /// Character offset of the token in the original string (in Characters).
        let start: Int
        let range: Range<String.Index>
    }

    private static let tokenPattern = try! NSRegularExpression(
        pattern: "[\\p{L}\\p{M}]+(?:['’][\\p{L}\\p{M}]+)*")

    /// Word tokens in reading order. Hyphenated words split into their parts;
    /// apostrophes inside a word are kept ("can't").
    static func tokens(_ text: String) -> [Token] {
        let ns = text as NSString
        return tokenPattern.matches(in: text, range: NSRange(location: 0, length: ns.length)).compactMap { m in
            guard let r = Range(m.range, in: text) else { return nil }
            return Token(text: String(text[r]), start: text.distance(from: text.startIndex, to: r.lowerBound), range: r)
        }
    }

    // MARK: sounds-like key

    private static let soundRules: [(NSRegularExpression, String)] = ([
        ("^kn", "n"), ("^gn", "n"), ("^wr", "r"), ("^ps", "s"), ("^wh", "w"),
        ("mb$", "m"), ("dge", "j"), ("tch", "ch"), ("ck", "k"), ("ph", "f"),
        ("gh", ""), ("qu", "kw"), ("x", "ks"), ("sch", "sk"),
        ("c(?=[eiy])", "s"), ("g(?=[eiy])", "j"), ("c", "k"), ("q", "k"),
        ("sh", "S"), ("ch", "C"), ("th", "T"), ("z", "s"), ("v", "f"),
    ] as [(String, String)]).map { (try! NSRegularExpression(pattern: $0.0), $0.1) }

    /// A rough "how it sounds" key so a child can type a word as they hear it:
    /// "nolij" and "knowledge" both give "nlj". Must match pipeline/appdb.py sound_key.
    static func soundKey(_ word: String) -> String {
        var w = String(word.lowercased().filter { ("a"..."z").contains($0) })
        if w.isEmpty { return "" }
        for (rule, replacement) in soundRules {
            w = rule.stringByReplacingMatches(
                in: w, range: NSRange(location: 0, length: (w as NSString).length), withTemplate: replacement)
        }
        guard let first = w.first else { return "" }
        let rest = w.dropFirst().filter { !"aeiouyhw".contains($0) }
        var out = "aeiouy".contains(first) ? "a" : String(first)
        for ch in rest where ch != out.last {
            out.append(ch)
        }
        return out
    }

    /// Edit distance between two short words (for ranking "did you mean" suggestions).
    static func editDistance(_ a: String, _ b: String) -> Int {
        let x = Array(a), y = Array(b)
        if x.isEmpty { return y.count }
        if y.isEmpty { return x.count }
        var previous = Array(0...y.count)
        var current = [Int](repeating: 0, count: y.count + 1)
        for i in 1...x.count {
            current[0] = i
            for j in 1...y.count {
                let cost = x[i - 1] == y[j - 1] ? 0 : 1
                current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            }
            swap(&previous, &current)
        }
        return previous[y.count]
    }
}
