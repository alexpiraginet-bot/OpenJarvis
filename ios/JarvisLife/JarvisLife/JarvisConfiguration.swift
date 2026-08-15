import Foundation

enum JarvisConfiguration {
    static func appURL(in bundle: Bundle = .main) -> URL? {
        guard let rawValue = bundle.object(forInfoDictionaryKey: "JarvisAppURL") as? String else {
            return nil
        }
        return validatedAppURL(rawValue)
    }

    static func validatedAppURL(_ rawValue: String) -> URL? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            let url = URL(string: value),
            let scheme = url.scheme?.lowercased(),
            scheme == "https" || scheme == "http",
            url.host != nil
        else {
            return nil
        }
        return url
    }
}
