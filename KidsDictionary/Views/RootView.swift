import SwiftUI

/// Read (snap a page, tap a word), Look up, My Words, Settings.
struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        if let problem = model.loadError {
            ContentUnavailableView("The dictionary could not open", systemImage: "exclamationmark.triangle",
                                   description: Text(problem))
        } else {
            TabView {
                ReadView()
                    .tabItem { Label("Read", systemImage: "camera") }
                LookupView()
                    .tabItem { Label("Look up", systemImage: "magnifyingglass") }
                MyWordsView()
                    .tabItem { Label("My Words", systemImage: "star") }
                SettingsView()
                    .tabItem { Label("Settings", systemImage: "gearshape") }
            }
        }
    }
}
