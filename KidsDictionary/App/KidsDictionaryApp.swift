import SwiftUI
import SwiftData

@main
struct KidsDictionaryApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .tint(.tap)
        }
        .modelContainer(for: SavedWord.self)
    }
}
