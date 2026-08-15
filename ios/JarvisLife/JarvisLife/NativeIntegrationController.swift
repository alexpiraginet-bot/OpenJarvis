import CryptoKit
import DeviceCheck
import EventKit
import Foundation
import HealthKit
import LocalAuthentication
import UIKit

/// Stable installation identity plus account-scoped App Attest key references.
enum NativeDeviceIdentity {
    private static let installationKey = "jarvis.native.installation-id"
    private static let appAttestKeyPrefix = "jarvis.native.app-attest-key."

    static func stableID(defaults: UserDefaults = .standard) -> String {
        if
            let stored = defaults.string(forKey: installationKey),
            stored.hasPrefix("ios-"),
            UUID(uuidString: String(stored.dropFirst(4))) != nil
        {
            return stored
        }

        let generated = "ios-\(UUID().uuidString.lowercased())"
        defaults.set(generated, forKey: installationKey)
        return generated
    }

    static func appAttestKeyID(
        accountID: String,
        defaults: UserDefaults = .standard
    ) -> String? {
        defaults.string(forKey: appAttestKeyPrefix + accountID)
    }

    static func storeAppAttestKeyID(
        _ keyID: String,
        accountID: String,
        defaults: UserDefaults = .standard
    ) {
        defaults.set(keyID, forKey: appAttestKeyPrefix + accountID)
    }

    static func clearAppAttestKeyID(
        accountID: String,
        defaults: UserDefaults = .standard
    ) {
        defaults.removeObject(forKey: appAttestKeyPrefix + accountID)
    }
}

enum NativeIntegrationError: LocalizedError {
    case unavailable
    case invalidPayload
    case denied
    case failed

    var errorDescription: String? {
        switch self {
        case .unavailable:
            "A proteção nativa deste iPhone não está disponível."
        case .invalidPayload:
            "O Jarvis recebeu dados inválidos para esta ação."
        case .denied:
            "A autenticação do iPhone não foi confirmada."
        case .failed:
            "O iPhone não conseguiu concluir esta ação."
        }
    }
}

struct NativeCalendarWrite: Codable, Equatable {
    let title: String
    let startAt: String
    let endAt: String
    let startDate: Date
    let endDate: Date
    let isAllDay: Bool
    let location: String
    let notes: String
}

struct NativeCalendarReceipt: Codable, Equatable {
    let id: String
    let title: String
    let startAt: String
    let endAt: String
    let isAllDay: Bool
    let location: String
    let calendarTitle: String

    var dictionary: [String: Any] {
        [
            "id": id,
            "title": title,
            "startAt": startAt,
            "endAt": endAt,
            "isAllDay": isAllDay,
            "location": location,
            "calendarTitle": calendarTitle,
        ]
    }

    var contextDictionary: [String: Any] {
        [
            "id": id,
            "title": title,
            "start_at": startAt,
            "end_at": endAt,
            "is_all_day": isAllDay,
            "location": location,
            "calendar_title": calendarTitle,
        ]
    }
}

struct NativeHealthSample: Codable, Equatable {
    let sampleID: String
    let kind: String
    let value: Double
    let unit: String
    let observedAt: String

    enum CodingKeys: String, CodingKey {
        case sampleID = "sample_id"
        case kind
        case value
        case unit
        case observedAt = "observed_at"
    }
}

private struct NativeHealthPayload: Codable {
    let samples: [NativeHealthSample]
}

/// Small durable receipt index. EventKit remains authoritative; the cache only
/// prevents repeating a write while a claim-bound assertion is being retried.
final class NativeCalendarReceiptCache {
    private struct Entry: Codable {
        let version: Int
        let accountDigest: String
        let proposalDigest: String
        let writeDigest: String
        let eventDigest: String
        let expiresAt: Date
    }

    private let defaults: UserDefaults
    private let keyPrefix = "jarvis.native.calendar-receipt."
    private let now: () -> Date
    private let timeToLive: TimeInterval

    init(
        defaults: UserDefaults = .standard,
        now: @escaping () -> Date = Date.init,
        timeToLive: TimeInterval = 15 * 60
    ) {
        self.defaults = defaults
        self.now = now
        self.timeToLive = max(1, timeToLive)
    }

    func hasReceipt(
        for proposalID: String,
        accountID: String,
        matching write: NativeCalendarWrite
    ) -> Bool {
        pruneExpired()
        guard
            let data = defaults.data(forKey: key(for: proposalID, accountID: accountID)),
            let entry = try? JSONDecoder().decode(Entry.self, from: data),
            entry.version == 1,
            entry.expiresAt > now(),
            entry.accountDigest == Self.digest(["account", accountID]),
            entry.proposalDigest == Self.digest(["proposal", proposalID]),
            entry.writeDigest == Self.writeDigest(write)
        else {
            return false
        }
        return true
    }

    func containsReceipt(for proposalID: String, accountID: String) -> Bool {
        pruneExpired()
        guard
            let data = defaults.data(forKey: key(for: proposalID, accountID: accountID)),
            let entry = try? JSONDecoder().decode(Entry.self, from: data),
            entry.version == 1,
            entry.expiresAt > now(),
            entry.accountDigest == Self.digest(["account", accountID]),
            entry.proposalDigest == Self.digest(["proposal", proposalID])
        else {
            return false
        }
        return true
    }

