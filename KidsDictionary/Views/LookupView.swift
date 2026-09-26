import SwiftUI

/// Search by typing, dictation or Apple Pencil handwriting (Scribble works in any
/// text field). Misspelt words get a "Did you mean" from how they sound.
struct LookupView: View {
    @EnvironmentObject private var model: AppModel
    @State private var query = ""
    @State private var chosen: WordLink?
    @State private var path: [WordLink] = []

    var body: some View {
        NavigationSplitView {
            List(selection: $chosen) {
                Section {
                    TextField("Type, say or write a word", text: $query)
                        .font(.system(size: 22))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.search)
                        .onSubmit { openBest() }
                } footer: {
                    Text("Spelling doesn’t need to be right.")
                }
                if let store = model.store, !TextKeys.lookupKey(query).isEmpty {
                    results(store)
                }
            }
            .navigationTitle("Look up")
            .scrollContentBackground(.hidden)
            .background(Color.page)
        } detail: {
            NavigationStack(path: $path) {
                Group {
                    if let chosen {
                        WordView(key: chosen.key)
                    } else {
                        ContentUnavailableView("Look up a word", systemImage: "text.magnifyingglass",
                                               description: Text("Type it the way it sounds."))
                            .background(Color.page)
                    }
                }
                .navigationDestination(for: WordLink.self) { WordView(key: $0.key) }
            }
        }
        .onChange(of: chosen) { path = [] }
    }

    @ViewBuilder private func results(_ store: WordStore) -> some View {
        let key = TextKeys.lookupKey(query)
        let found = ([key] + store.keys(for: key)).filter(store.contains).uniqued()
        let exact = !found.isEmpty
        let starts = store.completions(of: key).filter { $0 != key }
        let sounds = exact ? [] : store.suggestions(for: key)
        if exact {
            Section("Found") {
                ForEach(found, id: \.self) { row($0, store) }
            }
        }
        if !sounds.isEmpty {
            Section("Did you mean") {
                ForEach(sounds, id: \.key) { row($0.key, store) }
            }
        }
        if !starts.isEmpty {
            Section("Words starting with “\(key)”") {
                ForEach(starts, id: \.self) { row($0, store) }
            }
        }
        if !exact && sounds.isEmpty && starts.isEmpty {
            Text("No word found. Try writing it the way it sounds.")
                .foregroundStyle(Color.ink2)
        }
    }

    private func row(_ key: String, _ store: WordStore) -> some View {
        let first = store.meanings(ofKey: key).first
        return NavigationLink(value: WordLink(key: key)) {
            VStack(alignment: .leading, spacing: 2) {
                Text(first?.lemma ?? key).font(.rounded(19))
                if let d = first?.definition {
                    Text(d).font(.subheadline).foregroundStyle(Color.ink2).lineLimit(1)
                }
            }
            .padding(.vertical, 4)
        }
        .tag(WordLink(key: key))
    }

    private func openBest() {
        guard let store = model.store else { return }
        let key = TextKeys.lookupKey(query)
        if let k = ([key] + store.keys(for: key)).first(where: store.contains) {
            chosen = WordLink(key: k)
        } else if let s = store.suggestions(for: key).first {
            chosen = WordLink(key: s.key)
        }
    }
}

extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
