import Foundation

class RequestBuilder {
    static let shared = RequestBuilder()

    private var cachedTemplate: RequestTemplate?

    func buildSubmitRequests(params: [String: Any]) -> [[String: Any]] {
        let template = loadTemplate()
        let dates = (params["dates"] as? [String]) ?? [params["date"] as? String ?? VenueDefaults.shared.sourceDate]
        let courts = params["courts"] as? [[String: Any]] ?? []
        let timeSlots = params["time_slots"] as? [[String: Any]] ?? []

        if courts.isEmpty || timeSlots.isEmpty { return [] }

        var requests: [[String: Any]] = []
        for date in dates {
            for court in courts {
                let req = buildSingleRequest(template: template, date: date, court: court, timeSlots: timeSlots, params: params)
                requests.append(req)
            }
        }
        return requests
    }

    func buildSiteListRequest(params: [String: Any]) -> [String: Any]? {
        let template = loadTemplate()
        guard let baseURL = template.siteListURL else { return nil }

        let date = params["monitor_date"] as? String ?? params["date"] as? String ?? VenueDefaults.shared.sourceDate
        let url = replaceDateInURL(baseURL, date: date)

        return [
            "method": "GET",
            "url": url,
            "headers": template.headers,
        ]
    }

    private func buildSingleRequest(template: RequestTemplate, date: String, court: [String: Any], timeSlots: [[String: Any]], params: [String: Any]) -> [String: Any] {
        let siteId = court["site_id"]
        let sourceDate = VenueDefaults.shared.sourceDate

        let adjustedSlots: [[String: Any]] = timeSlots.prefix(2).map { slot in
            var adjusted = slot
            if date != sourceDate {
                let dayOffset = daysBetween(from: sourceDate, to: date)
                let secondsOffset = dayOffset * 86400
                if let startTs = slot["start_timestamp"] as? Int {
                    adjusted["start_timestamp"] = startTs + secondsOffset
                }
                if let endTs = slot["end_timestamp"] as? Int {
                    adjusted["end_timestamp"] = endTs + secondsOffset
                }
            }
            adjusted["source_date"] = date
            return adjusted
        }

        let venuesSiteTime: [[String: Any]] = adjustedSlots.map { slot in
            [
                "id": siteId as Any,
                "start_time": slot["start_timestamp"] as Any,
                "end_time": slot["end_timestamp"] as Any,
                "price": slot["price"] as Any,
                "times": slot["times"] as Any,
            ]
        }

        let body: [String: Any] = [
            "venues_id": template.venuesId,
            "venues_date": date,
            "venues_site_time": venuesSiteTime,
        ]

        var headers = template.headers
        if let paramHeaders = params["headers"] as? [String: String] {
            for (key, value) in paramHeaders where !value.isEmpty {
                headers[key] = value
            }
        }
        headers["Content-Type"] = "application/json"

        return [
            "method": "POST",
            "url": template.submitURL,
            "headers": headers,
            "body": body,
        ]
    }

    private func loadTemplate() -> RequestTemplate {
        if let cached = cachedTemplate { return cached }

        let data: Data? = {
            let docsDir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
            let importedFile = docsDir.appendingPathComponent("request.txt")
            if let d = try? Data(contentsOf: importedFile) { return d }
            if let bundledURL = Bundle.main.url(forResource: "request", withExtension: "txt") {
                return try? Data(contentsOf: bundledURL)
            }
            return nil
        }()

        guard let data,
              let entries = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            let fallback = RequestTemplate(submitURL: "", siteListURL: nil, headers: [:], venuesId: "")
            cachedTemplate = fallback
            return fallback
        }

        var submitURL = ""
        var siteListURL: String?
        var headers: [String: String] = [:]
        var venuesId = ""

        for entry in entries {
            let path = entry["path"] as? String ?? ""
            let hostname = entry["hostname"] as? String ?? ""

            if path.contains("/v2/reserve/submit") {
                submitURL = "https://\(hostname)\(path)"

                if let req = entry["req"] as? [String: Any],
                   let base64 = req["base64"] as? String,
                   let bodyData = Data(base64Encoded: base64),
                   let bodyJson = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any] {
                    venuesId = bodyJson["venues_id"] as? String ?? ""
                }

                if let reqHeaders = (entry["req"] as? [String: Any])?["headers"] as? [String: String] {
                    for (key, value) in reqHeaders {
                        let lower = key.lowercased()
                        if lower == "host" || lower == "content-length" || lower == "accept-encoding" { continue }
                        headers[key] = value
                    }
                }
            }

            if path.hasPrefix("/v1/venues_site_list") {
                siteListURL = "https://\(hostname)\(path)"
            }
        }

        let template = RequestTemplate(submitURL: submitURL, siteListURL: siteListURL, headers: headers, venuesId: venuesId)
        cachedTemplate = template
        return template
    }

    func reloadTemplate() {
        cachedTemplate = nil
    }

    func defaultHeaders() -> [String: String] {
        let template = loadTemplate()
        var result: [String: String] = [:]
        let keys = ["wx-token", "shop-id", "brand-code"]
        for key in keys {
            if let value = template.headers.first(where: { $0.key.lowercased() == key })?.value, !value.isEmpty {
                result[key] = value
            }
        }
        return result
    }

    private func replaceDateInURL(_ url: String, date: String) -> String {
        guard var components = URLComponents(string: url) else { return url }
        var items = components.queryItems ?? []
        if let idx = items.firstIndex(where: { $0.name == "date" }) {
            items[idx] = URLQueryItem(name: "date", value: date)
        } else {
            items.append(URLQueryItem(name: "date", value: date))
        }
        components.queryItems = items
        return components.string ?? url
    }

    private func daysBetween(from source: String, to target: String) -> Int {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy/MM/dd"
        formatter.timeZone = TimeZone(identifier: "Asia/Shanghai")

        guard let sourceDate = formatter.date(from: source),
              let targetDate = formatter.date(from: target) else { return 0 }

        let calendar = Calendar(identifier: .gregorian)
        let components = calendar.dateComponents([.day], from: sourceDate, to: targetDate)
        return components.day ?? 0
    }
}

struct RequestTemplate {
    let submitURL: String
    let siteListURL: String?
    let headers: [String: String]
    let venuesId: String
}
