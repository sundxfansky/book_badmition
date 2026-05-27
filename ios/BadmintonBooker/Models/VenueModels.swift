import Foundation

struct TimeSlot: Codable, Hashable {
    let startTime: String
    let endTime: String
    let startTimestamp: Int
    let endTimestamp: Int
    let price: String
    let times: String
    let sourceDate: String

    enum CodingKeys: String, CodingKey {
        case startTime = "start_time"
        case endTime = "end_time"
        case startTimestamp = "start_timestamp"
        case endTimestamp = "end_timestamp"
        case price, times
        case sourceDate = "source_date"
    }
}

struct Court: Codable, Hashable {
    let siteId: Int
    let siteName: String

    enum CodingKeys: String, CodingKey {
        case siteId = "site_id"
        case siteName = "site_name"
    }
}

struct BookingParams: Codable {
    var dryRun: Bool
    var verifySsl: Bool
    var intervalSeconds: Double
    var maxAttempts: Int
    var scheduleEnabled: Bool
    var scheduledStartAt: String
    var date: String
    var dates: [String]
    var requestMode: String?
    var courts: [Court]
    var timeSlots: [TimeSlot]
    var monitorEnabled: Bool
    var monitorDate: String
    var monitorIntervalSeconds: Double
    var monitorSelections: [[String: AnyCodable]]
    var headers: [String: String]

    enum CodingKeys: String, CodingKey {
        case dryRun = "dry_run"
        case verifySsl = "verify_ssl"
        case intervalSeconds = "interval_seconds"
        case maxAttempts = "max_attempts"
        case scheduleEnabled = "schedule_enabled"
        case scheduledStartAt = "scheduled_start_at"
        case date, dates
        case requestMode = "request_mode"
        case courts
        case timeSlots = "time_slots"
        case monitorEnabled = "monitor_enabled"
        case monitorDate = "monitor_date"
        case monitorIntervalSeconds = "monitor_interval_seconds"
        case monitorSelections = "monitor_selections"
        case headers
    }
}

struct AnyCodable: Codable, Hashable {
    let value: Any

    init(_ value: Any) { self.value = value }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let int = try? container.decode(Int.self) { value = int }
        else if let double = try? container.decode(Double.self) { value = double }
        else if let string = try? container.decode(String.self) { value = string }
        else if let bool = try? container.decode(Bool.self) { value = bool }
        else if let dict = try? container.decode([String: AnyCodable].self) { value = dict }
        else if let array = try? container.decode([AnyCodable].self) { value = array }
        else { value = NSNull() }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch value {
        case let v as Int: try container.encode(v)
        case let v as Double: try container.encode(v)
        case let v as String: try container.encode(v)
        case let v as Bool: try container.encode(v)
        case let v as [String: AnyCodable]: try container.encode(v)
        case let v as [AnyCodable]: try container.encode(v)
        default: try container.encodeNil()
        }
    }

    static func == (lhs: AnyCodable, rhs: AnyCodable) -> Bool {
        String(describing: lhs.value) == String(describing: rhs.value)
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(String(describing: value))
    }
}
