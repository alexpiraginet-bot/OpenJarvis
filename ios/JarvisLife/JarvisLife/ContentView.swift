import SwiftUI

struct ContentView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var loadError: String?
    @State private var reloadID = 0
    @StateObject private var biometricLock: BiometricLock

    init(biometricLock: BiometricLock? = nil) {
#if targetEnvironment(simulator)
        _biometricLock = StateObject(
            wrappedValue: biometricLock ?? BiometricLock(initiallyUnlocked: true)
        )
#else
        _biometricLock = StateObject(
            wrappedValue: biometricLock ?? BiometricLock()
        )
#endif
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if let appURL = JarvisConfiguration.appURL() {
                JarvisWebView(
                    appURL: appURL,
                    reloadID: reloadID,
                    loadError: $loadError
                )
                .ignoresSafeArea()
                .allowsHitTesting(
                    biometricLock.isUnlocked && !biometricLock.isContentObscured
                )
                .accessibilityHidden(
                    !biometricLock.isUnlocked || biometricLock.isContentObscured
                )
            } else {
                FailureView(
                    title: "Configuração incompleta",
                    message: "O endereço seguro do Jarvis não foi configurado neste build.",
                    retry: nil
                )
            }

            if let loadError {
                FailureView(
                    title: "Jarvis indisponível",
                    message: loadError,
                    retry: {
                        self.loadError = nil
                        reloadID += 1
                    }
                )
            }

            if biometricLock.isContentObscured {
                PrivacyShieldView()
                    .zIndex(9)
            }

            if !biometricLock.isUnlocked {
                BiometricLockView(lock: biometricLock)
                    .transition(.opacity)
                    .zIndex(10)
            }
        }
        .animation(.easeOut(duration: 0.2), value: biometricLock.isUnlocked)
        .onAppear {
            biometricLock.unlock()
        }
        .onChange(of: scenePhase) { _, phase in
            biometricLock.scenePhaseDidChange(phase)
        }
    }
}

private struct PrivacyShieldView: View {
    var body: some View {
        ZStack {
            Color(red: 0.015, green: 0.025, blue: 0.045)
                .ignoresSafeArea()
            Image(systemName: "lock.shield.fill")
                .font(.system(size: 32, weight: .medium))
                .foregroundStyle(Color.cyan)
                .accessibilityLabel("Conteúdo do Jarvis protegido")
        }
    }
}

private struct BiometricLockView: View {
    @ObservedObject var lock: BiometricLock

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("JARVIS LIFE")
                        .font(.system(size: 12, weight: .bold, design: .monospaced))
                        .tracking(2.4)
                        .foregroundStyle(Color.cyan)
                    Text("SISTEMA PROTEGIDO")
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .tracking(1.4)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "lock.shield.fill")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(Color.cyan)
                    .accessibilityHidden(true)
            }

            Spacer()

            ZStack {
                Circle()
                    .stroke(Color.cyan.opacity(0.16), lineWidth: 1)
                    .frame(width: 176, height: 176)
                Circle()
                    .stroke(Color.cyan.opacity(0.36), lineWidth: 1)
                    .frame(width: 136, height: 136)
                Image(systemName: "faceid")
                    .font(.system(size: 66, weight: .ultraLight))
                    .foregroundStyle(Color.cyan)
                    .symbolEffect(.pulse, options: .repeating, isActive: lock.isAuthenticating)
                    .accessibilityHidden(true)
            }

            Text("Identidade necessária")
                .font(.system(size: 24, weight: .semibold, design: .rounded))
                .padding(.top, 28)

            Text(lock.message)
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 310)
                .padding(.top, 10)

            Button(action: lock.unlock) {
                HStack(spacing: 10) {
                    if lock.isAuthenticating {
                        ProgressView()
                            .tint(.black)
                    } else {
                        Image(systemName: "faceid")
                    }
                    Text(lock.isAuthenticating ? "AUTENTICANDO" : "DESBLOQUEAR")
                        .font(.system(size: 13, weight: .bold, design: .monospaced))
                        .tracking(1.2)
                }
                .frame(maxWidth: .infinity)
                .frame(height: 54)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.black)
            .background(Color.cyan, in: RoundedRectangle(cornerRadius: 16))
            .disabled(lock.isAuthenticating)
            .padding(.top, 30)
            .accessibilityLabel("Desbloquear Jarvis com Face ID")

            Spacer()

            HStack(spacing: 8) {
                Circle()
                    .fill(Color.green)
                    .frame(width: 6, height: 6)
                Text("DADOS CRIPTOGRAFADOS EM TRÂNSITO")
                    .font(.system(size: 9, weight: .medium, design: .monospaced))
                    .tracking(1)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 28)
        .padding(.top, 24)
        .padding(.bottom, 18)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.015, green: 0.025, blue: 0.045))
    }
}

private struct FailureView: View {
    let title: String
    let message: String
    let retry: (() -> Void)?

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "wave.3.right.circle.fill")
                .font(.system(size: 52, weight: .light))
                .foregroundStyle(Color.cyan)
                .accessibilityHidden(true)

            Text(title)
                .font(.title2.weight(.semibold))

            Text(message)
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)

            if let retry {
                Button("Tentar novamente", action: retry)
                    .buttonStyle(.borderedProminent)
                    .tint(.cyan)
            }
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.01, green: 0.02, blue: 0.05))
    }
}
