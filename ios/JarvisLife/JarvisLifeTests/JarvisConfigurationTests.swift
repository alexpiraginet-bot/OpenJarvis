import CryptoKit
import DeviceCheck
import XCTest
@testable import JarvisLife

final class JarvisConfigurationTests: XCTestCase {
    private func appAttestError(_ code: DCError.Code) -> NSError {
        NSError(domain: DCErrorDomain, code: code.rawValue)
    }

    func testHealthKitUsageDescriptionsDeclareReadOnlyAccess() {
        let readPurpose = Bundle.main.object(
            forInfoDictionaryKey: "NSHealthShareUsageDescription"
        ) as? String
        let updatePurpose = Bundle.main.object(
            forInfoDictionaryKey: "NSHealthUpdateUsageDescription"
        ) as? String

        XCTAssertFalse(readPurpose?.isEmpty ?? true)
        XCTAssertFalse(updatePurpose?.isEmpty ?? true)
        XCTAssertTrue(updatePurpose?.contains("não grava nem altera") ?? false)
        XCTAssertTrue(updatePurpose?.contains("somente para leitura") ?? false)
    }

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

    func testTrustedWebOriginAcceptsWebKitDefaultHTTPSPort() {
        let appURL = URL(string: "https://jarvis-life.vercel.app/vida")!

        XCTAssertTrue(
            JarvisTrustedOrigin.matches(
                scheme: "https",
                host: "jarvis-life.vercel.app",
                port: 0,
                applicationURL: appURL
            )
        )
    }