    func store(
        eventIdentifier: String,
        for proposalID: String,
        accountID: String,
        write: NativeCalendarWrite
    ) {
        pruneExpired()
        guard let data = try? JSONEncoder().encode(
            Entry(
                version: 1,
                accountDigest: Self.digest(["account", accountID]),
                proposalDigest: Self.digest(["proposal", proposalID]),
                writeDigest: Self.writeDigest(write),
                eventDigest: Self.digest(["event", eventIdentifier]),
                expiresAt: now().addingTimeInterval(timeToLive)
            )
        ) else {
            return
        }
        defaults.set(data, forKey: key(for: proposalID, accountID: accountID))
    }

    func remove(for proposalID: String, accountID: String) {
        defaults.removeObject(forKey: key(for: proposalID, accountID: accountID))
    }

    func removeAll(accountID: String) {
        // Legacy entries had no account binding or expiry and stored the full
        // write. Purge every undecodable/expired legacy entry before applying
        // the account-scoped cleanup to the current digest-only schema.
        pruneExpired()
        let expected = Self.digest(["account", accountID])
        for (key, value) in defaults.dictionaryRepresentation()
        where key.hasPrefix(keyPrefix) {
            guard
                let data = value as? Data,
                let entry = try? JSONDecoder().decode(Entry.self, from: data),
                entry.accountDigest == expected
            else {
                continue
            }
            defaults.removeObject(forKey: key)
        }
    }

    private func pruneExpired() {
        let reference = now()
        for (key, value) in defaults.dictionaryRepresentation()
        where key.hasPrefix(keyPrefix) {
            guard
                let data = value as? Data,
                let entry = try? JSONDecoder().decode(Entry.self, from: data),
                entry.version == 1,
                entry.expiresAt > reference
            else {
                defaults.removeObject(forKey: key)
                continue
            }
        }
    }

    private func key(for proposalID: String, accountID: String) -> String {
        keyPrefix + Self.digest(["cache-key", accountID, proposalID])
    }

    private static func writeDigest(_ write: NativeCalendarWrite) -> String {
        digest([
            "write-v1",
            write.title,
            write.startAt,
            write.endAt,
            write.isAllDay ? "1" : "0",
            write.location,
            write.notes,
        ])
    }

