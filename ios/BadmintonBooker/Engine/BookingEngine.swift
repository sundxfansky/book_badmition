import Foundation

class BookingEngine {
    private var states: [String: EngineState] = [:]
    private let statesLock = NSLock()
    private let httpClient = BookingHTTPClient()

    func handle(path: String, method: String, body: String?, clientId: String) async throws -> Any {
        let bodyDict: [String: Any]? = {
            guard let body, let data = body.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            return json
        }()

        switch path {
        case "/api/metadata":
            return metadata(clientId: clientId)
        case "/api/status":
            return status(clientId: clientId)
        case "/api/preview":
            return preview(clientId: clientId, params: bodyDict?["params"] as? [String: Any])
        case "/api/save":
            return saveParams(clientId: clientId, params: bodyDict?["params"] as? [String: Any] ?? [:])
        case "/api/start":
            return start(clientId: clientId, params: bodyDict?["params"] as? [String: Any] ?? [:])
        case "/api/stop":
            return stop(clientId: clientId)
        case "/api/clear-logs":
            return clearLogs(clientId: clientId)
        case "/api/site-status":
            return try await siteStatus(clientId: clientId, params: bodyDict?["params"] as? [String: Any] ?? [:])
        default:
            return ["error": "Unknown path: \(path)"]
        }
    }

    private func stateFor(_ clientId: String) -> EngineState {
        statesLock.lock()
        defer { statesLock.unlock() }
        if let existing = states[clientId] { return existing }
        let state = EngineState()
        states[clientId] = state
        return state
    }

    private func metadata(clientId: String) -> [String: Any] {
        let defaults = VenueDefaults.shared
        let snapshot: [String: Any] = [
            "venues_id": "",
            "date": defaults.sourceDate,
            "dates": [defaults.sourceDate],
            "courts": defaults.courts.map { ["site_id": $0.siteId, "site_name": $0.siteName] },
            "times": defaults.timeSlots.map { $0.toDict() },
            "selected_site_id": defaults.courts.first?.siteId ?? 0,
            "selected_site_name": defaults.courts.first?.siteName ?? "",
            "fixed_courts": defaults.courts.map { ["site_id": $0.siteId, "site_name": $0.siteName] },
            "selected_times": [],
        ]
        return [
            "snapshot": snapshot,
            "site_list_snapshot": NSNull(),
            "params": defaultParams(),
        ]
    }

    private func defaultParams() -> [String: Any] {
        let defaults = VenueDefaults.shared
        return [
            "dry_run": false,
            "verify_ssl": false,
            "interval_seconds": 0.1,
            "max_attempts": 100000,
            "schedule_enabled": false,
            "scheduled_start_at": "",
            "date": defaults.sourceDate,
            "dates": [defaults.sourceDate],
            "monitor_enabled": false,
            "monitor_date": defaults.sourceDate,
            "monitor_interval_seconds": 20,
            "monitor_selections": [] as [[String: Any]],
            "courts": [["site_id": defaults.courts.first?.siteId ?? 0, "site_name": defaults.courts.first?.siteName ?? ""]],
            "time_slots": [] as [[String: Any]],
            "headers": ["shop-id": "", "brand-code": ""],
        ]
    }

    private func status(clientId: String) -> [String: Any] {
        let state = stateFor(clientId)
        return state.lock.withLock {
            [
                "running": state.running,
                "params": state.params,
                "logs": Array(state.logs.suffix(300)),
                "last_request": state.lastRequest as Any,
                "last_response": state.lastResponse as Any,
                "waiting_for_schedule": state.waitingForSchedule,
                "scheduled_start_at": state.scheduledStartAt,
            ]
        }
    }

    private func preview(clientId: String, params: [String: Any]?) -> [String: Any] {
        let state = stateFor(clientId)
        let effective = mergedParams(state: state, incoming: params ?? [:])
        let requests = RequestBuilder.shared.buildSubmitRequests(params: effective)
        return [
            "count": requests.count,
            "requests": requests.map { requestSummary($0) },
        ]
    }

    private func saveParams(clientId: String, params: [String: Any]) -> [String: Any] {
        let state = stateFor(clientId)
        let merged = mergedParams(state: state, incoming: params)
        state.lock.withLock {
            state.params = merged
        }
        log(state: state, "已更新抢票参数")
        return status(clientId: clientId)
    }

    private func start(clientId: String, params: [String: Any]) -> [String: Any] {
        let state = stateFor(clientId)
        let runParams = mergedParams(state: state, incoming: params)

        let alreadyRunning = state.lock.withLock { state.running }
        if alreadyRunning { return status(clientId: clientId) }

        state.lock.withLock {
            state.params = runParams
            state.running = true
            state.waitingForSchedule = false
            state.scheduledStartAt = (runParams["scheduled_start_at"] as? String) ?? ""
            state.stopRequested = false
        }

        Task.detached { [weak self] in
            await self?.runLoop(state: state, params: runParams)
        }

        if let scheduleEnabled = runParams["schedule_enabled"] as? Bool, scheduleEnabled,
           let startAt = runParams["scheduled_start_at"] as? String, !startAt.isEmpty {
            log(state: state, "已设置定时启动：\(startAt)")
        } else {
            log(state: state, "开始抢票...")
        }

        return status(clientId: clientId)
    }

    private func stop(clientId: String) -> [String: Any] {
        let state = stateFor(clientId)
        state.lock.withLock {
            state.stopRequested = true
            state.running = false
            state.waitingForSchedule = false
        }
        log(state: state, "已停止")
        return status(clientId: clientId)
    }

