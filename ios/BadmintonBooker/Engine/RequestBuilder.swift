import Foundation

class RequestBuilder {
    static let shared = RequestBuilder()

    private var cachedTemplate: RequestTemplate?

    func buildSubmitRequests(params: [String: Any], rotationIndex: Int = 0) -> [[String: Any]] {
        let template = loadTemplate()
        if template.submitURL.isEmpty { return [] }

        let selections = params["selections"] as? [[String: Any]] ?? []
        if !selections.isEmpty {
            return buildSelectionRequests(template: template, params: params, selections: selections, rotationIndex: rotationIndex)
        }

        let dates = (params["dates"] as? [String]) ?? [params["date"] as? String ?? VenueDefaults.shared.defaultDate]
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

    private func buildSelectionRequests(template: RequestTemplate, params: [String: Any], selections: [[String: Any]], rotationIndex: Int = 0) -> [[String: Any]] {
        let dates = (params["dates"] as? [String]) ?? [params["date"] as? String ?? VenueDefaults.shared.defaultDate]
        let mode = params["request_mode"] as? String ?? "single"
        let groups = selectionGroups(selections: selections, mode: mode, rotationIndex: rotationIndex)

        var requests: [[String: Any]] = []
        for date in dates {
            for group in groups {
                guard let firstItem = group.first,
                      let court = firstItem["court"] as? [String: Any] else { continue }
                let timeSlots = group.compactMap { item -> [String: Any]? in
                    guard let slot = item["time_slot"] as? [String: Any],
                          let slotCourt = item["court"] as? [String: Any] else { return nil }
                    var merged = slot
                    merged["site_id"] = slotCourt["site_id"]
                    merged["site_name"] = slotCourt["site_name"]
                    return merged
                }
                let req = buildSingleRequest(template: template, date: date, court: court, timeSlots: timeSlots, params: params)
                requests.append(req)
            }
        }
        return requests
    }

    private func selectionGroups(selections: [[String: Any]], mode: String, rotationIndex: Int = 0) -> [[[String: Any]]] {
        var seenSelectionKeys = Set<String>()
        let normalized = selections.filter { item in
            item["court"] is [String: Any] && item["time_slot"] is [String: Any]
        }.filter { item in
            let key = selectionKey(item)
            if seenSelectionKeys.contains(key) { return false }
            seenSelectionKeys.insert(key)
            return true
        }.sorted { a, b in
            let aCourtId = String(describing: (a["court"] as? [String: Any])?["site_id"] ?? "")
            let bCourtId = String(describing: (b["court"] as? [String: Any])?["site_id"] ?? "")
            if aCourtId != bCourtId { return aCourtId < bCourtId }
            let aStart = ((a["time_slot"] as? [String: Any])?["start_timestamp"] as? Int) ?? 0
            let bStart = ((b["time_slot"] as? [String: Any])?["start_timestamp"] as? Int) ?? 0
            return aStart < bStart
        }

        if mode != "pair" {
            return normalized.map { [$0] }
        }

        var groups: [[[String: Any]]] = []
        var used = Set<Int>()
        for pair in orderedPairCandidates(selections: normalized, rotationIndex: rotationIndex) {
            if used.contains(pair.0) || used.contains(pair.1) { continue }
            groups.append([normalized[pair.0], normalized[pair.1]])
            used.insert(pair.0)
            used.insert(pair.1)
        }
        return groups
    }

    private func selectionKey(_ selection: [String: Any]) -> String {
        let court = selection["court"] as? [String: Any] ?? [:]
        let slot = selection["time_slot"] as? [String: Any] ?? [:]
        return [
            String(describing: court["site_id"] ?? ""),
            String(describing: slot["start_time"] ?? ""),
            String(describing: slot["end_time"] ?? ""),
        ].joined(separator: "|")
    }

    private func orderedPairCandidates(selections: [[String: Any]], rotationIndex: Int) -> [(Int, Int)] {
        var priority: [(Int, Int)] = []
        var normal: [(Int, Int)] = []
        for firstIndex in selections.indices {
            for secondIndex in selections.indices where secondIndex > firstIndex {
                let first = selections[firstIndex]
                let second = selections[secondIndex]
                guard pairCompatible(first, second) else { continue }
                if selectionHasPriorityCourt(first) || selectionHasPriorityCourt(second) {
                    priority.append((firstIndex, secondIndex))
                } else {
                    normal.append((firstIndex, secondIndex))
                }
            }
        }
        return rotated(priority, rotationIndex: rotationIndex) + rotated(normal, rotationIndex: rotationIndex)
    }

    private func rotated(_ items: [(Int, Int)], rotationIndex: Int) -> [(Int, Int)] {
        guard !items.isEmpty else { return [] }
        let offset = rotationIndex % items.count
        return Array(items[offset...]) + Array(items[..<offset])
    }

    private func pairCompatible(_ first: [String: Any], _ second: [String: Any]) -> Bool {
        let firstCourtId = String(describing: (first["court"] as? [String: Any])?["site_id"] ?? "")
        let secondCourtId = String(describing: (second["court"] as? [String: Any])?["site_id"] ?? "")
        let firstSlot = first["time_slot"] as? [String: Any] ?? [:]
        let secondSlot = second["time_slot"] as? [String: Any] ?? [:]
        let sameCourt = firstCourtId == secondCourtId
        let adjacentSameCourt = sameCourt
            && (((firstSlot["end_timestamp"] as? Int) ?? 0) == ((secondSlot["start_timestamp"] as? Int) ?? -1))
        let sameTimeDifferentCourt = !sameCourt
            && String(describing: firstSlot["start_time"] ?? "") == String(describing: secondSlot["start_time"] ?? "")
            && String(describing: firstSlot["end_time"] ?? "") == String(describing: secondSlot["end_time"] ?? "")
        return adjacentSameCourt || sameTimeDifferentCourt
    }

    private func selectionHasPriorityCourt(_ selection: [String: Any]) -> Bool {
        let court = selection["court"] as? [String: Any] ?? [:]
        let siteName = String(describing: court["site_name"] ?? "")
        let siteId = String(describing: court["site_id"] ?? "")
        return siteName.contains("7号") || siteName.trimmingCharacters(in: .whitespacesAndNewlines) == "7" || siteId == "7"
    }

    func buildSiteListRequest(params: [String: Any]) -> [String: Any]? {
        let template = loadTemplate()
        guard let baseURL = template.siteListURL else { return nil }

        let date = params["monitor_date"] as? String ?? params["date"] as? String ?? VenueDefaults.shared.defaultDate
        let url = replaceDateInURL(baseURL, date: date)

        var headers = template.siteListHeaders
        if let paramHeaders = params["headers"] as? [String: String] {
            for (key, value) in paramHeaders where !value.isEmpty {
                headers[key] = value
            }
        }
        headers = sanitizedHeaders(headers)

        return [
            "method": "GET",
            "url": url,
            "headers": headers,
        ]
    }

    private func buildSingleRequest(template: RequestTemplate, date: String, court: [String: Any], timeSlots: [[String: Any]], params: [String: Any]) -> [String: Any] {
        let siteId = court["site_id"] ?? 0
        let siteName = court["site_name"] as? String ?? ""
        let sourceDate = VenueDefaults.shared.defaultDate

        var body = template.submitBody
        body["venues_date"] = date

        let venuesSiteTime: [[String: Any]] = timeSlots.prefix(2).map { slot in
            let dayOffset = (date != sourceDate) ? daysBetween(from: slot["source_date"] as? String ?? sourceDate, to: date) : 0
            let secondsOffset = dayOffset * 86400
            let startTs = (slot["start_timestamp"] as? Int ?? 0) + secondsOffset
            let endTs = (slot["end_timestamp"] as? Int ?? 0) + secondsOffset
            return [
                "site_id": slot["site_id"] ?? siteId,
                "site_name": slot["site_name"] as? String ?? siteName,
                "start_time": slot["start_time"] as? String ?? "",
                "end_time": slot["end_time"] as? String ?? "",
                "start_timestamp": startTs,
                "end_timestamp": endTs,
                "times": slot["times"] as? String ?? "1",
                "price": slot["price"] as? String ?? "0",
            ] as [String: Any]
        }
        body["venues_site_time"] = venuesSiteTime

        var headers = template.headers
        if let paramHeaders = params["headers"] as? [String: String] {
            for (key, value) in paramHeaders where !value.isEmpty {
                headers[key] = value
            }
        }
        headers["content-type"] = "application/json"
        headers = sanitizedHeaders(headers)

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
            let fallback = RequestTemplate(submitURL: "", siteListURL: nil, headers: [:], siteListHeaders: [:], venuesId: "", submitBody: [:])
            cachedTemplate = fallback
            return fallback
        }

        var submitURL = ""
        var siteListURL: String?
        var siteListHeaders: [String: String] = [:]
        var headers: [String: String] = [:]
        var venuesId = ""
        var submitBody: [String: Any] = [:]

        for entry in entries {
            let path = entry["path"] as? String ?? ""
            let hostname = entry["hostname"] as? String ?? ""

            if path.contains("/v2/reserve/submit") {
                submitURL = (entry["url"] as? String) ?? "https://\(hostname)\(path)"

                if let req = entry["req"] as? [String: Any],
                   let base64 = req["base64"] as? String,
                   let bodyData = Data(base64Encoded: base64),
                   let bodyJson = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any] {
                    submitBody = bodyJson
                    venuesId = bodyJson["venues_id"] as? String ?? ""
                }

                if let reqHeaders = (entry["req"] as? [String: Any])?["headers"] as? [String: String] {
                    for (key, value) in reqHeaders {
                        let lower = key.lowercased()
                        if shouldDropHeader(lower) { continue }
                        headers[key] = value
                    }
                }
            }

            if path.hasPrefix("/v1/venues/venues_site_list") {
                siteListURL = (entry["url"] as? String) ?? "https://\(hostname)\(path)"
                if let reqHeaders = (entry["req"] as? [String: Any])?["headers"] as? [String: String] {
                    for (key, value) in reqHeaders {
                        let lower = key.lowercased()
                        if shouldDropHeader(lower) { continue }
                        siteListHeaders[key] = value
                    }
                }
            }
        }

        let template = RequestTemplate(
            submitURL: submitURL,
            siteListURL: siteListURL,
            headers: headers,
            siteListHeaders: siteListHeaders.isEmpty ? headers : siteListHeaders,
            venuesId: venuesId,
            submitBody: submitBody
        )
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

    func fullHeaders() -> [String: String] {
        loadTemplate().headers
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

    private func sanitizedHeaders(_ headers: [String: String]) -> [String: String] {
        var result: [String: String] = [:]
        for (key, value) in headers {
            let lower = key.lowercased()
            if shouldDropHeader(lower) || value.isEmpty { continue }
            result[key] = value
        }
        return result
    }

    private func shouldDropHeader(_ lowercasedName: String) -> Bool {
        lowercasedName.hasPrefix(":")
            || lowercasedName == "host"
            || lowercasedName == "content-length"
            || lowercasedName == "accept-encoding"
            || lowercasedName == "connection"
            || lowercasedName == "priority"
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
    let siteListHeaders: [String: String]
    let venuesId: String
    let submitBody: [String: Any]
}
