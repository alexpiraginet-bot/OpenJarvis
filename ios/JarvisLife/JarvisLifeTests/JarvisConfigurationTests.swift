import XCTest
@testable import JarvisLife

final class JarvisConfigurationTests: XCTestCase {
    func testAcceptsHTTPSApplicationURL() {
        let url = JarvisConfiguration.validatedAppURL("https://jarvis.example/vida")
        XCTAssertEqual(url?.absoluteString, "https://jarvis.example/vida")
    }

    func testAcceptsLocalHTTPForSimulatorBuilds() {
        let url = JarvisConfiguration.validatedAppURL("http://127.0.0.1:8100/vida")
        XCTAssertEqual(url?.host, "127.0.0.1")
    }

    func testRejectsNonWebSchemesAndMissingHosts() {
        XCTAssertNil(JarvisConfiguration.validatedAppURL("file:///tmp/vida"))
        XCTAssertNil(JarvisConfiguration.validatedAppURL("not-a-url"))
    }

    func testNativeVoiceLevelNormalization() {
        XCTAssertEqual(NativeSpeechController.normalizedLevel(forRMS: 0), 0)
        XCTAssertEqual(NativeSpeechController.normalizedLevel(forRMS: .nan), 0)
        XCTAssertEqual(
            NativeSpeechController.normalizedLevel(forRMS: 0.01),
            0.3,
            accuracy: 0.001
        )
        XCTAssertEqual(
            NativeSpeechController.normalizedLevel(forRMS: 0.1),
            0.7,
            accuracy: 0.001
        )
        XCTAssertEqual(NativeSpeechController.normalizedLevel(forRMS: 1), 1)
    }

    func testNativeSpeechUsesAudiblePlaybackSession() {
        let profile = NativeSpeechController.speakingAudioProfile

        XCTAssertEqual(profile.category, .playback)
        XCTAssertEqual(profile.mode, .spokenAudio)
        XCTAssertTrue(profile.options.contains(.duckOthers))
    }
}
