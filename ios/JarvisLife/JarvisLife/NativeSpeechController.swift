@preconcurrency import AVFoundation
import Foundation
import OSLog
import Speech

final class NativeSpeechController: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    typealias EventHandler = ([String: Any]) -> Void

    struct AudioSessionProfile {
        let category: AVAudioSession.Category
        let mode: AVAudioSession.Mode
        let options: AVAudioSession.CategoryOptions
    }

    private static let spectrumBinCount = 64
    private static let silenceDelay: TimeInterval = 1.15
    private static let levelEmissionInterval: TimeInterval = 1.0 / 24.0
    private static let listeningAudioProfile = AudioSessionProfile(
        category: .record,
        mode: .measurement,
        options: [.duckOthers, .allowBluetoothHFP]
    )
    static let speakingAudioProfile = AudioSessionProfile(
        category: .playback,
        mode: .spokenAudio,
        options: [.duckOthers]
    )
    private static let logger = Logger(
        subsystem: Bundle.main.bundleIdentifier ?? "JarvisLife",
        category: "voice"
    )

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "pt-BR"))
    private let audioEngine = AVAudioEngine()
    private let synthesizer = AVSpeechSynthesizer()
    private let onEvent: EventHandler

    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var activeUtterance: AVSpeechUtterance?
    private var hasInputTap = false
    private var userRequestedStop = false
    private var latestTranscript = ""
    private var silenceWorkItem: DispatchWorkItem?
    private var lastLevelEmissionTime: TimeInterval = 0

    init(onEvent: @escaping EventHandler) {
        self.onEvent = onEvent
        super.init()
        synthesizer.delegate = self
    }

    func start() {
        guard !audioEngine.isRunning else { return }
        userRequestedStop = false
        cancelSpeech()

        requestSpeechPermission { [weak self] speechAllowed in
            guard let self else { return }
            guard speechAllowed else {
                self.fail("Autorize o reconhecimento de voz nos Ajustes para falar com o Jarvis.")
                return
            }

            self.requestMicrophonePermission { [weak self] microphoneAllowed in
                guard let self else { return }
                guard microphoneAllowed else {
                    self.fail("Autorize o microfone nos Ajustes para falar com o Jarvis.")
                    return
                }
                self.beginRecognition()
            }
        }
    }

    func stop() {
        userRequestedStop = true
        finishRecognition(cancelTask: true)
        cancelSpeech()
        emit(["type": "state", "state": "idle"])
    }

    func speak(_ text: String) {
        let answer = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !answer.isEmpty else { return }

        userRequestedStop = true
        finishRecognition(cancelTask: true)
        cancelSpeech()

        do {
            try activateAudioSession(Self.speakingAudioProfile)
            Self.logger.notice("Speech playback session activated")
        } catch {
            Self.logger.error("Speech playback session failed: \(error.localizedDescription)")
            fail("Não consegui ativar a voz do Jarvis. Tente novamente.")
            return
        }

        let utterance = AVSpeechUtterance(string: answer)
        utterance.voice = AVSpeechSynthesisVoice(language: "pt-BR")
        utterance.rate = 0.51
        utterance.pitchMultiplier = 0.96
        activeUtterance = utterance
        emit(["type": "state", "state": "speaking"])
        synthesizer.speak(utterance)
    }

    private func beginRecognition() {
        guard let recognizer, recognizer.isAvailable else {
            fail("O reconhecimento de voz está temporariamente indisponível.")
            return
        }

        finishRecognition(cancelTask: true)
        userRequestedStop = false
        latestTranscript = ""

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        recognitionRequest = request

        do {
            try activateAudioSession(Self.listeningAudioProfile)
            Self.logger.notice("Speech recognition session activated")

            let inputNode = audioEngine.inputNode
            let format = inputNode.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                finishRecognition(cancelTask: true)
                fail("O microfone não forneceu um formato de áudio válido.")
                return
            }

            inputNode.installTap(onBus: 0, bufferSize: 1_024, format: format) {
                [weak self, weak request] buffer, _ in
                request?.append(buffer)
                self?.emitSignal(from: buffer)
            }
            hasInputTap = true
            audioEngine.prepare()
            try audioEngine.start()
        } catch {
            finishRecognition(cancelTask: true)
            fail("Não consegui iniciar o microfone. Tente novamente.")
            return
        }

        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            DispatchQueue.main.async {
                self?.handleRecognitionResult(result, error: error)
            }
        }

        emit(["type": "state", "state": "listening"])
    }

    private func activateAudioSession(_ profile: AudioSessionProfile) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(
            profile.category,
            mode: profile.mode,
            options: profile.options
        )
        try session.setActive(true, options: .notifyOthersOnDeactivation)
    }

    private func cancelSpeech() {
        activeUtterance = nil
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        deactivateAudioSession()
    }

    private func finishSpeech(_ utterance: AVSpeechUtterance) {
        guard utterance === activeUtterance else { return }
        activeUtterance = nil
        deactivateAudioSession()
        Self.logger.notice("Speech playback session finished")
        emit(["type": "state", "state": "idle"])
    }

    private func deactivateAudioSession() {
        try? AVAudioSession.sharedInstance().setActive(
            false,
            options: .notifyOthersOnDeactivation
        )
    }

    private func handleRecognitionResult(
        _ result: SFSpeechRecognitionResult?,
        error: Error?
    ) {
        if let result {
            let transcript = result.bestTranscription.formattedString
            if !transcript.isEmpty {
                if result.isFinal {
                    deliverFinalTranscript(transcript, cancelTask: false)
                } else {
                    latestTranscript = transcript
                    emit([
                        "type": "transcript",
                        "text": transcript,
                        "final": false,
                    ])
                    scheduleSilenceFinalization(for: transcript)
                }
                return
            }
        }

        if error != nil, !userRequestedStop {
            finishRecognition(cancelTask: true)
            fail("Não consegui entender agora. Toque no microfone e tente de novo.")
        }
    }

    private func finishRecognition(cancelTask: Bool) {
        silenceWorkItem?.cancel()
        silenceWorkItem = nil
        latestTranscript = ""
        if audioEngine.isRunning {
            audioEngine.stop()
        }
        recognitionRequest?.endAudio()
        if hasInputTap {
            audioEngine.inputNode.removeTap(onBus: 0)
            hasInputTap = false
        }
        if cancelTask {
            recognitionTask?.cancel()
        }
        recognitionTask = nil
        recognitionRequest = nil
        deactivateAudioSession()
        emit([
            "type": "level",
            "level": 0,
            "spectrum": Array(repeating: 0, count: Self.spectrumBinCount),
        ])
    }

    private func scheduleSilenceFinalization(for transcript: String) {
        silenceWorkItem?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            guard
                let self,
                !self.userRequestedStop,
                self.audioEngine.isRunning,
                self.latestTranscript == transcript
            else {
                return
            }
            self.deliverFinalTranscript(transcript, cancelTask: true)
        }
        silenceWorkItem = workItem
        DispatchQueue.main.asyncAfter(
            deadline: .now() + Self.silenceDelay,
            execute: workItem
        )
    }

    private func deliverFinalTranscript(_ transcript: String, cancelTask: Bool) {
        let finalText = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !finalText.isEmpty else { return }

        userRequestedStop = true
        finishRecognition(cancelTask: cancelTask)
        emit([
            "type": "transcript",
            "text": finalText,
            "final": true,
        ])
    }

    private func emitSignal(from buffer: AVAudioPCMBuffer) {
        let now = ProcessInfo.processInfo.systemUptime
        guard now - lastLevelEmissionTime >= Self.levelEmissionInterval else {
            return
        }
        lastLevelEmissionTime = now

        guard
            let channel = buffer.floatChannelData?.pointee,
            buffer.frameLength > 0
        else {
            return
        }

        let frameCount = Int(buffer.frameLength)
        var sumOfSquares: Double = 0
        for index in 0 ..< frameCount {
            let sample = Double(channel[index])
            sumOfSquares += sample * sample
        }
        let rms = sqrt(sumOfSquares / Double(frameCount))

        let framesPerBin = max(1, frameCount / Self.spectrumBinCount)
        var spectrum = [Double]()
        spectrum.reserveCapacity(Self.spectrumBinCount)
        for bin in 0 ..< Self.spectrumBinCount {
            let start = bin * framesPerBin
            if start >= frameCount {
                spectrum.append(0)
                continue
            }
            let end = min(frameCount, start + framesPerBin)
            var binSquares: Double = 0
            for index in start ..< end {
                let sample = Double(channel[index])
                binSquares += sample * sample
            }
            let binRMS = sqrt(binSquares / Double(end - start))
            spectrum.append(Self.normalizedLevel(forRMS: binRMS) * 255)
        }

        emit([
            "type": "level",
            "level": Self.normalizedLevel(forRMS: rms),
            "spectrum": spectrum,
        ])
    }

    static func normalizedLevel(forRMS rms: Double) -> Double {
        guard rms.isFinite, rms > 0 else { return 0 }
        let decibels = 20 * log10(rms)
        return min(1, max(0, (decibels + 55) / 50))
    }

    private func requestSpeechPermission(completion: @escaping (Bool) -> Void) {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            completion(true)
        case .notDetermined:
            SFSpeechRecognizer.requestAuthorization { status in
                DispatchQueue.main.async {
                    completion(status == .authorized)
                }
            }
        default:
            completion(false)
        }
    }

    private func requestMicrophonePermission(completion: @escaping (Bool) -> Void) {
        AVAudioApplication.requestRecordPermission { allowed in
            DispatchQueue.main.async {
                completion(allowed)
            }
        }
    }

    private func fail(_ message: String) {
        emit(["type": "error", "text": message])
    }

    private func emit(_ event: [String: Any]) {
        if Thread.isMainThread {
            onEvent(event)
        } else {
            DispatchQueue.main.async { [onEvent] in
                onEvent(event)
            }
        }
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didFinish utterance: AVSpeechUtterance
    ) {
        finishSpeech(utterance)
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didCancel utterance: AVSpeechUtterance
    ) {
        finishSpeech(utterance)
    }
}
