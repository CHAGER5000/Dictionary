// swift-tools-version:5.9
// The dictionary logic (KidsDictionary/Core) is plain Foundation + SQLite code, so
// it is also built as a package and tested with `swift test` on a Mac. The app
// compiles the same files directly through its synchronised folder. No
// third-party dependencies.
import PackageDescription

let package = Package(
    name: "DictionaryCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    targets: [
        .target(name: "DictionaryCore", path: "KidsDictionary/Core"),
        .testTarget(name: "DictionaryCoreTests", dependencies: ["DictionaryCore"], path: "SwiftTests/DictionaryCoreTests"),
    ],
    swiftLanguageVersions: [.v5]
)
