import SwiftUI
import WebKit

struct BookingWebView: UIViewRepresentable {
    @EnvironmentObject var settings: AppSettings

    func makeCoordinator() -> WebViewBridge {
        WebViewBridge(settings: settings)
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

        config.preferences.javaScriptCanOpenWindowsAutomatically = false

        let webView = WKWebView(frame: .zero, configuration: config)
        if #available(iOS 16.4, *) {
            webView.isInspectable = true
        }
        context.coordinator.webView = webView

        loadLocalHTML(webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.settings = settings
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
