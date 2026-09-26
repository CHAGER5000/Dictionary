import SwiftUI

/// Opens the bundled dictionary once and shares it with every screen.
@MainActor
final class AppModel: ObservableObject {
    let store: WordStore?
    let picker: MeaningPicker?
    let loadError: String?
    let speech = Speech()

    init() {
        guard let path = Bundle.main.path(forResource: "app_en", ofType: "sqlite") else {
            store = nil
            picker = nil
            loadError = "The dictionary file is missing from this build."
            return
        }
        do {
            let s = try WordStore(path: path)
            store = s
            picker = MeaningPicker(store: s)
            loadError = nil
        } catch {
            store = nil
            picker = nil
            loadError = "\(error)"
        }
    }
}
