import SwiftUI
import PhotosUI

/// What was tapped on the page and what the dictionary made of it.
struct TappedWord: Equatable {
    let word: PageWord
    let sentence: String
    let sentenceRange: Range<Int>
    let picks: [PickedMeaning]
}

/// Snap a book page once, then tap any word on it. The meaning that fits the
/// sentence appears beside the page on iPad and in a sheet on iPhone.
struct ReadView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var page: ReadPage?
    @State private var tapped: TappedWord?
    @State private var lookedUp: Set<Int> = []
    @State private var showScanner = false
    @State private var photo: PhotosPickerItem?
    @State private var reading = false
    @State private var problem: String?
    @State private var sheetShown = false

    var body: some View {
        NavigationStack {
            Group {
                if let page {
                    if sizeClass == .regular {
                        HStack(spacing: 0) {
                            PageCanvas(page: page, tapped: tapped, lookedUp: lookedUp, onTap: { tap($0) })
                            Divider()
                            panel.frame(width: 460)
                        }
                    } else {
                        PageCanvas(page: page, tapped: tapped, lookedUp: lookedUp, onTap: { tap($0) })
                            .sheet(isPresented: $sheetShown) {
                                NavigationStack {
                                    panel.navigationDestination(for: WordLink.self) { WordView(key: $0.key) }
                                }
                                    .presentationDetents([.medium, .large])
                            }
                    }
                } else {
                    start
                }
            }
            .background(Color.page)
            .navigationTitle(page == nil ? "Read" : "Your page")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if page != nil {
                    ToolbarItem(placement: .topBarLeading) { snapMenu(label: "Next page") }
                }
                ToolbarItem(placement: .topBarTrailing) { LevelPicker() }
            }
            .navigationDestination(for: WordLink.self) { WordView(key: $0.key) }
        }
        .fullScreenCover(isPresented: $showScanner) {
            DocumentScannerView(onFinish: { images in
                showScanner = false
                if let first = images.first { load(first) }
            }, onCancel: { showScanner = false })
            .ignoresSafeArea()
        }
        .onChange(of: photo) {
            guard let item = photo else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                    load(image)
                } else {
                    problem = "That photo could not be opened."
                }
                photo = nil
            }
        }
        .alert("Something went wrong", isPresented: Binding(get: { problem != nil }, set: { if !$0 { problem = nil } })) {
            Button("OK") { problem = nil }
        } message: {
            Text(problem ?? "")
        }
    }

    // MARK: start screen

    private var start: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "book")
                .font(.system(size: 64))
                .foregroundStyle(Color.tap)
            Text("The right meaning, right where you’re reading.")
                .font(.rounded(30))
                .multilineTextAlignment(.center)
                .foregroundStyle(Color.ink)
            Text("Snap a page of your book, then tap any word.")
                .font(.title3)
                .foregroundStyle(Color.ink2)
            if reading {
                ProgressView("Reading the page…")
            } else {
                snapMenu(label: "Snap a page")
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
            Label("The photo stays on this device. Nothing is sent.", systemImage: "lock")
                .font(.footnote)
                .foregroundStyle(Color.ink2)
            Spacer()
        }
        .padding(32)
        .frame(maxWidth: .infinity)
    }

    private func snapMenu(label: String) -> some View {
        Menu {
            if DocumentScannerView.isAvailable {
                Button { showScanner = true } label: { Label("Use the camera", systemImage: "camera") }
            }
            PhotosPicker(selection: $photo, matching: .images) {
                Label("Choose a photo", systemImage: "photo")
            }
        } label: {
            Label(label, systemImage: "camera")
                .font(.rounded(18))
        }
    }

    // MARK: meaning panel

    @ViewBuilder private var panel: some View {
        if let tapped {
            MeaningPanel(tapped: tapped)
        } else {
            VStack(spacing: 12) {
                Image(systemName: "hand.tap")
                    .font(.system(size: 44))
                    .foregroundStyle(Color.tap)
                Text("Tap a word on the page.")
                    .font(.rounded(22))
                    .foregroundStyle(Color.ink)
                Text("With Apple Pencil, just touch the word.")
                    .foregroundStyle(Color.ink2)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.page)
        }
    }

    // MARK: actions

    private func load(_ image: UIImage) {
        reading = true
        tapped = nil
        lookedUp = []
        Task {
            do {
                let read = try await PageReader.read(image)
                if read.words.isEmpty {
                    problem = "No words were found on that photo. Try again with the page flat and well lit."
                } else {
                    page = read
                }
            } catch {
                problem = "The page could not be read."
            }
            reading = false
        }
    }

    private func tap(_ word: PageWord) {
        guard let page, let picker = model.picker else { return }
        let range = Sentences.range(in: page.text, around: word.offset)
        let sentence = Sentences.sentence(in: page.text, around: word.offset)
        let at = word.offset - range.lowerBound
        // offset of the word inside the tidied sentence: find it by text near `at`
        let sentenceOffset = nearestOffset(of: word.text, in: sentence, near: at)
        let picks = picker.pick(sentence: sentence, target: word.text, at: sentenceOffset)
        tapped = TappedWord(word: word, sentence: sentence, sentenceRange: range, picks: picks)
        if !picks.isEmpty { lookedUp.insert(word.id) }
        sheetShown = true
    }

    private func nearestOffset(of word: String, in sentence: String, near at: Int) -> Int? {
        let tokens = TextKeys.tokens(sentence).filter { $0.text == word }
        return tokens.min(by: { abs($0.start - at) < abs($1.start - at) })?.start
    }
}

