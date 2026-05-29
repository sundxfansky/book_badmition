import SwiftUI
import WebKit

final class WebViewCommandCenter: ObservableObject {
    @Published var isRunning = false
    weak var webView: WKWebView?

    func updateRunning(_ running: Bool) {
        DispatchQueue.main.async {
            self.isRunning = running
        }
    }

    func toggleRunState() {
        isRunning ? stop() : start()
    }

    func start() {
        evaluate("window.bmintonNativeCommands && window.bmintonNativeCommands.start()")
    }

    func stop() {
        evaluate("window.bmintonNativeCommands && window.bmintonNativeCommands.stop()")
    }

    func nextTab() {
        evaluate("window.bmintonNativeCommands && window.bmintonNativeCommands.nextTab()")
    }

    func previousTab() {
        evaluate("window.bmintonNativeCommands && window.bmintonNativeCommands.previousTab()")
    }

    private func evaluate(_ script: String) {
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript(script)
        }
    }
}

struct BookingWebView: UIViewRepresentable {
    @EnvironmentObject var settings: AppSettings
    @ObservedObject var commandCenter: WebViewCommandCenter

    func makeCoordinator() -> WebViewBridge {
        WebViewBridge(settings: settings, commandCenter: commandCenter)
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let contentController = config.userContentController

        contentController.add(context.coordinator, name: "nativeBridge")

        let bridgeScript = WKUserScript(
            source: Self.bridgeInjectionJS,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        contentController.addUserScript(bridgeScript)

        let disableZoomScript = WKUserScript(
            source: "document.addEventListener('gesturestart', function(e) { e.preventDefault(); });",
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
        contentController.addUserScript(disableZoomScript)

        config.preferences.javaScriptCanOpenWindowsAutomatically = false

        let webView = WKWebView(frame: .zero, configuration: config)
        if #available(iOS 16.4, *) {
            webView.isInspectable = true
        }
        webView.scrollView.maximumZoomScale = 1.0
        webView.scrollView.minimumZoomScale = 1.0
        context.coordinator.webView = webView
        commandCenter.webView = webView

        loadLocalHTML(webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.settings = settings
        context.coordinator.commandCenter = commandCenter
    }

    private func loadLocalHTML(_ webView: WKWebView) {
        guard let htmlURL = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "Web") else {
            return
        }
        let webDir = htmlURL.deletingLastPathComponent()
        webView.loadFileURL(htmlURL, allowingReadAccessTo: webDir)
    }

    private static let bridgeInjectionJS = """
    (function() {
        const pendingRequests = {};
        let requestId = 0;

        window._nativeBridge = {
            call: function(path, method, body) {
                return new Promise(function(resolve, reject) {
                    const id = ++requestId;
                    pendingRequests[id] = { resolve, reject };
                    window.webkit.messageHandlers.nativeBridge.postMessage({
                        id: id,
                        path: path,
                        method: method,
                        body: body
                    });
                });
            },
            resolve: function(id, data) {
                if (pendingRequests[id]) {
                    pendingRequests[id].resolve(data);
                    delete pendingRequests[id];
                }
            },
            reject: function(id, error) {
                if (pendingRequests[id]) {
                    pendingRequests[id].reject(new Error(error));
                    delete pendingRequests[id];
                }
            }
        };

        const originalFetch = window.fetch;
        window.fetch = function(input, init) {
            const url = typeof input === 'string' ? input : input.url;
            if (url.startsWith('/api/') || url.startsWith('/sundx/')) {
                const method = (init && init.method) || 'GET';
                let body = null;
                if (init && init.body) {
                    body = typeof init.body === 'string' ? init.body : JSON.stringify(init.body);
                }
                const headers = (init && init.headers) || {};
                return window._nativeBridge.call(url, method, body).then(function(data) {
                    return new Response(JSON.stringify(data), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                });
            }
            return originalFetch.apply(this, arguments);
        };
    })();
    """
}
