import SwiftUI
import SwiftData

/// Words the child saved, newest first, each with the meaning they needed.
struct MyWordsView: View {
    @Query(sort: \SavedWord.savedAt, order: .reverse) private var words: [SavedWord]
    @Environment(\.modelContext) private var context

    var body: some View {
        NavigationStack {
            Group {
                if words.isEmpty {
                    ContentUnavailableView("No words yet", systemImage: "star",
                                           description: Text("Tap the star on a word to keep it here."))
                } else {
                    List {
                        ForEach(words) { saved in
                            NavigationLink(value: saved) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(saved.lemma).font(.rounded(20)).foregroundStyle(Color.ink)
                                    Text(saved.definition).font(.subheadline).foregroundStyle(Color.ink2)
                                    if let s = saved.sentence {
                                        Text("“\(s)”").font(.caption).foregroundStyle(Color.ink2).lineLimit(2)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                        }
                        .onDelete { offsets in
                            for i in offsets { context.delete(words[i]) }
                        }
                    }
                    .scrollContentBackground(.hidden)
                }
            }
            .background(Color.page)
            .navigationTitle("My Words")
            .navigationDestination(for: SavedWord.self) { WordView(key: $0.key, preferredMeaning: $0.meaningID) }
            .navigationDestination(for: WordLink.self) { WordView(key: $0.key) }
        }
    }
}
