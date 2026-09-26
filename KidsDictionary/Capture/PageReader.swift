import UIKit
import Vision

/// A word on a snapped page: where it is (so it can be tapped) and where it is
/// in the page's text (so the sentence around it can be found).
struct PageWord: Identifiable, Hashable {
    let id: Int
    let text: String
    /// Position on the image, 0...1, top-left origin.
    let rect: CGRect
    /// Character offset of the word in `ReadPage.text`.
    let offset: Int
}

struct ReadPage {
    let image: UIImage
    let text: String
    let words: [PageWord]
}

/// Reads the words on a photo of a book page with Apple's on-device text
/// recognition (Vision). Nothing leaves the device.
enum PageReader {
    static func read(_ image: UIImage) async throws -> ReadPage {
        try await Task.detached(priority: .userInitiated) {
            guard let cg = upright(image) else { return ReadPage(image: image, text: "", words: []) }
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = true      // book text: prose, so correction helps
            request.recognitionLanguages = ["en-GB", "en-US"]
            try VNImageRequestHandler(cgImage: cg, orientation: .up).perform([request])

            // reading order: top to bottom, then left to right
            let lines = (request.results ?? []).sorted {
                abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.01
                    ? $0.boundingBox.midY > $1.boundingBox.midY
                    : $0.boundingBox.minX < $1.boundingBox.minX
            }
            var text = ""
            var words: [PageWord] = []
            var previousBottom: CGFloat?
            for observation in lines {
                guard let candidate = observation.topCandidates(1).first else { continue }
                let line = candidate.string
                let box = observation.boundingBox
                if !text.isEmpty {
                    // a gap much taller than a line is a new paragraph
                    let gap = (previousBottom ?? box.maxY) - box.maxY
                    text += gap > box.height * 2.2 ? "\n\n" : "\n"
                }
                let lineStart = text.count
                text += line
                previousBottom = box.minY
                for token in TextKeys.tokens(line) {
                    let r: CGRect
                    if let b = try? candidate.boundingBox(for: token.range)?.boundingBox, b.width > 0 {
                        r = b
                    } else {
                        let length = CGFloat(max(line.count, 1))
                        r = CGRect(x: box.minX + box.width * CGFloat(token.start) / length, y: box.minY,
                                   width: box.width * CGFloat(token.text.count) / length, height: box.height)
                    }
                    words.append(PageWord(
                        id: words.count, text: token.text,
                        rect: CGRect(x: r.minX, y: 1 - r.maxY, width: r.width, height: r.height),
                        offset: lineStart + token.start))
                }
            }
            return ReadPage(image: UIImage(cgImage: cg), text: text, words: words)
        }.value
    }

    /// Photos can carry a rotation flag; draw them upright first.
    private static func upright(_ image: UIImage) -> CGImage? {
        if image.imageOrientation == .up, let cg = image.cgImage { return cg }
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let drawn = UIGraphicsImageRenderer(size: image.size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: image.size))
        }
        return drawn.cgImage
    }
}
