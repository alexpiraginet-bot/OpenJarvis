import SwiftUI
import UIKit
import WebKit

struct JarvisWebView: UIViewRepresentable {
    let appURL: URL
    let reloadID: Int
    @Binding var loadError: String?

    func makeCoordinator() -> Coordinator {
        Coordinator(appURL: appURL, loadError: $loadError)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.websiteDataStore = .default()
        configuration.userContentController.add(context.coordinator, name: "jarvisVoice")
        configuration.userContentController.add(
            context.coordinator,
            name: "jarvisIntegrations"
        )

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.scrollView.bounces = false
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.backgroundColor = .black

        context.coordinator.attach(webView)
        context.coordinator.load(reloadID: reloadID)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.load(reloadID: reloadID)
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(
            forName: "jarvisVoice"
        )
        webView.configuration.userContentController.removeScriptMessageHandler(
            forName: "jarvisIntegrations"
        )
        coordinator.stopVoice()
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        private let appURL: URL
        private var loadError: Binding<String?>
        private weak var webView: WKWebView?
        private var lastReloadID: Int?
        private lazy var voice = NativeSpeechController { [weak self] event in
            self?.send(event: event, browserEvent: "jarvis-native-voice")
        }
        private lazy var integrations = NativeIntegrationController { [weak self] event in
            self?.send(event: event, browserEvent: "jarvis-native-integration")
        }

        init(appURL: URL, loadError: Binding<String?>) {
            self.appURL = appURL
            self.loadError = loadError
        }

        func attach(_ webView: WKWebView) {
            self.webView = webView
        }

        func load(reloadID: Int) {
            guard lastReloadID != reloadID, let webView else { return }
            lastReloadID = reloadID
            let request = URLRequest(
                url: appURL,
                cachePolicy: .reloadRevalidatingCacheData,
                timeoutInterval: 20
            )
            webView.load(request)
        }

        func stopVoice() {
            voice.stop()
        }

        func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard
                message.frameInfo.isMainFrame,
                isTrustedOrigin(message.frameInfo.securityOrigin),
                let payload = message.body as? [String: Any],
                let action = payload["action"] as? String
            else {
                return
            }

            switch message.name {
            case "jarvisVoice":
                switch action {
                case "start":
                    voice.start()
                case "stop":
                    voice.stop()
                case "speak":
                    guard let text = payload["text"] as? String else { return }
                    voice.speak(text)
                default:
                    break
                }
            case "jarvisIntegrations":
                integrations.handle(payload)
            default:
                return
            }
        }

        private func isTrustedOrigin(_ origin: WKSecurityOrigin) -> Bool {
            guard
                let scheme = appURL.scheme?.lowercased(),
                let host = appURL.host?.lowercased(),
                origin.protocol.lowercased() == scheme,
                origin.host.lowercased() == host
            else {
                return false
            }
            let expectedPort = appURL.port ?? (scheme == "https" ? 443 : 80)
            return origin.port == expectedPort
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            loadError.wrappedValue = nil
        }

        func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation!,
            withError error: Error
        ) {
            show(error)
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            show(error)
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard
                navigationAction.targetFrame?.isMainFrame == true,
                let destination = navigationAction.request.url,
                !isTrustedNavigation(destination)
            else {
                decisionHandler(.allow)
                return
            }

            if destination.scheme == "https" || destination.scheme == "http" {
                UIApplication.shared.open(destination)
            }
            decisionHandler(.cancel)
        }

        private func isTrustedNavigation(_ destination: URL) -> Bool {
            destination.scheme?.lowercased() == appURL.scheme?.lowercased()
                && destination.host?.lowercased() == appURL.host?.lowercased()
                && (destination.port ?? defaultPort(destination.scheme))
                    == (appURL.port ?? defaultPort(appURL.scheme))
        }

        private func defaultPort(_ scheme: String?) -> Int {
            scheme?.lowercased() == "https" ? 443 : 80
        }

        private func show(_ error: Error) {
            let nsError = error as NSError
            guard nsError.code != NSURLErrorCancelled else { return }
            loadError.wrappedValue = "Não consegui conectar ao núcleo do Jarvis. Verifique a rede e tente novamente."
        }

        private func send(event: [String: Any], browserEvent: String) {
            guard
                JSONSerialization.isValidJSONObject(event),
                let data = try? JSONSerialization.data(withJSONObject: event),
                let payload = String(data: data, encoding: .utf8)
            else {
                return
            }

            let script = "window.dispatchEvent(new CustomEvent('\(browserEvent)',{detail:\(payload)}));"
            webView?.evaluateJavaScript(script)
        }
    }
}
