import Foundation
import Combine

enum AppMode: String, CaseIterable {
    case remote = "remote"
    case local = "local"

    var displayName: String {
        switch self {
        case .remote: return "远程服务器"
        case .local: return "本地引擎"
        }
    }
}

class AppSettings: ObservableObject {
    @Published var mode: AppMode {
        didSet { UserDefaults.standard.set(mode.rawValue, forKey: "app_mode") }
    }
    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "server_url") }
    }
    @Published var clientId: String

    init() {
        let savedMode = UserDefaults.standard.string(forKey: "app_mode") ?? "remote"
        self.mode = AppMode(rawValue: savedMode) ?? .remote
        self.serverURL = UserDefaults.standard.string(forKey: "server_url") ?? ""

        if let saved = UserDefaults.standard.string(forKey: "client_id"), !saved.isEmpty {
            self.clientId = saved
        } else {
            let newId = UUID().uuidString.lowercased()
            UserDefaults.standard.set(newId, forKey: "client_id")
            self.clientId = newId
        }
    }
}
