import Foundation

class RemoteAPIClient {
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    func forward(baseURL: String, path: String, method: String, body: String?, clientId: String) async throws -> Any {
        guard !baseURL.isEmpty else {
            throw RemoteError.noServerConfigured
        }

        let urlString = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path
        guard let url = URL(string: urlString) else {
            throw RemoteError.invalidURL(urlString)
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(clientId, forHTTPHeaderField: "x-client-id")

        if let body, !body.isEmpty {
            request.httpBody = Data(body.utf8)
        }

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw RemoteError.invalidResponse
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let bodyStr = String(data: data, encoding: .utf8) ?? ""
            throw RemoteError.httpError(httpResponse.statusCode, bodyStr)
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) else {
            throw RemoteError.invalidJSON
        }

        return json
    }
}

enum RemoteError: LocalizedError {
    case noServerConfigured
    case invalidURL(String)
    case invalidResponse
    case httpError(Int, String)
    case invalidJSON

    var errorDescription: String? {
        switch self {
        case .noServerConfigured: return "未配置服务器地址，请在设置中填写"
        case .invalidURL(let url): return "无效的URL: \(url)"
        case .invalidResponse: return "服务器响应无效"
        case .httpError(let code, let body): return "HTTP \(code): \(body.prefix(200))"
        case .invalidJSON: return "响应JSON解析失败"
        }
    }
}
