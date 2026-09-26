import SwiftUI

/// Level, privacy promise and the credits the dictionary's licence requires.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("level") private var levelRaw = ReadingLevel.junior.rawValue

    var body: some View {
        NavigationStack {
            Form {
                Section("Level") {
                    Picker("Words for", selection: $levelRaw) {
                        ForEach(ReadingLevel.allCases) { level in
                            Text("\(level.title) (\(level.ages))").tag(level.rawValue)
                        }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }
                Section("Privacy") {
                    Label("No ads, no tracking, no accounts.", systemImage: "hand.raised")
                    Label("Works offline. Photos of pages never leave this device.", systemImage: "lock")
                }
                Section("Where the words come from") {
                    Text(model.store?.meta("attribution") ?? "Open English WordNet (CC BY 4.0).")
                    Text(model.store?.meta("princeton_notice") ?? "")
                    Text(model.store?.meta("attribution_soule1871") ?? "")
                    Text(model.store?.meta("attribution_roget1911") ?? "")
                    Text("Changes: definitions and synonyms were selected and combined for children, and words unsuitable for children were left out.")
                    NavigationLink("WordNet licence") {
                        ScrollView {
                            Text(model.store?.meta("princeton_licence") ?? "")
                                .font(.footnote.monospaced())
                                .padding()
                        }
                        .navigationTitle("WordNet licence")
                    }
                }
                .font(.footnote)
            }
            .navigationTitle("Settings")
        }
    }
}
