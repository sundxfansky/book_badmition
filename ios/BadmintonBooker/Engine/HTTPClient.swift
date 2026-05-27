import Foundation

class BookingHTTPClient: NSObject, URLSessionDelegate {
    private lazy var verifiedSession: URLSession = {
        URLSession(configuration: .default, delegate: nil, delegateQueue: nil)
    }()

    private lazy var unverifiedSession: URLSession = {
        URLSession(configuration: .default, delegate: self, delegateQueue: nil)
    }()

    func send(request: [String: Any], verifySsl: Bool, timeout: TimeInterval = 10) async throws -> (String, Bool) {
        guard let urlString = request["url"] as? String, let url = URL(string: urlString) else {
            throw HTTPClientError.invalidURL
        }

        var urlRequest = URLRequest(url: url, timeoutInterval: timeout)
        urlRequest.httpMethod = (request["method"] as? String) ?? "GET"

        if let headers = request["headers"] as? [String: String] {
            for (key, value) in headers {
                urlRequest.setValue(value, forHTTPHeaderField: key)
            }
        }

        if let body = request["body"] {
            if let bodyDict = body as? [String: Any] {
                urlRequest.httpBody = try? JSONSerialization.data(withJSONObject: bodyDict)
                if urlRequest.value(forHTTPHeaderField: "Content-Type") == nil {
                    urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
                }
            } else if let bodyString = body as? String {
                urlRequest.httpBody = Data(bodyString.utf8)
            }
        }

        if verifySsl {
            do {
                let (data, _) = try await verifiedSession.data(for: urlRequest)
                return (String(data: data, encoding: .utf8) ?? "", false)
            } catch let error as NSError where error.domain == NSURLErrorDomain &&
                (error.code == NSURLErrorServerCertificateUntrusted ||
                 error.code == NSURLErrorServerCertificateHasBadDate ||
                 error.code == NSURLErrorServerCertificateHasUnknownRoot) {
                let (data, _) = try await unverifiedSession.data(for: urlRequest)
                return (String(data: data, encoding: .utf8) ?? "", true)
            }
        } else {
            let (data, _) = try await unverifiedSession.data(for: urlRequest)
            return (String(data: data, encoding: .utf8) ?? "", false)
        }
    }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            return (.useCredential, URLCredential(trust: trust))
        }
        return (.performDefaultHandling, nil)
    }
}

enum HTTPClientError: LocalizedError {
    case invalidURL
    case noResponse

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "无效的请求URL"
        case .noResponse: return "无响应"
        }
    }
}
