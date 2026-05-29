import Foundation
import WebKit

class WebViewBridge: NSObject, WKScriptMessageHandler {
    var settings: AppSettings
    weak var webView: WKWebView?
    weak var commandCenter: WebViewCommandCenter?
    private let tabClientId: String
    private lazy var remoteClient = RemoteAPIClient()
    private lazy var localEngine = BookingEngine()

    init(settings: AppSettings, commandCenter: WebViewCommandCenter) {
        self.settings = settings
        self.commandCenter = commandCenter
        self.tabClientId = UUID().uuidString.lowercased()
        super.init()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let path = body["path"] as? String,
              let method = body["method"] as? String else {
            if let body = message.body as? [String: Any],
               body["event"] as? String == "status" {
                let running = body["running"] as? Bool ?? false
                let waiting = body["waiting_for_schedule"] as? Bool ?? false
                commandCenter?.updateRunning(running || waiting)
            }
            return
        }
        guard let id = body["id"] as? Int else { return }

        let requestBody = body["body"] as? String

        Task {
            do {
                let result = try await handleRequest(path: path, method: method, body: requestBody)
                resolveJS(id: id, data: result)
            } catch {
                rejectJS(id: id, error: error.localizedDescription)
            }
        }
    }

    private func handleRequest(path: String, method: String, body: String?) async throws -> Any {
        switch settings.mode {
        case .remote:
            return try await remoteClient.forward(
                baseURL: settings.serverURL,
                path: path,
                method: method,
                body: body,
                clientId: tabClientId
            )
        case .local:
            return try await localEngine.handle(path: path, method: method, body: body, clientId: tabClientId)
        }
    }

    private func resolveJS(id: Int, data: Any) {
        guard let webView else { return }
        let jsonData: Data
        if let dict = data as? [String: Any] {
            jsonData = (try? JSONSerialization.data(withJSONObject: dict)) ?? Data("{}".utf8)
        } else if let array = data as? [Any] {
            jsonData = (try? JSONSerialization.data(withJSONObject: array)) ?? Data("[]".utf8)
        } else {
            jsonData = Data("{}".utf8)
        }
        let jsonString = String(data: jsonData, encoding: .utf8) ?? "{}"
        let js = "window._nativeBridge.resolve(\(id), \(jsonString));"
        DispatchQueue.main.async {
            webView.evaluateJavaScript(js)
        }
    }

    private func rejectJS(id: Int, error: String) {
        guard let webView else { return }
        let escaped = error.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
        let js = "window._nativeBridge.reject(\(id), \"\(escaped)\");"
        DispatchQueue.main.async {
            webView.evaluateJavaScript(js)
        }
    }
}