    func testTrustedWebOriginRejectsAnotherExplicitPort() {
        let appURL = URL(string: "https://jarvis-life.vercel.app/vida")!

        XCTAssertFalse(
            JarvisTrustedOrigin.matches(
                scheme: "https",
                host: "jarvis-life.vercel.app",
                port: 8443,
                applicationURL: appURL
            )
        )
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

    func testNativeDeviceIdentityIsStableAndNotSecretMaterial() {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let first = NativeDeviceIdentity.stableID(defaults: defaults)
        let second = NativeDeviceIdentity.stableID(defaults: defaults)

        XCTAssertEqual(first, second)
        XCTAssertTrue(first.hasPrefix("ios-"))
        XCTAssertNotNil(UUID(uuidString: String(first.dropFirst(4))))
    }

    @MainActor
    func testNativeCalendarPermissionEmitsADeviceGrantReceipt() async {
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        await controller.requestCalendarPermission(
            ["provider": "apple_calendar"],
            requestID: "request-calendar-4"
        )

        XCTAssertEqual(received["type"] as? String, "deviceGrant")
        XCTAssertEqual(received["status"] as? String, "granted")
        XCTAssertEqual(received["provider"] as? String, "apple_calendar")
        XCTAssertEqual(received["requestId"] as? String, "request-calendar-4")
        XCTAssertEqual(received["deviceId"] as? String, "ios-device-1234")
        XCTAssertEqual(
            received["grantedScopes"] as? [String],
            ["events.read", "events.write"]
        )
    }

    @MainActor
    func testNativeCalendarPermissionDoesNotEmitAGrantWhenAccessIsDenied() async {
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { false },
            hasFullCalendarAccess: { false },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        await controller.requestCalendarPermission(
            ["provider": "apple_calendar"],
            requestID: "request-calendar-5"
        )

        XCTAssertEqual(received["status"] as? String, "denied")
        XCTAssertNil(received["deviceId"])
        XCTAssertNil(received["grantedScopes"])
    }

    func testAppAttestKeyReferenceCanBeRotatedPerAccount() {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let accountID = String(repeating: "a", count: 32)

        NativeDeviceIdentity.storeAppAttestKeyID(
            "old-key",
            accountID: accountID,
            defaults: defaults
        )
        NativeDeviceIdentity.clearAppAttestKeyID(
            accountID: accountID,
            defaults: defaults
        )

        XCTAssertNil(
            NativeDeviceIdentity.appAttestKeyID(
                accountID: accountID,
                defaults: defaults
            )
        )
    }

    @MainActor
    func testInvalidAppAttestKeyRotatesOnceBeforeAttestationSucceeds() async {
        let accountID = String(repeating: "a", count: 32)
        let oldKey = String(repeating: "o", count: 43)
        let newKey = String(repeating: "n", count: 43)
        var attestedKeys: [String] = []
        var invalidatedAccounts: [String] = []
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestKey: { _ in newKey },
            requestAttestation: { keyID, _ in
                attestedKeys.append(keyID)
                if keyID == oldKey {
                    throw self.appAttestError(.invalidKey)
                }
                return Data(repeating: 9, count: 64)
            },
            invalidateAppAttestKey: { invalidatedAccounts.append($0) },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "attest",
            "requestId": "request-attest-rotation",
            "accountId": accountID,
            "keyId": oldKey,
            "challenge": String(repeating: "c", count: 43),
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(attestedKeys, [oldKey, newKey])
        XCTAssertEqual(invalidatedAccounts, [accountID])
        XCTAssertEqual(received["status"] as? String, "ready")
        XCTAssertEqual(received["keyId"] as? String, newKey)
    }

    @MainActor
    func testServerUnavailableRetriesAttestationOnceWithTheSameKey() async {
        let accountID = String(repeating: "b", count: 32)
        let keyID = String(repeating: "k", count: 43)
        var attestedKeys: [String] = []
        var invalidatedAccounts: [String] = []
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestKey: { _ in XCTFail("must not generate a new key"); return "" },
            requestAttestation: { attemptedKey, _ in
                attestedKeys.append(attemptedKey)
                if attestedKeys.count == 1 {
                    throw self.appAttestError(.serverUnavailable)
                }
                return Data(repeating: 5, count: 64)
            },
            invalidateAppAttestKey: { invalidatedAccounts.append($0) },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "attest",
            "requestId": "request-attest-unavailable",
            "accountId": accountID,
            "keyId": keyID,
            "challenge": String(repeating: "c", count: 43),
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(attestedKeys, [keyID, keyID])
        XCTAssertTrue(invalidatedAccounts.isEmpty)
        XCTAssertEqual(received["status"] as? String, "ready")
        XCTAssertEqual(received["keyId"] as? String, keyID)
    }

    @MainActor
    func testInvalidAppAttestKeyRotatesAtMostOnce() async {
        let accountID = String(repeating: "c", count: 32)
        let oldKey = String(repeating: "o", count: 43)
        let newKey = String(repeating: "n", count: 43)
        var attestedKeys: [String] = []
        var invalidatedAccounts: [String] = []
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestKey: { _ in newKey },
            requestAttestation: { attemptedKey, _ in
                attestedKeys.append(attemptedKey)
                throw self.appAttestError(.invalidKey)
            },
            invalidateAppAttestKey: { invalidatedAccounts.append($0) },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "attest",
            "requestId": "request-attest-one-rotation",
            "accountId": accountID,
            "keyId": oldKey,
            "challenge": String(repeating: "c", count: 43),
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(attestedKeys, [oldKey, newKey])
        XCTAssertEqual(invalidatedAccounts, [accountID])
        XCTAssertEqual(received["status"] as? String, "error")
    }

    @MainActor
    func testCalendarReadEmitsOnlyTenUpcomingEvents() async {
        let events = (0..<12).map { index in
            NativeCalendarReceipt(
                id: "event-\(index)",
                title: "Compromisso \(index)",
                startAt: "2026-08-\(String(format: "%02d", index + 14))T15:00:00-03:00",
                endAt: "2026-08-\(String(format: "%02d", index + 14))T16:00:00-03:00",
                isAllDay: false,
                location: "",
                calendarTitle: "Pessoal"
            )
        }
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            readCalendarEvents: { events },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "readCalendarEvents",
            "requestId": "request-read-calendar",
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        let emitted = received["events"] as? [[String: Any]]
        XCTAssertEqual(received["status"] as? String, "ready")
        XCTAssertEqual(emitted?.count, 10)
        XCTAssertEqual(emitted?.first?["start_at"] as? String, events[0].startAt)
        XCTAssertNil(emitted?.first?["startAt"])
    }

    func testUpcomingCalendarWindowEndsAfterThirtyOneDays() {
        let calendar = Calendar(identifier: .gregorian)
        let start = Date(timeIntervalSince1970: 1_800_000_000)

        let window = NativeIntegrationController.upcomingCalendarWindow(
            from: start,
            calendar: calendar
        )

        XCTAssertEqual(window.start, start)
        XCTAssertEqual(
            window.end,
            calendar.date(byAdding: .day, value: 31, to: start)
        )
    }

    func testCalendarReceiptCacheStoresOnlyAccountBoundDigestsAndExpires() {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        var now = Date(timeIntervalSince1970: 1_800_000_000)
        let cache = NativeCalendarReceiptCache(
            defaults: defaults,
            now: { now },
            timeToLive: 15 * 60
        )
        let accountID = String(repeating: "a", count: 32)
        let otherAccountID = String(repeating: "b", count: 32)
        let proposalID = "proposal-calendar-private-1234"
        let write = NativeCalendarWrite(
            title: "Reunião confidencial",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            startDate: Date(timeIntervalSince1970: 1_786_742_400),
            endDate: Date(timeIntervalSince1970: 1_786_746_000),
            isAllDay: false,
            location: "Sala secreta",
            notes: "Aquisição sigilosa"
        )

        cache.store(
            eventIdentifier: "event-private-1234",
            for: proposalID,
            accountID: accountID,
            write: write
        )

        XCTAssertTrue(
            cache.hasReceipt(
                for: proposalID,
                accountID: accountID,
                matching: write
            )
        )
        XCTAssertFalse(
            cache.hasReceipt(
                for: proposalID,
                accountID: otherAccountID,
                matching: write
            )
        )
        let persisted = defaults.dictionaryRepresentation().values
            .compactMap { $0 as? Data }
            .compactMap { String(data: $0, encoding: .utf8) }
            .joined(separator: "\n")
        for sensitive in [
            accountID,
            proposalID,
            "event-private-1234",
            write.title,
            write.startAt,
            write.endAt,
            write.location,
            write.notes,
        ] {
            XCTAssertFalse(persisted.contains(sensitive), "persisted sensitive value: \(sensitive)")
        }

        now.addTimeInterval(15 * 60 + 1)
        XCTAssertFalse(
            cache.hasReceipt(
                for: proposalID,
                accountID: accountID,
                matching: write
            )
        )
        XCTAssertTrue(
            (defaults.persistentDomain(forName: suite) ?? [:]).values
                .compactMap { $0 as? Data }.isEmpty
        )
    }

    func testCalendarDisconnectCleanupPurgesLegacySensitiveReceipts() {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let legacyKey = "jarvis.native.calendar-receipt.legacy-proposal"
        defaults.set(
            Data(
                #"{"proposalID":"legacy-proposal","write":{"title":"Legacy secret"}}"#
                    .utf8
            ),
            forKey: legacyKey
        )
        let cache = NativeCalendarReceiptCache(defaults: defaults)

        cache.removeAll(accountID: String(repeating: "a", count: 32))

        XCTAssertNil(defaults.data(forKey: legacyKey))
    }

    @MainActor
    func testCalendarReceiptCleanupRemovesOnlyTheResolvedProposal() async {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let cache = NativeCalendarReceiptCache(defaults: defaults)
        let accountID = String(repeating: "a", count: 32)
        let write = NativeCalendarWrite(
            title: "Marketing",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            startDate: Date(timeIntervalSince1970: 1_786_742_400),
            endDate: Date(timeIntervalSince1970: 1_786_746_000),
            isAllDay: false,
            location: "",
            notes: ""
        )
        cache.store(
            eventIdentifier: "event-1",
            for: "proposal-calendar-cleanup-1",
            accountID: accountID,
            write: write
        )
        cache.store(
            eventIdentifier: "event-2",
            for: "proposal-calendar-cleanup-2",
            accountID: accountID,
            write: write
        )
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            calendarReceiptCache: cache,
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "clearCalendarReceipts",
            "requestId": "request-clear-one",
            "accountId": accountID,
            "proposalId": "proposal-calendar-cleanup-1",
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(received["type"] as? String, "calendarReceiptsCleared")
        XCTAssertEqual(received["status"] as? String, "ready")
        XCTAssertFalse(
            cache.hasReceipt(
                for: "proposal-calendar-cleanup-1",
                accountID: accountID,
                matching: write
            )
        )
        XCTAssertTrue(
            cache.hasReceipt(
                for: "proposal-calendar-cleanup-2",
                accountID: accountID,
                matching: write
            )
        )
    }

    @MainActor
    func testCalendarDisconnectCleanupIsBoundToOneAccount() async {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let cache = NativeCalendarReceiptCache(defaults: defaults)
        let accountID = String(repeating: "a", count: 32)
        let otherAccountID = String(repeating: "b", count: 32)
        let proposalID = "proposal-calendar-account-cleanup"
        let write = NativeCalendarWrite(
            title: "Marketing",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            startDate: Date(timeIntervalSince1970: 1_786_742_400),
            endDate: Date(timeIntervalSince1970: 1_786_746_000),
            isAllDay: false,
            location: "",
            notes: ""
        )
        for account in [accountID, otherAccountID] {
            cache.store(
                eventIdentifier: "event-\(account.first!)",
                for: proposalID,
                accountID: account,
                write: write
            )
        }
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            calendarReceiptCache: cache,
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "clearCalendarReceipts",
            "requestId": "request-clear-account",
            "accountId": accountID,
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(received["type"] as? String, "calendarReceiptsCleared")
        XCTAssertFalse(
            cache.hasReceipt(
                for: proposalID,
                accountID: accountID,
                matching: write
            )
        )
        XCTAssertTrue(
            cache.hasReceipt(
                for: proposalID,
                accountID: otherAccountID,
                matching: write
            )
        )
    }

    @MainActor
    func testCalendarReceiptPersistsAcrossControllerRecreationForTheSameProposal() async {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let cache = NativeCalendarReceiptCache(defaults: defaults)
        let proposalID = "proposal-calendar-persistent-1234"
        let event = NativeCalendarReceipt(
            id: "event-persistent",
            title: "Marketing",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            isAllDay: false,
            location: "",
            calendarTitle: "Pessoal"
        )
        var saveCalls = 0
        var savedByMarker: [String: NativeCalendarReceipt] = [:]
        var receipts: [[String: Any]] = []
        func makeController() -> NativeIntegrationController {
            NativeIntegrationController(
                requestAssertion: { _, _ in Data(repeating: 7, count: 64) },
                requestCalendarAccess: { true },
                hasFullCalendarAccess: { true },
                saveCalendarEvent: { accountID, proposalID, _ in
                    let marker = "\(accountID):\(proposalID)"
                    if let existing = savedByMarker[marker] { return existing }
                    saveCalls += 1
                    savedByMarker[marker] = event
                    return event
                },
                calendarReceiptCache: cache,
                deviceID: { "ios-device-1234" },
                deviceLabel: { "iPhone" },
                send: { receipts.append($0) }
            )
        }
        let payload: [String: Any] = [
            "action": "createCalendarEvent",
            "proposalId": proposalID,
            "claimToken": "claim-token-abcdefghijklmnopqrstuvwxyz",
            "accountId": String(repeating: "a", count: 32),
            "keyId": String(repeating: "k", count: 43),
            "event": [
                "title": "Marketing",
                "start_at": "2026-08-14T15:00:00-03:00",
                "end_at": "2026-08-14T16:00:00-03:00",
            ],
        ]

        var firstPayload = payload
        firstPayload["requestId"] = "request-save-first"
        let firstController = makeController()
        firstController.handle(firstPayload)
        for _ in 0..<100 where receipts.count < 1 { await Task.yield() }
        var secondPayload = payload
        secondPayload["requestId"] = "request-save-retry"
        let secondController = makeController()
        secondController.handle(secondPayload)
        for _ in 0..<100 where receipts.count < 2 { await Task.yield() }

        XCTAssertEqual(saveCalls, 1)
        XCTAssertEqual(receipts.count, 2)
        XCTAssertEqual(
            (receipts[0]["event"] as? [String: Any])?["id"] as? String,
            "event-persistent"
        )
        XCTAssertEqual(
            (receipts[1]["event"] as? [String: Any])?["id"] as? String,
            "event-persistent"
        )
    }

    @MainActor
    func testCalendarRetryRejectsChangedWriteForTheSameAccountAndProposal() async {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let cache = NativeCalendarReceiptCache(defaults: defaults)
        let accountID = String(repeating: "a", count: 32)
        let proposalID = "proposal-calendar-immutable-1234"
        let original = NativeCalendarWrite(
            title: "Marketing",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            startDate: Date(timeIntervalSince1970: 1_786_742_400),
            endDate: Date(timeIntervalSince1970: 1_786_746_000),
            isAllDay: false,
            location: "",
            notes: ""
        )
        cache.store(
            eventIdentifier: "event-original",
            for: proposalID,
            accountID: accountID,
            write: original
        )
        var saveCalls = 0
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestAssertion: { _, _ in Data(repeating: 7, count: 64) },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            saveCalendarEvent: { _, _, _ in
                saveCalls += 1
                return NativeCalendarReceipt(
                    id: "event-mutated",
                    title: "Outro assunto",
                    startAt: original.startAt,
                    endAt: original.endAt,
                    isAllDay: false,
                    location: "",
                    calendarTitle: "Pessoal"
                )
            },
            calendarReceiptCache: cache,
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "createCalendarEvent",
            "requestId": "request-save-mutated",
            "proposalId": proposalID,
            "claimToken": "claim-token-abcdefghijklmnopqrstuvwxyz",
            "accountId": accountID,
            "keyId": String(repeating: "k", count: 43),
            "event": [
                "title": "Outro assunto",
                "start_at": original.startAt,
                "end_at": original.endAt,
            ],
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(received["status"] as? String, "error")
        XCTAssertEqual(saveCalls, 0)
    }

    @MainActor
    func testCalendarRetryForAnotherAccountCannotReuseTheSameEvent() async {
        let suite = "JarvisConfigurationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let cache = NativeCalendarReceiptCache(defaults: defaults)
        let firstAccount = String(repeating: "a", count: 32)
        let secondAccount = String(repeating: "b", count: 32)
        let proposalID = "proposal-calendar-account-bound-1234"
        var eventByMarker: [String: NativeCalendarReceipt] = [:]
        var saveCalls = 0
        let cacheEvent = NativeCalendarReceipt(
            id: "event-first-account",
            title: "Marketing",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            isAllDay: false,
            location: "",
            calendarTitle: "Pessoal"
        )
        var receipts: [[String: Any]] = []
        func makeController() -> NativeIntegrationController {
            NativeIntegrationController(
                requestAssertion: { _, _ in Data(repeating: 7, count: 64) },
                requestCalendarAccess: { true },
                hasFullCalendarAccess: { true },
                saveCalendarEvent: { accountID, candidateProposal, write in
                    let marker = "\(accountID):\(candidateProposal)"
                    if let existing = eventByMarker[marker] { return existing }
                    saveCalls += 1
                    let event = NativeCalendarReceipt(
                        id: accountID == firstAccount
                            ? cacheEvent.id
                            : "event-second-account",
                        title: write.title,
                        startAt: write.startAt,
                        endAt: write.endAt,
                        isAllDay: write.isAllDay,
                        location: write.location,
                        calendarTitle: "Pessoal"
                    )
                    eventByMarker[marker] = event
                    return event
                },
                calendarReceiptCache: cache,
                deviceID: { "ios-device-1234" },
                deviceLabel: { "iPhone" },
                send: { receipts.append($0) }
            )
        }
        let common: [String: Any] = [
            "action": "createCalendarEvent",
            "proposalId": proposalID,
            "claimToken": "claim-token-abcdefghijklmnopqrstuvwxyz",
            "keyId": String(repeating: "k", count: 43),
            "event": [
                "title": "Marketing" as Any,
                "start_at": cacheEvent.startAt as Any,
                "end_at": cacheEvent.endAt as Any,
            ] as [String: Any],
        ]
        var controllers: [NativeIntegrationController] = []
        for (index, accountID) in [firstAccount, secondAccount].enumerated() {
            var payload = common
            payload["requestId"] = "request-account-\(index)"
            payload["accountId"] = accountID
            let controller = makeController()
            controllers.append(controller)
            controller.handle(payload)
            for _ in 0..<100 where receipts.count <= index { await Task.yield() }
        }

        XCTAssertEqual(receipts.count, 2, "native replies: \(receipts)")
        XCTAssertEqual(saveCalls, 2)
        XCTAssertEqual(
            (receipts.first?["event"] as? [String: Any])?["id"] as? String,
            "event-first-account"
        )
        XCTAssertEqual(
            (receipts.last?["event"] as? [String: Any])?["id"] as? String,
            "event-second-account"
        )
    }

    @MainActor
    func testCalendarSaveSignsTheExactEventKitResult() async {
        var received: [String: Any] = [:]
        var signedHash = Data()
        let event = NativeCalendarReceipt(
            id: "event-123",
            title: "Marketing",
            startAt: "2026-08-14T15:00:00-03:00",
            endAt: "2026-08-14T16:00:00-03:00",
            isAllDay: false,
            location: "",
            calendarTitle: "Pessoal"
        )
        let controller = NativeIntegrationController(
            requestAssertion: { _, hash in
                signedHash = hash
                return Data(repeating: 7, count: 64)
            },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            saveCalendarEvent: { _, _, _ in event },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "createCalendarEvent",
            "requestId": "request-save-1",
            "proposalId": "proposal-calendar-1234",
            "claimToken": "claim-token-abcdefghijklmnopqrstuvwxyz",
            "accountId": String(repeating: "a", count: 32),
            "keyId": String(repeating: "k", count: 43),
            "event": [
                "title": "Marketing",
                "start_at": "2026-08-14T15:00:00-03:00",
                "end_at": "2026-08-14T16:00:00-03:00",
            ],
        ])
        for _ in 0..<100 where received.isEmpty {
            await Task.yield()
        }

        XCTAssertEqual(received["status"] as? String, "saved")
        XCTAssertEqual(received["keyId"] as? String, String(repeating: "k", count: 43))
        XCTAssertNotNil(received["assertion"] as? String)
        XCTAssertEqual(
            signedHash,
            Data(SHA256.hash(data: NativeIntegrationController.nativeResultData(
                claimToken: "claim-token-abcdefghijklmnopqrstuvwxyz",
                proposalID: "proposal-calendar-1234",
                deviceID: "ios-device-1234",
                event: event
            )))
        )
    }

