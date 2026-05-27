import Foundation

@MainActor
class RuntimeState: ObservableObject {
    @Published var running = false
    @Published var waitingForSchedule = false
    @Published var scheduledStartAt = ""
    @Published var logs: [String] = []
    @Published var lastRequest: [String: Any]?
    @Published var lastResponse: [String: Any]?
    @Published var params: [String: Any] = [:]

    func addLog(_ message: String) {
        let timestamp = Self.formatter.string(from: Date())
        logs.append("[\(timestamp)] \(message)")
        if logs.count > 300 { logs.removeFirst(logs.count - 300) }
    }

    func reset() {
        running = false
        waitingForSchedule = false
        scheduledStartAt = ""
        logs = []
        lastRequest = nil
        lastResponse = nil
    }

    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()
}
