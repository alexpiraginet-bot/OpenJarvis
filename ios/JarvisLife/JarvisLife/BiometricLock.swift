import Combine
import Foundation
import LocalAuthentication
import SwiftUI

protocol DeviceAuthenticating {
    func authenticate(
        reason: String,
        completion: @escaping (Result<Void, Error>) -> Void
    )
}

struct LocalDeviceAuthenticator: DeviceAuthenticating {
    func authenticate(
        reason: String,
        completion: @escaping (Result<Void, Error>) -> Void
    ) {
        let context = LAContext()
        context.localizedCancelTitle = "Cancelar"
        context.localizedFallbackTitle = "Usar código"

        var policyError: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &policyError) else {
            completion(
                .failure(
                    policyError
                        ?? NSError(
                            domain: LAError.errorDomain,
                            code: LAError.authenticationFailed.rawValue
                        )
                )
            )
            return
        }

        context.evaluatePolicy(
            .deviceOwnerAuthentication,
            localizedReason: reason
        ) { success, error in
            if success {
                completion(.success(()))
            } else {
                completion(
                    .failure(
                        error
                            ?? NSError(
                                domain: LAError.errorDomain,
                                code: LAError.authenticationFailed.rawValue
                            )
                    )
                )
            }
        }
    }
}

@MainActor
final class BiometricLock: ObservableObject {
    @Published private(set) var isUnlocked: Bool
    @Published private(set) var isAuthenticating = false
    @Published private(set) var isContentObscured = false
    @Published private(set) var message = "Confirme sua identidade para acessar sua vida."

    private let authenticator: DeviceAuthenticating
    private let backgroundGracePeriod: TimeInterval
    private let clock: () -> TimeInterval
    private var backgroundedAt: TimeInterval?

    init(
        initiallyUnlocked: Bool = false,
        authenticator: DeviceAuthenticating = LocalDeviceAuthenticator(),
        backgroundGracePeriod: TimeInterval = 5 * 60,
        clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    ) {
        isUnlocked = initiallyUnlocked
        self.authenticator = authenticator
        self.backgroundGracePeriod = max(0, backgroundGracePeriod)
        self.clock = clock
    }

    func scenePhaseDidChange(_ phase: ScenePhase) {
        switch phase {
        case .active:
            let returnedAfterGracePeriod = backgroundedAt.map {
                clock() - $0 >= backgroundGracePeriod
            } ?? false
            backgroundedAt = nil
            isContentObscured = false

            if returnedAfterGracePeriod {
                lock()
            }
            unlock()
        case .inactive:
            isContentObscured = true
        case .background:
            isContentObscured = true
            if backgroundedAt == nil {
                backgroundedAt = clock()
            }
        @unknown default:
            lock()
        }
    }

    func lock() {
        isUnlocked = false
        message = "Jarvis protegido. Desbloqueie para continuar."
    }

    func unlock() {
        guard !isUnlocked, !isAuthenticating else { return }
        isAuthenticating = true
        message = "Autenticando com Face ID…"

        authenticator.authenticate(
            reason: "Desbloqueie o Jarvis para acessar seus dados pessoais."
        ) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                self.isAuthenticating = false
                switch result {
                case .success:
                    self.isUnlocked = true
                    self.message = "Identidade confirmada."
                case let .failure(error):
                    self.isUnlocked = false
                    self.message = Self.message(for: error)
                }
            }
        }
    }

    private static func message(for error: Error) -> String {
        let nsError = error as NSError
        guard nsError.domain == LAError.errorDomain else {
            return "Não foi possível autenticar. Toque para tentar novamente."
        }

        switch LAError.Code(rawValue: nsError.code) {
        case .biometryNotEnrolled:
            return "Configure o Face ID nos Ajustes do iPhone para proteger o Jarvis."
        case .biometryLockout:
            return "Face ID bloqueado. Use o código do iPhone para continuar."
        case .passcodeNotSet:
            return "Configure um código no iPhone para proteger o Jarvis."
        case .userCancel, .appCancel, .systemCancel:
            return "Jarvis continua bloqueado. Toque para desbloquear."
        default:
            return "Face ID não confirmou sua identidade. Tente novamente."
        }
    }
}