    private func clearLogs(clientId: String) -> [String: Any] {
        let state = stateFor(clientId)
        state.lock.withLock { state.logs = [] }
        return status(clientId: clientId)
    }

    private func siteStatus(clientId: String, params: [String: Any]) async throws -> [String: Any] {
        let state = stateFor(clientId)
        let effective = mergedParams(state: state, incoming: params)
        guard let request = RequestBuilder.shared.buildSiteListRequest(params: effective) else {
            return ["success": false, "message": "无法构建查询请求"]
        }
        do {
            let (data, _) = try await httpClient.send(request: request, verifySsl: effective["verify_ssl"] as? Bool ?? false)
            if let json = try? JSONSerialization.jsonObject(with: Data(data.utf8)) as? [String: Any] {
                return ["success": true, "message": "查询成功", "snapshot": json]
            }
            return ["success": false, "message": "响应解析失败"]
        } catch {
            return ["success": false, "message": error.localizedDescription]
        }
    }

    // MARK: - Booking Loop

    private func runLoop(state: EngineState, params: [String: Any]) async {
        if let scheduleEnabled = params["schedule_enabled"] as? Bool, scheduleEnabled,
           let startAt = params["scheduled_start_at"] as? String, !startAt.isEmpty {
            state.lock.withLock { state.waitingForSchedule = true }
            let waited = await waitForSchedule(state: state, startAt: startAt)
            state.lock.withLock { state.waitingForSchedule = false }
            if !waited { return }
        }

        let interval = params["interval_seconds"] as? Double ?? 0.1
        let maxAttempts = params["max_attempts"] as? Int ?? 100000
        var attempt = 0
        var successUnits = 0

        while !state.lock.withLock({ state.stopRequested }) {
            attempt += 1
            if maxAttempts > 0 && attempt > maxAttempts {
                log(state: state, "已达到最大尝试次数 \(maxAttempts)")
                break
            }

            let requests = RequestBuilder.shared.buildSubmitRequests(params: params)
            if requests.isEmpty {
                log(state: state, "无法构建请求，请检查参数")
                break
            }

            let results = await sendRound(state: state, requests: requests, params: params, attempt: attempt)
            successUnits += results

            if successUnits >= 2 {
                log(state: state, "抢票成功！共 \(successUnits) 个场次")
                break
            }

            if interval > 0 {
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }

        state.lock.withLock {
            state.running = false
        }
    }

    private func waitForSchedule(state: EngineState, startAt: String) async -> Bool {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        formatter.timeZone = TimeZone(identifier: "Asia/Shanghai")

        guard let targetDate = formatter.date(from: startAt) else {
            log(state: state, "定时时间格式错误: \(startAt)")
            return false
        }

        while Date() < targetDate {
            if state.lock.withLock({ state.stopRequested }) { return false }
            let remaining = targetDate.timeIntervalSince(Date())
            log(state: state, "等待定时启动，剩余 \(Int(remaining)) 秒")
            try? await Task.sleep(nanoseconds: min(1_000_000_000, UInt64(remaining * 1_000_000_000)))
        }
        log(state: state, "定时到达，开始执行")
        return true
    }

    private func sendRound(state: EngineState, requests: [[String: Any]], params: [String: Any], attempt: Int) async -> Int {
        let verifySsl = params["verify_ssl"] as? Bool ?? false
        var successes = 0

        await withTaskGroup(of: Bool.self) { group in
            for (index, req) in requests.prefix(16).enumerated() {
                group.addTask { [weak self] in
                    guard let self else { return false }
                    do {
                        let (responseBody, _) = try await self.httpClient.send(request: req, verifySsl: verifySsl)
                        let success = self.isBookingSuccess(responseBody)
                        if success {
                            self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) 成功!")
                        }
                        state.lock.withLock {
                            state.lastResponse = ["body": responseBody, "success": success] as [String: Any]
                        }
                        return success
                    } catch {
                        if attempt % 100 == 1 {
                            self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) 失败: \(error.localizedDescription)")
                        }
                        return false
                    }
                }
            }
            for await result in group {
                if result { successes += 1 }
            }
        }

        if attempt % 50 == 0 {
            log(state: state, "已尝试 \(attempt) 次...")
        }

        return successes
    }

    private func isBookingSuccess(_ response: String) -> Bool {
        guard let data = response.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let code = json["code"] as? Int else {
            return false
        }
        return code == 0
    }

    // MARK: - Helpers

    private func mergedParams(state: EngineState, incoming: [String: Any]) -> [String: Any] {
        var base = state.lock.withLock { state.params }
        for (key, value) in incoming {
            base[key] = value
        }
        if base.isEmpty {
            return defaultParams()
        }
        return base
    }

    private func log(state: EngineState, _ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        let timestamp = formatter.string(from: Date())
        state.lock.withLock {
            state.logs.append("[\(timestamp)] \(message)")
            if state.logs.count > 300 {
                state.logs.removeFirst(state.logs.count - 300)
            }
        }
    }

    private func requestSummary(_ request: [String: Any]) -> [String: Any] {
        var summary: [String: Any] = [:]
        summary["method"] = request["method"] ?? "POST"
        summary["url"] = request["url"] ?? ""
        if let body = request["body"] as? [String: Any] {
            summary["body_preview"] = String(describing: body).prefix(200)
        }
        return summary
    }
}

class EngineState {
    let lock = NSLock()
    var running = false
    var waitingForSchedule = false
    var scheduledStartAt = ""
    var stopRequested = false
    var logs: [String] = []
    var params: [String: Any] = [:]
    var lastRequest: [String: Any]?
    var lastResponse: [String: Any]?
}