    @MainActor
    func testFinanceAssertionRequiresBiometricsEvenWhenJavaScriptSaysFalse() async {
        var received: [String: Any] = [:]
        var biometricCalls = 0
        let body = try! JSONSerialization.data(withJSONObject: [
            "challenge": "c",
            "confirmation_method": "explicit",
            "device_id": "ios-device-1234",
            "purpose": "finance",
            "resource_id": "finance:record",
            "user_id": String(repeating: "a", count: 32),
        ], options: [.sortedKeys])
        let clientData = body.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        let controller = NativeIntegrationController(
            requestAssertion: { _, _ in Data(repeating: 1, count: 64) },
            requestBiometric: { biometricCalls += 1 },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        controller.handle([
            "action": "assert",
            "requestId": "request-finance-1",
            "accountId": String(repeating: "a", count: 32),
            "keyId": String(repeating: "k", count: 43),
            "clientData": clientData,
            "requireBiometric": false,
        ])
        for _ in 0..<100 where received.isEmpty {
            await Task.yield()
        }

        XCTAssertEqual(biometricCalls, 1)
        XCTAssertEqual(received["status"] as? String, "ready")
    }

    @MainActor
    func testNativeHealthPermissionEmitsTheReadOnlyHealthKitScopes() async {
        var received: [String: Any] = [:]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            requestHealthAccess: { true },
            healthDataAvailable: { true },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        await controller.requestHealthPermission(
            ["provider": "apple_health"],
            requestID: "request-health-grant-1"
        )

        XCTAssertEqual(received["type"] as? String, "deviceGrant")
        XCTAssertEqual(received["status"] as? String, "granted")
        XCTAssertEqual(received["provider"] as? String, "apple_health")
        XCTAssertEqual(received["deviceId"] as? String, "ios-device-1234")
        XCTAssertEqual(
            received["grantedScopes"] as? [String],
            [
                "steps.read",
                "sleep.read",
                "heart_rate.read",
                "resting_heart_rate.read",
                "active_energy.read",
                "workouts.read",
            ]
        )
    }

    @MainActor
    func testNativeHealthReadReturnsABoundedOpaquePayloadAndDigest() async {
        var received: [String: Any] = [:]
        let samples = [
            NativeHealthSample(
                sampleID: "steps:2026-08-14",
                kind: "steps",
                value: 8421,
                unit: "count",
                observedAt: "2026-08-14T12:00:00Z"
            )
        ]
        let controller = NativeIntegrationController(
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            requestHealthAccess: { true },
            healthDataAvailable: { true },
            readHealthData: { samples },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )

        await controller.sendHealthData(requestID: "request-health-read-1")

        XCTAssertEqual(received["type"] as? String, "healthData")
        XCTAssertEqual(received["status"] as? String, "ready")
        XCTAssertEqual(received["sampleCount"] as? Int, 1)
        XCTAssertEqual(received["deviceId"] as? String, "ios-device-1234")
        XCTAssertTrue((received["resourceId"] as? String)?.hasPrefix("health:") == true)
        let encoded = received["payload"] as? String ?? ""
        let raw = NativeIntegrationController.base64URLDataForTesting(encoded)
        XCTAssertEqual(
            String(data: raw ?? Data(), encoding: .utf8),
            "{\"samples\":[{\"kind\":\"steps\",\"observed_at\":\"2026-08-14T12:00:00Z\",\"sample_id\":\"steps:2026-08-14\",\"unit\":\"count\",\"value\":8421}]}"
        )
        XCTAssertEqual(
            received["resourceId"] as? String,
            "health:4ab320d62432fe8067e63675cccf3e1a6e54d43e3423738a761278b96383177e"
        )
        let object = try? JSONSerialization.jsonObject(with: raw ?? Data())
        let body = object as? [String: Any]
        XCTAssertEqual((body?["samples"] as? [[String: Any]])?.count, 1)
    }

    @MainActor
    func testHealthSyncAssertionRejectsAResourceNotProducedByHealthKit() async {
        var received: [String: Any] = [:]
        var assertionCalls = 0
        let controller = NativeIntegrationController(
            requestAssertion: { _, _ in
                assertionCalls += 1
                return Data(repeating: 1, count: 64)
            },
            requestCalendarAccess: { true },
            hasFullCalendarAccess: { true },
            requestHealthAccess: { true },
            healthDataAvailable: { true },
            readHealthData: { [] },
            deviceID: { "ios-device-1234" },
            deviceLabel: { "iPhone" },
            send: { received = $0 }
        )
        await controller.sendHealthData(requestID: "request-health-read-2")
        let body = try! JSONSerialization.data(withJSONObject: [
            "challenge": "c",
            "confirmation_method": "",
            "device_id": "ios-device-1234",
            "purpose": "health_sync",
            "resource_id": "health:\(String(repeating: "f", count: 64))",
            "user_id": String(repeating: "a", count: 32),
        ], options: [.sortedKeys])
        let clientData = body.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        received = [:]

        controller.handle([
            "action": "assert",
            "requestId": "request-health-assert-1",
            "accountId": String(repeating: "a", count: 32),
            "keyId": String(repeating: "k", count: 43),
            "clientData": clientData,
            "requireBiometric": false,
        ])
        for _ in 0..<100 where received.isEmpty { await Task.yield() }

        XCTAssertEqual(assertionCalls, 0)
        XCTAssertEqual(received["status"] as? String, "denied")
    }
}