    private static func digest(_ values: [String]) -> String {
        var data = Data()
        for value in values {
            let bytes = Data(value.utf8)
            data.append(Data("\(bytes.count):".utf8))
            data.append(bytes)
        }
        return SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

/// App Attest, Face ID and EventKit bridge for the embedded Jarvis web UI.
final class NativeIntegrationController {
    typealias KeyRequest = (String) async throws -> String
    typealias AttestationRequest = (String, Data) async throws -> Data
    typealias AssertionRequest = (String, Data) async throws -> Data
    typealias BiometricRequest = () async throws -> Void
    typealias CalendarAccessRequest = () async throws -> Bool
    typealias CalendarRead = () throws -> [NativeCalendarReceipt]
    typealias CalendarSave = (
        String,
        String,
        NativeCalendarWrite
    ) throws -> NativeCalendarReceipt
    typealias HealthAccessRequest = () async throws -> Bool
    typealias HealthRead = () async throws -> [NativeHealthSample]
    typealias KeyInvalidation = (String) -> Void

    private let requestKey: KeyRequest
    private let requestAttestation: AttestationRequest
    private let requestAssertion: AssertionRequest
    private let requestBiometric: BiometricRequest
    private let invalidateAppAttestKey: KeyInvalidation
    private let requestCalendarAccess: CalendarAccessRequest
    private let hasFullCalendarAccess: () -> Bool
    private let readCalendarEvents: CalendarRead
    private let saveCalendarEvent: CalendarSave
    private let requestHealthAccess: HealthAccessRequest
    private let healthDataAvailable: () -> Bool
    private let readHealthData: HealthRead
    private let calendarReceiptCache: NativeCalendarReceiptCache
    private let deviceID: () -> String
    private let deviceLabel: () -> String
    private let send: ([String: Any]) -> Void
    private var pendingHealthSyncResourceID: String?

    init(
        requestKey: @escaping KeyRequest = { _ in "test-key" },
        requestAttestation: @escaping AttestationRequest = { _, _ in Data() },
        requestAssertion: @escaping AssertionRequest = { _, _ in Data() },
        requestBiometric: @escaping BiometricRequest = {},
        invalidateAppAttestKey: @escaping KeyInvalidation = { _ in },
        requestCalendarAccess: @escaping CalendarAccessRequest,
        hasFullCalendarAccess: @escaping () -> Bool,
        readCalendarEvents: @escaping CalendarRead = { [] },
        saveCalendarEvent: @escaping CalendarSave = { _, _, _ in
            throw NativeIntegrationError.unavailable
        },
        requestHealthAccess: @escaping HealthAccessRequest = { false },
        healthDataAvailable: @escaping () -> Bool = { false },
        readHealthData: @escaping HealthRead = { [] },
        calendarReceiptCache: NativeCalendarReceiptCache = NativeCalendarReceiptCache(),
        deviceID: @escaping () -> String,
        deviceLabel: @escaping () -> String,
        send: @escaping ([String: Any]) -> Void
    ) {
        self.requestKey = requestKey
        self.requestAttestation = requestAttestation
        self.requestAssertion = requestAssertion
        self.requestBiometric = requestBiometric
        self.invalidateAppAttestKey = invalidateAppAttestKey
        self.requestCalendarAccess = requestCalendarAccess
        self.hasFullCalendarAccess = hasFullCalendarAccess
        self.readCalendarEvents = readCalendarEvents
        self.saveCalendarEvent = saveCalendarEvent
        self.requestHealthAccess = requestHealthAccess
        self.healthDataAvailable = healthDataAvailable
        self.readHealthData = readHealthData
        self.calendarReceiptCache = calendarReceiptCache
        self.deviceID = deviceID
        self.deviceLabel = deviceLabel
        self.send = send
    }

    convenience init(send: @escaping ([String: Any]) -> Void) {
        let eventStore = EKEventStore()
        let healthStore = HKHealthStore()
        let appAttest = DCAppAttestService.shared
        self.init(
            requestKey: { accountID in
                guard appAttest.isSupported else {
                    throw NativeIntegrationError.unavailable
                }
                if let stored = NativeDeviceIdentity.appAttestKeyID(accountID: accountID) {
                    return stored
                }
                let keyID = try await Self.generateAppAttestKey(appAttest)
                NativeDeviceIdentity.storeAppAttestKeyID(keyID, accountID: accountID)
                return keyID
            },
            requestAttestation: { keyID, clientDataHash in
                try await appAttest.attestKey(keyID, clientDataHash: clientDataHash)
            },
            requestAssertion: { keyID, clientDataHash in
                try await appAttest.generateAssertion(
                    keyID,
                    clientDataHash: clientDataHash
                )
            },
            requestBiometric: {
                let context = LAContext()
                context.localizedCancelTitle = "Cancelar"
                var error: NSError?
                guard context.canEvaluatePolicy(
                    .deviceOwnerAuthenticationWithBiometrics,
                    error: &error
                ) else {
                    throw NativeIntegrationError.unavailable
                }
                let accepted = try await context.evaluatePolicy(
                    .deviceOwnerAuthenticationWithBiometrics,
                    localizedReason: "Confirme esta ação financeira no Jarvis."
                )
                guard accepted else { throw NativeIntegrationError.denied }
            },
            invalidateAppAttestKey: { accountID in
                NativeDeviceIdentity.clearAppAttestKeyID(accountID: accountID)
            },
            requestCalendarAccess: {
                try await eventStore.requestFullAccessToEvents()
            },
            hasFullCalendarAccess: {
                EKEventStore.authorizationStatus(for: .event) == .fullAccess
            },
            readCalendarEvents: {
                guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else {
                    throw NativeIntegrationError.denied
                }
                let window = Self.upcomingCalendarWindow(from: Date())
                let predicate = eventStore.predicateForEvents(
                    withStart: window.start,
                    end: window.end,
                    calendars: nil
                )
                return eventStore.events(matching: predicate)
                    .sorted { $0.startDate < $1.startDate }
                    .prefix(10)
                    .compactMap(Self.calendarReceiptForContext)
            },
            saveCalendarEvent: { accountID, proposalID, write in
                guard
                    EKEventStore.authorizationStatus(for: .event) == .fullAccess,
                    let calendar = eventStore.defaultCalendarForNewEvents
                else {
                    throw NativeIntegrationError.denied
                }
                let marker = Self.calendarMarkerURL(
                    accountID: accountID,
                    proposalID: proposalID
                )
                let predicate = eventStore.predicateForEvents(
                    withStart: write.startDate.addingTimeInterval(-1),
                    end: write.endDate.addingTimeInterval(1),
                    calendars: nil
                )
                if
                    let existing = eventStore.events(matching: predicate).first(
                        where: { $0.url == marker }
                    ),
                    let identifier = existing.eventIdentifier,
                    !identifier.isEmpty
                {
                    return Self.calendarReceipt(
                        existing,
                        write: write,
                        identifier: identifier
                    )
                }
                let event = EKEvent(eventStore: eventStore)
                event.calendar = calendar
                event.title = write.title
                event.startDate = write.startDate
                event.endDate = write.endDate
                event.isAllDay = write.isAllDay
                event.location = write.location.isEmpty ? nil : write.location
                event.notes = write.notes.isEmpty ? nil : write.notes
                event.url = marker
                try eventStore.save(event, span: .thisEvent, commit: true)
                guard let identifier = event.eventIdentifier, !identifier.isEmpty else {
                    throw NativeIntegrationError.failed
                }
                return Self.calendarReceipt(event, write: write, identifier: identifier)
            },
            requestHealthAccess: {
                guard HKHealthStore.isHealthDataAvailable() else {
                    throw NativeIntegrationError.unavailable
                }
                return try await withCheckedThrowingContinuation { continuation in
                    healthStore.requestAuthorization(
                        toShare: [],
                        read: Self.healthReadTypes()
                    ) { granted, error in
                        if let error {
                            continuation.resume(throwing: error)
                        } else {
                            continuation.resume(returning: granted)
                        }
                    }
                }
            },
            healthDataAvailable: {
                HKHealthStore.isHealthDataAvailable()
            },
            readHealthData: {
                guard HKHealthStore.isHealthDataAvailable() else {
                    throw NativeIntegrationError.unavailable
                }
                return try await Self.readHealthSamples(from: healthStore)
            },
            deviceID: { NativeDeviceIdentity.stableID() },
            deviceLabel: { UIDevice.current.model },
            send: send
        )
    }

    func handle(_ payload: [String: Any]) {
        guard
            let action = payload["action"] as? String,
            let requestID = payload["requestId"] as? String,
            !requestID.isEmpty
        else {
            return
        }

        Task { @MainActor [weak self] in
            guard let self else { return }
            switch action {
            case "identity":
                await sendIdentity(payload, requestID: requestID)
            case "attest":
                await sendAttestation(payload, requestID: requestID)
            case "assert":
                await sendAssertion(payload, requestID: requestID)
            case "requestPermission":
                if payload["provider"] as? String == "apple_health" {
                    await requestHealthPermission(payload, requestID: requestID)
                } else {
                    await requestCalendarPermission(payload, requestID: requestID)
                }
            case "readCalendarEvents":
                sendCalendarEvents(requestID: requestID)
            case "readHealthData":
                await sendHealthData(requestID: requestID)
            case "createCalendarEvent":
                createCalendarEvent(payload, requestID: requestID)
            case "clearCalendarReceipts":
                clearCalendarReceipts(payload, requestID: requestID)
            default:
                sendFailure(
                    type: "nativeError",
                    status: "unavailable",
                    requestID: requestID,
                    message: "Esta integração nativa ainda não está disponível."
                )
            }
        }
    }

    @MainActor
    private func sendIdentity(_ payload: [String: Any], requestID: String) async {
        guard let accountID = Self.accountID(payload["accountId"]) else {
            sendFailure(
                type: "deviceIdentity",
                status: "error",
                requestID: requestID,
                message: NativeIntegrationError.invalidPayload.localizedDescription
            )
            return
        }
        do {
            let keyID = try await requestKey(accountID)
            send([
                "type": "deviceIdentity",
                "status": "ready",
                "requestId": requestID,
                "deviceId": deviceID(),
                "deviceLabel": deviceLabel(),
                "keyId": keyID,
            ])
        } catch {
            sendFailure(
                type: "deviceIdentity",
                status: "error",
                requestID: requestID,
                message: "Não foi possível criar a identidade segura deste iPhone."
            )
        }
    }

    @MainActor
    private func sendAttestation(_ payload: [String: Any], requestID: String) async {
        guard
            let accountID = Self.accountID(payload["accountId"]),
            let keyID = Self.bounded(payload["keyId"], minimum: 32, maximum: 128),
            let challenge = Self.base64URLData(payload["challenge"], maximum: 64)
        else {
            sendFailure(
                type: "appAttestation",
                status: "error",
                requestID: requestID,
                message: NativeIntegrationError.invalidPayload.localizedDescription
            )
            return
        }
        do {
            let clientDataHash = Data(SHA256.hash(data: challenge))
            var attestedKey = keyID
            var rotated = false
            let result: Data
            do {
                result = try await requestAttestation(attestedKey, clientDataHash)
            } catch {
                if Self.isAppAttestError(error, .serverUnavailable) {
                    result = try await requestAttestation(attestedKey, clientDataHash)
                } else if Self.isAppAttestError(error, .invalidKey) {
                    invalidateAppAttestKey(accountID)
                    attestedKey = try await requestKey(accountID)
                    rotated = true
                    result = try await requestAttestation(attestedKey, clientDataHash)
                } else {
                    throw error
                }
            }
            send([
                "type": "appAttestation",
                "status": "ready",
                "requestId": requestID,
                "keyId": attestedKey,
                "rotated": rotated,
                "attestationObject": Self.base64URL(result),
            ])
        } catch {
            sendFailure(
                type: "appAttestation",
                status: "error",
                requestID: requestID,
                message: "A Apple não conseguiu certificar esta instalação do Jarvis."
            )
        }
    }

    @MainActor
    private func sendAssertion(_ payload: [String: Any], requestID: String) async {
        guard
            let accountID = Self.accountID(payload["accountId"]),
            let keyID = Self.bounded(payload["keyId"], minimum: 32, maximum: 128),
            let clientData = Self.base64URLData(payload["clientData"], maximum: 2_048)
        else {
            sendFailure(
                type: "appAssertion",
                status: "error",
                requestID: requestID,
                message: NativeIntegrationError.invalidPayload.localizedDescription
            )
            return
        }
        do {
            guard let context = Self.assertionContext(clientData) else {
                throw NativeIntegrationError.invalidPayload
            }
            if context.purpose == "health_sync" {
                guard pendingHealthSyncResourceID == context.resourceID else {
                    throw NativeIntegrationError.invalidPayload
                }
            }
            // JavaScript is not a trusted authority for local presence. A
            // finance assertion always requires Face ID even if a compromised
            // page explicitly sends requireBiometric=false.
            if
                context.purpose == "finance"
                || payload["requireBiometric"] as? Bool == true
            {
                try await requestBiometric()
            }
            let result = try await assertionWithNetworkRetry(
                keyID: keyID,
                clientDataHash: Data(SHA256.hash(data: clientData))
            )
            send([
                "type": "appAssertion",
                "status": "ready",
                "requestId": requestID,
                "keyId": keyID,
                "assertion": Self.base64URL(result),
            ])
            if context.purpose == "health_sync" {
                pendingHealthSyncResourceID = nil
            }
        } catch {
            if Self.isAppAttestError(error, .invalidKey) {
                invalidateAppAttestKey(accountID)
                sendFailure(
                    type: "appAssertion",
                    status: "error",
                    requestID: requestID,
                    code: "app_attest_key_invalid",
                    message: "A identidade segura deste iPhone precisa ser renovada."
                )
                return
            }
            sendFailure(
                type: "appAssertion",
                status: "denied",
                requestID: requestID,
                message: "A ação não foi autenticada pelo Face ID deste iPhone."
            )
        }
    }

    @MainActor
    func requestCalendarPermission(_ payload: [String: Any], requestID: String) async {
        let provider = payload["provider"] as? String ?? ""
        guard provider == "apple_calendar" else {
            sendFailure(
                type: "deviceGrant",
                status: "unavailable",
                requestID: requestID,
                provider: provider,
                message: "Esta integração nativa ainda não está disponível."
            )
            return
        }
        do {
            let granted = try await requestCalendarAccess()
            guard granted && hasFullCalendarAccess() else {
                throw NativeIntegrationError.denied
            }
            send([
                "type": "deviceGrant",
                "status": "granted",
                "provider": provider,
                "requestId": requestID,
                "grantedScopes": ["events.read", "events.write"],
                "deviceId": deviceID(),
                "deviceLabel": deviceLabel(),
            ])
        } catch {
            sendFailure(
                type: "deviceGrant",
                status: "denied",
                requestID: requestID,
                provider: provider,
                message: "Acesso ao Calendário negado. Libere em Ajustes para conectar."
            )
        }
    }

    @MainActor
    func requestHealthPermission(_ payload: [String: Any], requestID: String) async {
        let provider = payload["provider"] as? String ?? ""
        guard provider == "apple_health", healthDataAvailable() else {
            sendFailure(
                type: "deviceGrant",
                status: "unavailable",
                requestID: requestID,
                provider: provider,
                message: "O Apple Health não está disponível neste aparelho."
            )
            return
        }
        do {
            // HealthKit intentionally does not reveal which read types the
            // user denied. A successful result means the official consent
            // sheet completed; a later empty read still fails honestly as
            // zero imported samples rather than claiming data exists.
            guard try await requestHealthAccess() else {
                throw NativeIntegrationError.denied
            }
            send([
                "type": "deviceGrant",
                "status": "granted",
                "provider": provider,
                "requestId": requestID,
                "grantedScopes": Self.healthScopeNames,
                "deviceId": deviceID(),
                "deviceLabel": deviceLabel(),
            ])
        } catch {
            sendFailure(
                type: "deviceGrant",
                status: "denied",
                requestID: requestID,
                provider: provider,
                message: "Acesso ao Apple Health não concluído. Libere em Ajustes > Saúde."
            )
        }
    }

    @MainActor
    func sendHealthData(requestID: String) async {
        do {
            guard healthDataAvailable() else {
                throw NativeIntegrationError.unavailable
            }
            let samples = Array(try await readHealthData().prefix(128))
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
            let rawPayload = try encoder.encode(NativeHealthPayload(samples: samples))
            guard rawPayload.count <= 256_000 else {
                throw NativeIntegrationError.invalidPayload
            }
            let currentDeviceID = deviceID()
            let resourceID = Self.healthResourceID(
                deviceID: currentDeviceID,
                rawPayload: rawPayload
            )
            pendingHealthSyncResourceID = resourceID
            send([
                "type": "healthData",
                "status": "ready",
                "requestId": requestID,
                "deviceId": currentDeviceID,
                "payload": Self.base64URL(rawPayload),
                "resourceId": resourceID,
                "sampleCount": samples.count,
            ])
        } catch {
            pendingHealthSyncResourceID = nil
            sendFailure(
                type: "healthData",
                status: "error",
                requestID: requestID,
                message: "Não foi possível ler os dados autorizados no Apple Health."
            )
        }
    }

    @MainActor
    private func sendCalendarEvents(requestID: String) {
        do {
            guard hasFullCalendarAccess() else {
                throw NativeIntegrationError.denied
            }
            let events = try readCalendarEvents()
                .prefix(10)
                .map(\.contextDictionary)
            send([
                "type": "calendarEvents",
                "status": "ready",
                "requestId": requestID,
                "events": events,
            ])
        } catch {
            sendFailure(
                type: "calendarEvents",
                status: "error",
                requestID: requestID,
                message: "Não foi possível ler os próximos eventos do iPhone."
            )
        }
    }

    @MainActor
    private func createCalendarEvent(_ payload: [String: Any], requestID: String) {
        Task { @MainActor in
          do {
            guard
                let proposalID = Self.bounded(
                    payload["proposalId"],
                    minimum: 16,
                    maximum: 128
                ),
                let claimToken = Self.claimToken(payload["claimToken"]),
                let accountID = Self.accountID(payload["accountId"]),
                let keyID = Self.bounded(payload["keyId"], minimum: 32, maximum: 128),
                let eventPayload = payload["event"] as? [String: Any]
            else {
                throw NativeIntegrationError.invalidPayload
            }
            let write = try Self.calendarWrite(eventPayload)
            if
                calendarReceiptCache.containsReceipt(
                    for: proposalID,
                    accountID: accountID
                ),
                !calendarReceiptCache.hasReceipt(
                    for: proposalID,
                    accountID: accountID,
                    matching: write
                )
            {
                throw NativeIntegrationError.invalidPayload
            }
            let receipt = try saveCalendarEvent(accountID, proposalID, write)
            calendarReceiptCache.store(
                eventIdentifier: receipt.id,
                for: proposalID,
                accountID: accountID,
                write: write
            )
            let resultData = Self.nativeResultData(
                claimToken: claimToken,
                proposalID: proposalID,
                deviceID: deviceID(),
                event: receipt
            )
            let assertion = try await assertionWithNetworkRetry(
                keyID: keyID,
                clientDataHash: Data(SHA256.hash(data: resultData))
            )
            send([
                "type": "calendarEvent",
                "status": "saved",
                "requestId": requestID,
                "event": receipt.dictionary,
                "keyId": keyID,
                "assertion": Self.base64URL(assertion),
            ])
          } catch {
            if
                let accountID = Self.accountID(payload["accountId"]),
                Self.isAppAttestError(error, .invalidKey)
            {
                invalidateAppAttestKey(accountID)
                sendFailure(
                    type: "calendarEvent",
                    status: "error",
                    requestID: requestID,
                    code: "app_attest_key_invalid",
                    message: "A identidade segura deste iPhone precisa ser renovada."
                )
                return
            }
            sendFailure(
                type: "calendarEvent",
                status: "error",
                requestID: requestID,
                message: "Não foi possível salvar o evento no Calendário do iPhone."
            )
          }
        }
    }

    @MainActor
    private func clearCalendarReceipts(_ payload: [String: Any], requestID: String) {
        guard let accountID = Self.accountID(payload["accountId"]) else {
            sendFailure(
                type: "calendarReceiptsCleared",
                status: "error",
                requestID: requestID,
                message: NativeIntegrationError.invalidPayload.localizedDescription
            )
            return
        }
        if payload.keys.contains("proposalId") {
            guard
                let proposalID = Self.bounded(
                    payload["proposalId"],
                    minimum: 16,
                    maximum: 128
                )
            else {
                sendFailure(
                    type: "calendarReceiptsCleared",
                    status: "error",
                    requestID: requestID,
                    message: NativeIntegrationError.invalidPayload.localizedDescription
                )
                return
            }
            calendarReceiptCache.remove(for: proposalID, accountID: accountID)
        } else {
            calendarReceiptCache.removeAll(accountID: accountID)
        }
        send([
            "type": "calendarReceiptsCleared",
            "status": "ready",
            "requestId": requestID,
        ])
    }

    private func sendFailure(
        type: String,
        status: String,
        requestID: String,
        provider: String = "",
        code: String = "",
        message: String
    ) {
        var event: [String: Any] = [
            "type": type,
            "status": status,
            "requestId": requestID,
            "message": message,
        ]
        if !provider.isEmpty { event["provider"] = provider }
        if !code.isEmpty { event["code"] = code }
        send(event)
    }

    private func assertionWithNetworkRetry(
        keyID: String,
        clientDataHash: Data
    ) async throws -> Data {
        do {
            return try await requestAssertion(keyID, clientDataHash)
        } catch {
            guard Self.isAppAttestError(error, .serverUnavailable) else {
                throw error
            }
            return try await requestAssertion(keyID, clientDataHash)
        }
    }

    private static func generateAppAttestKey(
        _ service: DCAppAttestService
    ) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            service.generateKey { keyID, error in
                if let keyID, error == nil {
                    continuation.resume(returning: keyID)
                } else {
                    continuation.resume(
                        throwing: error ?? NativeIntegrationError.failed
                    )
                }
            }
        }
    }

