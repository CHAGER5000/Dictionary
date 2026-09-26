import Foundation
import SwiftData

/// A word a child saved to My Words, with the meaning they needed and, when it
/// came from a book, the sentence it was in. Stored only on this device (and, on a
/// shared school iPad, only in that child's own space).
@Model
final class SavedWord {
    var key: String
    var lemma: String
    var meaningID: String
    var definition: String
    var sentence: String?
    var savedAt: Date

    init(key: String, lemma: String, meaningID: String, definition: String, sentence: String? = nil) {
        self.key = key
        self.lemma = lemma
        self.meaningID = meaningID
        self.definition = definition
        self.sentence = sentence
        self.savedAt = Date()
    }
}
