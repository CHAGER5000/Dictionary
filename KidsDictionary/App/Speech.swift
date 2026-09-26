import AVFoundation

/// Says words out loud with the device's own voices (works offline).
final class Speech {
    private let synthesizer = AVSpeechSynthesizer()

    func say(_ text: String) {
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-GB")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.85
        synthesizer.speak(utterance)
    }
}