    private static func isAppAttestError(
        _ error: Error,
        _ code: DCError.Code
    ) -> Bool {
        let native = error as NSError
        return native.domain == DCErrorDomain && native.code == code.rawValue
    }

    static func upcomingCalendarWindow(
        from start: Date,
        calendar: Calendar = .current
    ) -> (start: Date, end: Date) {
        let end = calendar.date(byAdding: .day, value: 31, to: start)
            ?? start.addingTimeInterval(31 * 24 * 60 * 60)
        return (start, end)
    }

    static let healthScopeNames = [
        "steps.read",
        "sleep.read",
        "heart_rate.read",
        "resting_heart_rate.read",
        "active_energy.read",
        "workouts.read",
    ]

    private static func healthReadTypes() -> Set<HKObjectType> {
        var types = Set<HKObjectType>()
        [
            HKQuantityTypeIdentifier.stepCount,
            .heartRate,
            .restingHeartRate,
            .activeEnergyBurned,
        ].compactMap { HKObjectType.quantityType(forIdentifier: $0) }
            .forEach { types.insert($0) }
        if let sleep = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) {
            types.insert(sleep)
        }
        types.insert(HKObjectType.workoutType())
        return types
    }

    private static func readHealthSamples(
        from store: HKHealthStore,
        now: Date = Date(),
        calendar: Calendar = .current
    ) async throws -> [NativeHealthSample] {
        guard
            let stepsType = HKObjectType.quantityType(forIdentifier: .stepCount),
            let heartType = HKObjectType.quantityType(forIdentifier: .heartRate),
            let restingType = HKObjectType.quantityType(
                forIdentifier: .restingHeartRate
            ),
            let energyType = HKObjectType.quantityType(
                forIdentifier: .activeEnergyBurned
            ),
            let sleepType = HKObjectType.categoryType(forIdentifier: .sleepAnalysis)
        else {
            throw NativeIntegrationError.unavailable
        }
        let start = calendar.date(byAdding: .day, value: -30, to: now)
            ?? now.addingTimeInterval(-30 * 24 * 60 * 60)
        async let steps = dailyQuantitySamples(
            store: store,
            type: stepsType,
            unit: .count(),
            kind: "steps",
            outputUnit: "count",
            start: start,
            end: now,
            calendar: calendar
        )
        async let energy = dailyQuantitySamples(
            store: store,
            type: energyType,
            unit: .kilocalorie(),
            kind: "active_energy",
            outputUnit: "kcal",
            start: start,
            end: now,
            calendar: calendar
        )
        async let sleep = dailySleepSamples(
            store: store,
            type: sleepType,
            start: start,
            end: now,
            calendar: calendar
        )
        async let heart = latestQuantitySample(
            store: store,
            type: heartType,
            unit: .count().unitDivided(by: .minute()),
            kind: "heart_rate",
            outputUnit: "bpm",
            start: start,
            end: now
        )
        async let resting = latestQuantitySample(
            store: store,
            type: restingType,
            unit: .count().unitDivided(by: .minute()),
            kind: "resting_heart_rate",
            outputUnit: "bpm",
            start: start,
            end: now
        )
        async let workouts = dailyWorkoutSamples(
            store: store,
            start: start,
            end: now,
            calendar: calendar
        )
        let result = try await (
            steps + energy + sleep + heart + resting + workouts
        )
        return result.sorted { $0.observedAt > $1.observedAt }
    }

    private static func dailyQuantitySamples(
        store: HKHealthStore,
        type: HKQuantityType,
        unit: HKUnit,
        kind: String,
        outputUnit: String,
        start: Date,
        end: Date,
        calendar: Calendar
    ) async throws -> [NativeHealthSample] {
        let predicate = HKQuery.predicateForSamples(
            withStart: start,
            end: end,
            options: .strictStartDate
        )
        let anchor = calendar.startOfDay(for: start)
        let statistics = try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<[HKStatistics], Error>) in
            let query = HKStatisticsCollectionQuery(
                quantityType: type,
                quantitySamplePredicate: predicate,
                options: .cumulativeSum,
                anchorDate: anchor,
                intervalComponents: DateComponents(day: 1)
            )
            query.initialResultsHandler = { _, collection, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                var values: [HKStatistics] = []
                collection?.enumerateStatistics(from: start, to: end) {
                    statistic, _ in values.append(statistic)
                }
                continuation.resume(returning: values)
            }
            store.execute(query)
        }
        return statistics.compactMap { statistic in
            guard let sum = statistic.sumQuantity() else { return nil }
            let value = rounded(sum.doubleValue(for: unit), places: 3)
            guard value > 0 else { return nil }
            return NativeHealthSample(
                sampleID: "\(kind):\(dayKey(statistic.startDate, calendar: calendar))",
                kind: kind,
                value: value,
                unit: outputUnit,
                observedAt: iso8601(min(statistic.endDate, end))
            )
        }
    }

    private static func dailySleepSamples(
        store: HKHealthStore,
        type: HKCategoryType,
        start: Date,
        end: Date,
        calendar: Calendar
    ) async throws -> [NativeHealthSample] {
        let samples = try await fetchSamples(
            store: store,
            type: type,
            start: start,
            end: end,
            limit: 500
        )
        var totals: [String: Double] = [:]
        var latest: [String: Date] = [:]
        for case let sample as HKCategorySample in samples {
            if
                sample.value == HKCategoryValueSleepAnalysis.inBed.rawValue
                || sample.value == HKCategoryValueSleepAnalysis.awake.rawValue
            {
                continue
            }
            let day = dayKey(sample.startDate, calendar: calendar)
            totals[day, default: 0] += sample.endDate.timeIntervalSince(
                sample.startDate
            ) / 3_600
            latest[day] = max(latest[day] ?? sample.endDate, sample.endDate)
        }
        return totals.compactMap { day, total in
            guard total > 0, let observedAt = latest[day] else { return nil }
            return NativeHealthSample(
                sampleID: "sleep_hours:\(day)",
                kind: "sleep_hours",
                value: rounded(min(total, 24), places: 3),
                unit: "h",
                observedAt: iso8601(observedAt)
            )
        }
    }

    private static func latestQuantitySample(
        store: HKHealthStore,
        type: HKQuantityType,
        unit: HKUnit,
        kind: String,
        outputUnit: String,
        start: Date,
        end: Date
    ) async throws -> [NativeHealthSample] {
        let samples = try await fetchSamples(
            store: store,
            type: type,
            start: start,
            end: end,
            limit: 1
        )
        guard let sample = samples.first as? HKQuantitySample else { return [] }
        return [
            NativeHealthSample(
                sampleID: "\(kind):\(sample.uuid.uuidString.lowercased())",
                kind: kind,
                value: rounded(sample.quantity.doubleValue(for: unit), places: 3),
                unit: outputUnit,
                observedAt: iso8601(sample.endDate)
            )
        ]
    }

    private static func dailyWorkoutSamples(
        store: HKHealthStore,
        start: Date,
        end: Date,
        calendar: Calendar
    ) async throws -> [NativeHealthSample] {
        let samples = try await fetchSamples(
            store: store,
            type: HKObjectType.workoutType(),
            start: start,
            end: end,
            limit: 500
        )
        var totals: [String: Double] = [:]
        var latest: [String: Date] = [:]
        for case let workout as HKWorkout in samples {
            let day = dayKey(workout.startDate, calendar: calendar)
            totals[day, default: 0] += workout.duration / 60
            latest[day] = max(latest[day] ?? workout.endDate, workout.endDate)
        }
        return totals.compactMap { day, total in
            guard total > 0, let observedAt = latest[day] else { return nil }
            return NativeHealthSample(
                sampleID: "workout_minutes:\(day)",
                kind: "workout_minutes",
                value: rounded(min(total, 1_440), places: 3),
                unit: "min",
                observedAt: iso8601(observedAt)
            )
        }
    }

    private static func fetchSamples(
        store: HKHealthStore,
        type: HKSampleType,
        start: Date,
        end: Date,
        limit: Int
    ) async throws -> [HKSample] {
        let predicate = HKQuery.predicateForSamples(
            withStart: start,
            end: end,
            options: .strictStartDate
        )
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKSampleQuery(
                sampleType: type,
                predicate: predicate,
                limit: limit,
                sortDescriptors: [
                    NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: false)
                ]
            ) { _, samples, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: samples ?? [])
                }
            }
            store.execute(query)
        }
    }

    private static func dayKey(_ date: Date, calendar: Calendar) -> String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    private static func iso8601(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    private static func rounded(_ value: Double, places: Int) -> Double {
        let scale = pow(10, Double(places))
        return (value * scale).rounded() / scale
    }

    private static func healthResourceID(
        deviceID: String,
        rawPayload: Data
    ) -> String {
        var sealed = Data("health-sync-v1\0".utf8)
        sealed.append(Data(deviceID.utf8))
        sealed.append(0)
        sealed.append(rawPayload)
        let digest = SHA256.hash(data: sealed)
            .map { String(format: "%02x", $0) }
            .joined()
        return "health:\(digest)"
    }

    private static func calendarMarkerURL(accountID: String, proposalID: String) -> URL {
        let digest = SHA256.hash(data: Data("\(accountID):\(proposalID)".utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return URL(string: "jarvis-life://calendar/proposal/\(digest)")!
    }

    private static func calendarReceipt(
        _ event: EKEvent,
        write: NativeCalendarWrite,
        identifier: String
    ) -> NativeCalendarReceipt {
        NativeCalendarReceipt(
            id: identifier,
            title: event.title,
            startAt: write.startAt,
            endAt: write.endAt,
            isAllDay: event.isAllDay,
            location: event.location ?? "",
            calendarTitle: event.calendar.title
        )
    }

    private static func calendarReceiptForContext(
        _ event: EKEvent
    ) -> NativeCalendarReceipt? {
        guard
            let identifier = event.eventIdentifier,
            !identifier.isEmpty,
            let title = event.title?.trimmingCharacters(in: .whitespacesAndNewlines),
            !title.isEmpty
        else {
            return nil
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return NativeCalendarReceipt(
            id: identifier,
            title: String(title.prefix(160)),
            startAt: formatter.string(from: event.startDate),
            endAt: formatter.string(from: event.endDate),
            isAllDay: event.isAllDay,
            location: String((event.location ?? "").prefix(200)),
            calendarTitle: String(event.calendar.title.prefix(160))
        )
    }

    private static func accountID(_ value: Any?) -> String? {
        guard let candidate = bounded(value, minimum: 32, maximum: 32) else {
            return nil
        }
        let allowed = CharacterSet(charactersIn: "0123456789abcdef")
        return candidate.unicodeScalars.allSatisfy(allowed.contains) ? candidate : nil
    }

    private static func bounded(
        _ value: Any?,
        minimum: Int,
        maximum: Int
    ) -> String? {
        guard let raw = value as? String else { return nil }
        let candidate = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard candidate.count >= minimum, candidate.count <= maximum else { return nil }
        return candidate
    }

    private static func base64URLData(_ value: Any?, maximum: Int) -> Data? {
        guard let candidate = bounded(value, minimum: 16, maximum: maximum * 2) else {
            return nil
        }
        var base64 = candidate
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        guard let result = Data(base64Encoded: base64), result.count <= maximum else {
            return nil
        }
        return result
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    static func base64URLDataForTesting(_ value: String) -> Data? {
        base64URLData(value, maximum: 256_000)
    }

    static func assertionPurpose(_ clientData: Data) -> String? {
        assertionContext(clientData)?.purpose
    }

    private static func assertionContext(
        _ clientData: Data
    ) -> (purpose: String, resourceID: String)? {
        guard
            let object = try? JSONSerialization.jsonObject(with: clientData),
            let body = object as? [String: Any],
            let purpose = body["purpose"] as? String,
            ["device_grant", "native_action", "finance", "health_sync"].contains(
                purpose
            ),
            let resourceID = body["resource_id"] as? String,
            resourceID.count <= 128
        else {
            return nil
        }
        return (purpose, resourceID)
    }

    private static func claimToken(_ value: Any?) -> String? {
        guard let token = bounded(value, minimum: 32, maximum: 128) else {
            return nil
        }
        let allowed = CharacterSet(
            charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        )
        return token.unicodeScalars.allSatisfy(allowed.contains) ? token : nil
    }

    private static func calendarWrite(_ payload: [String: Any]) throws -> NativeCalendarWrite {
        guard
            let title = bounded(payload["title"], minimum: 1, maximum: 160),
            let startAt = bounded(payload["start_at"], minimum: 1, maximum: 64),
            let endAt = bounded(payload["end_at"], minimum: 1, maximum: 64),
            let startDate = parseISO8601(startAt),
            let endDate = parseISO8601(endAt),
            endDate > startDate
        else {
            throw NativeIntegrationError.invalidPayload
        }
        let location = bounded(payload["location"], minimum: 1, maximum: 200) ?? ""
        let notes = bounded(payload["notes"], minimum: 1, maximum: 1_000) ?? ""
        return NativeCalendarWrite(
            title: title,
            startAt: startAt,
            endAt: endAt,
            startDate: startDate,
            endDate: endDate,
            isAllDay: payload["is_all_day"] as? Bool ?? false,
            location: location,
            notes: notes
        )
    }

    private static func parseISO8601(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = fractional.date(from: value) { return parsed }
        let standard = ISO8601DateFormatter()
        standard.formatOptions = [.withInternetDateTime]
        return standard.date(from: value)
    }

    static func nativeResultData(
        claimToken: String,
        proposalID: String,
        deviceID: String,
        event: NativeCalendarReceipt
    ) -> Data {
        let values = [
            "jarvis-native-result-v1",
            claimToken,
            proposalID,
            deviceID,
            event.id,
            event.title,
            event.startAt,
            event.endAt,
            event.isAllDay ? "1" : "0",
            event.location,
            event.calendarTitle,
        ]
        var result = Data()
        for value in values {
            let bytes = Data(value.utf8)
            result.append(Data("\(bytes.count):".utf8))
            result.append(bytes)
        }
        return result
    }

}
