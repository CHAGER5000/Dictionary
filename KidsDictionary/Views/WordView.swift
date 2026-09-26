import SwiftUI
import SwiftData

/// Navigation value for opening a word's page (from a similar word, My Words, search).
struct WordLink: Hashable {
    let key: String
}

/// The word page: all meanings, each in its own colour, and for the chosen
/// meaning its example, similar words and opposites.
struct WordView: View {
    let key: String
    /// A meaning to select first (the one saved, or the one that fitted a sentence).
    var preferredMeaning: String?

    @EnvironmentObject private var model: AppModel
    @AppStorage("level") private var levelRaw = ReadingLevel.junior.rawValue
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var meanings: [Meaning] = []
    @State private var selected: String?

    private var level: ReadingLevel { ReadingLevel(rawValue: levelRaw) ?? .junior }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                if meanings.isEmpty {
                    Text("This word isn’t in the dictionary yet.")
                        .font(.body)
                        .foregroundStyle(Color.ink2)
                } else if sizeClass == .regular {
                    HStack(alignment: .top, spacing: 20) {
                        meaningList.frame(maxWidth: .infinity)
                        details.frame(maxWidth: .infinity)
                    }
                } else {
                    meaningList
                    details
                }
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(Color.page)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { LevelPicker() } }
        .task(id: key) { load() }
    }

    private func load() {
        meanings = model.store?.meanings(ofKey: key) ?? []
        if let p = preferredMeaning, meanings.contains(where: { $0.id == p }) {
            selected = p
        } else {
            selected = meanings.first?.id
        }
    }

    private var shownMeanings: [Meaning] {
        let limit = level == .junior ? 1 + level.otherMeaningLimit : meanings.count
        var list = Array(meanings.prefix(limit))
        if let s = selected, !list.contains(where: { $0.id == s }), let m = meanings.first(where: { $0.id == s }) {
            list.append(m)
        }
        return list
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 14) {
                Text(meanings.first?.lemma ?? key)
                    .font(.rounded(48))
                    .foregroundStyle(Color.ink)
                SpeakButton(text: meanings.first?.lemma ?? key)
                if let m = meanings.first(where: { $0.id == selected }) ?? meanings.first {
                    SaveButton(key: key, meaning: m)
                }
            }
            if let first = meanings.first {
                let parts = Array(Set(meanings.map(\.partOfSpeech))).sorted()
                Text(([first.partOfSpeech] + parts.filter { $0 != first.partOfSpeech }).joined(separator: " · "))
                    .font(.body)
                    .foregroundStyle(Color.ink2)
            }
        }
    }

    private var meaningList: some View {
        VStack(alignment: .leading, spacing: 4) {
            Overline(text: meanings.count == 1 ? "1 meaning" : "\(meanings.count) meanings")
                .padding(.horizontal, 10)
                .padding(.top, 6)
            ForEach(Array(shownMeanings.enumerated()), id: \.element.id) { index, m in
                Button {
                    selected = m.id
                } label: {
                    HStack(alignment: .top, spacing: 14) {
                        Text("\(index + 1)")
                            .font(.rounded(16))
                            .foregroundStyle(.white)
                            .frame(width: 32, height: 32)
                            .background(Circle().fill(Color.meaning(index)))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(m.definition)
                                .font(.system(size: 19, weight: m.id == selected ? .bold : .regular))
                                .foregroundStyle(Color.ink)
                                .multilineTextAlignment(.leading)
                            Text(m.partOfSpeech)
                                .font(.subheadline)
                                .foregroundStyle(Color.ink2)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(10)
                    .frame(minHeight: 56)
                    .background(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(m.id == selected ? Color.meaning(index).opacity(0.10) : .clear))
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(m.id == selected ? .isSelected : [])
            }
            if level == .junior && meanings.count > shownMeanings.count {
                Text("Explorer shows \(meanings.count - shownMeanings.count) more meanings.")
                    .font(.footnote)
                    .foregroundStyle(Color.ink2)
                    .padding(10)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    @ViewBuilder private var details: some View {
        if let index = shownMeanings.firstIndex(where: { $0.id == selected }) {
            MeaningDetails(meaning: shownMeanings[index], word: key, color: .meaning(index), level: level)
        }
    }
}

/// Example, similar words and opposites of one meaning.
struct MeaningDetails: View {
    let meaning: Meaning
    let word: String
    let color: Color
    let level: ReadingLevel
    @EnvironmentObject private var model: AppModel

    var body: some View {
        let synonyms = model.store?.synonyms(of: meaning.id, excluding: word, limit: level.synonymLimit) ?? []
        let opposites = model.store?.opposites(of: meaning.id) ?? []
        VStack(alignment: .leading, spacing: 16) {
            if let example = meaning.example {
                VStack(alignment: .leading, spacing: 8) {
                    Overline(text: "In a sentence")
                    Text("“\(example)”")
                        .font(.system(size: 19, design: .serif))
                        .foregroundStyle(Color.ink)
                }
                .card()
            }
            VStack(alignment: .leading, spacing: 10) {
                Overline(text: "Similar words for this meaning", color: color)
                if synonyms.isEmpty {
                    Text("No similar words for this meaning.")
                        .foregroundStyle(Color.ink2)
                } else {
                    FlowRow {
                        ForEach(synonyms, id: \.self) { w in
                            NavigationLink(value: WordLink(key: TextKeys.lookupKey(w))) {
                                ChipLabel(word: w, color: color)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .card()
            if !opposites.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Overline(text: "Opposites")
                    FlowRow {
                        ForEach(opposites, id: \.self) { w in
                            NavigationLink(value: WordLink(key: TextKeys.lookupKey(w))) {
                                ChipLabel(word: w, color: color, dashed: true)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .card()
            }
        }
    }
}

struct ChipLabel: View {
    let word: String
    let color: Color
    var dashed = false
    var body: some View {
        Text(word)
            .font(.rounded(17, .heavy))
            .foregroundStyle(color)
            .padding(.horizontal, 16)
            .frame(minHeight: 44)
            .background(
                Capsule().strokeBorder(color, style: StrokeStyle(lineWidth: 2, dash: dashed ? [5, 4] : []))
                    .background(Capsule().fill(Color.card)))
    }
}

struct SpeakButton: View {
    let text: String
    @EnvironmentObject private var model: AppModel
    var body: some View {
        Button {
            model.speech.say(text)
        } label: {
            Image(systemName: "speaker.wave.2.fill")
                .font(.system(size: 20, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 48, height: 48)
                .background(Circle().fill(Color.tap))
        }
        .accessibilityLabel("Hear \(text) said out loud")
    }
}

/// Star that saves this word, with this meaning, to My Words.
struct SaveButton: View {
    let key: String
    let meaning: Meaning
    var sentence: String?
    @Environment(\.modelContext) private var context
    @Query private var saved: [SavedWord]

    init(key: String, meaning: Meaning, sentence: String? = nil) {
        self.key = key
        self.meaning = meaning
        self.sentence = sentence
        _saved = Query(filter: #Predicate<SavedWord> { word in word.key == key })
    }

    private var existing: SavedWord? { saved.first { $0.meaningID == meaning.id } }

    var body: some View {
        Button {
            if let existing {
                context.delete(existing)
            } else {
                context.insert(SavedWord(key: key, lemma: meaning.lemma, meaningID: meaning.id,
                                         definition: meaning.definition, sentence: sentence))
            }
        } label: {
            Image(systemName: existing == nil ? "star" : "star.fill")
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(existing == nil ? Color.ink : Color.highlighter)
                .frame(width: 48, height: 48)
                .background(Circle().fill(Color.paper2))
        }
        .accessibilityLabel(existing == nil ? "Save to My Words" : "Saved in My Words")
    }
}

/// Junior / Explorer switch, remembered on the device.
struct LevelPicker: View {
    @AppStorage("level") private var levelRaw = ReadingLevel.junior.rawValue
    var body: some View {
        Picker("Level", selection: $levelRaw) {
            ForEach(ReadingLevel.allCases) { level in
                Text(level.title).tag(level.rawValue)
            }
        }
        .pickerStyle(.segmented)
        .frame(width: 200)
    }
}