/// The snapped page with every word tappable. The tapped word is highlighted,
/// its sentence underlined, and words already looked up are lightly marked.
struct PageCanvas: View {
    let page: ReadPage
    let tapped: TappedWord?
    let lookedUp: Set<Int>
    let onTap: (PageWord) -> Void

    var body: some View {
        GeometryReader { geo in
            let size = fitted(page.image.size, in: geo.size)
            ScrollView([.vertical, .horizontal]) {
                ZStack(alignment: .topLeading) {
                    Image(uiImage: page.image)
                        .resizable()
                        .frame(width: size.width, height: size.height)
                    ForEach(page.words) { word in
                        let r = CGRect(x: word.rect.minX * size.width, y: word.rect.minY * size.height,
                                       width: word.rect.width * size.width, height: word.rect.height * size.height)
                        let inSentence = tapped?.sentenceRange.contains(word.offset) ?? false
                        let isTapped = tapped?.word.id == word.id
                        Button { onTap(word) } label: {
                            ZStack(alignment: .bottom) {
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(isTapped ? Color.highlighter.opacity(0.55)
                                          : lookedUp.contains(word.id) ? Color.highlighter.opacity(0.22) : Color.clear)
                                if inSentence && !isTapped {
                                    Rectangle().fill(Color.tap.opacity(0.6)).frame(height: 3)
                                }
                            }
                            .overlay(RoundedRectangle(cornerRadius: 4)
                                .stroke(isTapped ? Color.tap : .clear, lineWidth: 3))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .frame(width: max(r.width, 24), height: max(r.height + 6, 30))
                        .position(x: r.midX, y: r.midY)
                        .accessibilityLabel(word.text)
                    }
                }
                .frame(width: size.width, height: size.height)
            }
        }
        .background(Color(red: 0.914, green: 0.878, blue: 0.800))
    }

    private func fitted(_ image: CGSize, in box: CGSize) -> CGSize {
        guard image.width > 0, image.height > 0 else { return box }
        let scale = min(box.width / image.width, 1.6 * box.height / image.height)
        return CGSize(width: image.width * scale, height: image.height * scale)
    }
}

/// The meaning that fits, with the clues that pointed to it, similar words for
/// that meaning only, and the other meanings folded away.
struct MeaningPanel: View {
    let tapped: TappedWord
    @EnvironmentObject private var model: AppModel
    @AppStorage("level") private var levelRaw = ReadingLevel.junior.rawValue
    @State private var showOthers = false

    private var level: ReadingLevel { ReadingLevel(rawValue: levelRaw) ?? .junior }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let best = tapped.picks.first {
                    let key = TextKeys.lookupKey(best.meaning.lemma)
                    HStack(spacing: 12) {
                        Text(best.meaning.lemma).font(.rounded(44)).foregroundStyle(Color.ink)
                        SpeakButton(text: best.meaning.lemma)
                        SaveButton(key: key, meaning: best.meaning, sentence: tapped.sentence)
                        Spacer()
                        Text(best.meaning.partOfSpeech)
                            .font(.subheadline.bold())
                            .foregroundStyle(Color.ink2)
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(Capsule().fill(Color.paper2))
                    }
                    if !best.clues.isEmpty {
                        (Text("Clues we spotted in your sentence: ").foregroundStyle(Color.ink2)
                         + Text(best.clues.joined(separator: ", ")).bold().foregroundStyle(Color.meaning1))
                            .font(.subheadline)
                            .card(.paper2)
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        Overline(text: best.clues.isEmpty ? "Most likely meaning" : "Meaning that fits",
                                 color: .meaning1)
                        Text(best.meaning.definition)
                            .font(.system(size: 22))
                            .foregroundStyle(Color.ink)
                        if let example = best.meaning.example {
                            Text("“\(example)”").font(.subheadline).foregroundStyle(Color.ink2)
                        }
                    }
                    .card()
                    MeaningDetails(meaning: best.meaning, word: key, color: .meaning1, level: level)
                    others(Array(tapped.picks.dropFirst().prefix(level.otherMeaningLimit)))
                    NavigationLink(value: WordLink(key: key)) {
                        Label("Everything about “\(best.meaning.lemma)”", systemImage: "book")
                            .font(.rounded(17))
                    }
                } else {
                    Text("“\(tapped.word.text)”").font(.rounded(36)).foregroundStyle(Color.ink)
                    Text("This word isn’t in the dictionary yet. It may be a name, or the photo may have misread it.")
                        .foregroundStyle(Color.ink2)
                }
            }
            .padding(20)
        }
        .background(Color.page)
    }

    @ViewBuilder private func others(_ list: [PickedMeaning]) -> some View {
        if !list.isEmpty {
            DisclosureGroup(isExpanded: $showOthers) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(list.enumerated()), id: \.element.id) { index, p in
                        HStack(alignment: .top, spacing: 10) {
                            RoundedRectangle(cornerRadius: 3).fill(Color.meaning(index + 1)).frame(width: 6)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(p.meaning.definition).foregroundStyle(Color.ink)
                                Text(p.meaning.partOfSpeech).font(.caption).foregroundStyle(Color.ink2)
                            }
                        }
                    }
                }
                .padding(.top, 8)
            } label: {
                Overline(text: list.count == 1 ? "Another meaning" : "\(list.count) other meanings",
                         color: .meaning2)
            }
            .card()
        }
    }
}
