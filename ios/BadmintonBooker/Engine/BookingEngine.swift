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
            return preview(clientId: clientId, params: bodyDict)
        case "/api/save":
            return saveParams(clientId: clientId, params: bodyDict ?? [:])
        case "/api/start":
            return start(clientId: clientId, params: bodyDict ?? [:])
        case "/api/stop":
            return stop(clientId: clientId)
        case "/api/clear-logs":
            return clearLogs(clientId: clientId)
        case "/api/site-status":
            return try await siteStatus(clientId: clientId, params: bodyDict ?? [:])
        case "/api/check-token":
            return try await checkToken(clientId: clientId)
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
        let templateHeaders = RequestBuilder.shared.defaultHeaders()
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
            "headers": [
                "shop-id": templateHeaders["shop-id"] ?? "",
                "brand-code": templateHeaders["brand-code"] ?? "",
                "wx-token": templateHeaders["wx-token"] ?? "",
            ],
        ]
    }

    private func status(clientId: String) -> [String: Any] {
        let state = stateFor(clientId)
        return state.lock.withLock {
            [
                "running": state.running,
                "params": state.params.isEmpty ? defaultParams() : state.params,
                "logs": Array(state.logs.suffix(300)),
                "last_request": state.lastRequest ?? NSNull(),
                "last_response": state.lastResponse ?? NSNull(),
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
        log(state: state, "已更新抢票配置")
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

    private func checkToken(clientId: String) async throws -> [String: Any] {
        let state = stateFor(clientId)
        let effective = mergedParams(state: state, incoming: [:])
        var headers = RequestBuilder.shared.defaultHeaders()
        if let paramHeaders = effective["headers"] as? [String: String] {
            headers.merge(paramHeaders) { _, new in new }
        }
        let token = (headers["wx-token"] ?? "").trimmingCharacters(in: .whitespaces)
        if token.isEmpty {
            return ["success": false, "member_name": "", "error": "token 为空"]
        }
        let request: [String: Any] = [
            "url": "https://stmember.styd.cn/v1/member/is_parent?",
            "method": "GET",
            "headers": headers,
        ]
        do {
            let (raw, _) = try await httpClient.send(request: request, verifySsl: false)
            guard let payload = try? JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any] else {
                return ["success": false, "member_name": "", "error": "响应解析失败"]
            }
            if (payload["code"] as? Int) != 0 {
                return ["success": false, "member_name": "", "error": payload["msg"] as? String ?? "接口返回错误"]
            }
            let info = ((payload["data"] as? [String: Any])?["info"] as? [String: Any]) ?? [:]
            let name = (info["member_name"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            if !name.isEmpty {
                return ["success": true, "member_name": name]
            }
            return ["success": false, "member_name": "", "error": "无法提取 member_name"]
        } catch {
            return ["success": false, "member_name": "", "error": error.localizedDescription]
        }
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
        let requestInfo = requestSummary(request)
        state.lock.withLock { state.lastRequest = requestInfo }
        log(state: state, "查询场地状态请求：\(requestInfo["method"] ?? "GET") \(requestInfo["url"] ?? "")")

        if effective["dry_run"] as? Bool == true {
            let snapshot = mockSiteListSnapshot(params: effective)
            let availableCount = (snapshot["items"] as? [[String: Any]])?.filter { $0["available"] as? Bool == true }.count ?? 0
            let totalCount = (snapshot["items"] as? [[String: Any]])?.count ?? 0
            let message = "dry-run 查询完成：可约 \(availableCount) 个，已约 \(totalCount - availableCount) 个"
            log(state: state, message)
            state.lock.withLock {
                state.lastResponse = ["success": true, "message": message]
            }
            return [
                "success": true,
                "message": message,
                "request": requestInfo,
                "snapshot": snapshot,
                "available_count": availableCount,
                "occupied_count": totalCount - availableCount,
            ]
        }
        do {
            let (data, _) = try await httpClient.send(request: request, verifySsl: effective["verify_ssl"] as? Bool ?? false)
            if let payload = try? JSONSerialization.jsonObject(with: Data(data.utf8)) as? [String: Any] {
                let success = (payload["code"] as? Int) == 0
                let snapshot = siteListSnapshot(from: payload, params: effective)
                let availableCount = (snapshot["items"] as? [[String: Any]])?.filter { $0["available"] as? Bool == true }.count ?? 0
                let totalCount = (snapshot["items"] as? [[String: Any]])?.count ?? 0
                let message = success ? "查询完成：可约 \(availableCount) 个，已约 \(totalCount - availableCount) 个" : "查询失败：\(payload["msg"] ?? payload["code"] ?? "未知错误")"
                log(state: state, message)
                state.lock.withLock {
                    state.lastResponse = ["success": success, "message": message, "payload": payload]
                }
                return [
                    "success": success,
                    "message": message,
                    "request": requestInfo,
                    "snapshot": snapshot,
                    "available_count": availableCount,
                    "occupied_count": totalCount - availableCount,
                    "response": payload,
                ]
            }
            log(state: state, "查询响应解析失败：\(data.prefix(200))")
            return ["success": false, "message": "响应解析失败"]
        } catch {
            log(state: state, "查询场地状态失败：\(error.localizedDescription)")
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
        let requiredSuccessUnits = requiredSuccessUnits(params: params)

        if params["monitor_enabled"] as? Bool == true {
            await runMonitorLoop(state: state, params: params)
            state.lock.withLock {
                state.running = false
            }
            return
        }

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

            if successUnits >= requiredSuccessUnits {
                log(state: state, "抢票成功！共 \(successUnits)/\(requiredSuccessUnits) 个场次")
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
            log(state: state, "等待定时启动，剩余 \(formatDuration(remaining))")
            try? await Task.sleep(nanoseconds: min(1_000_000_000, UInt64(remaining * 1_000_000_000)))
        }
        log(state: state, "定时到达，开始执行")
        return true
    }

    private func sendRound(state: EngineState, requests: [[String: Any]], params: [String: Any], attempt: Int) async -> Int {
        let verifySsl = params["verify_ssl"] as? Bool ?? false
        let dryRun = params["dry_run"] as? Bool ?? false
        var successes = 0

        await withTaskGroup(of: Int.self) { group in
            for (index, req) in requests.prefix(16).enumerated() {
                group.addTask { [weak self] in
                    guard let self else { return 0 }
                    if dryRun {
                        let target = self.requestTargetDescription(req)
                        self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) dry-run 成功: \(target)")
                        state.lock.withLock {
                            state.lastRequest = self.requestSummary(req)
                            state.lastResponse = ["body": ["code": 0, "msg": "dry-run mock success"], "success": true] as [String: Any]
                        }
                        return self.requestSuccessUnits(req)
                    }
                    let target = self.requestTargetDescription(req)
                    let summary = self.requestSummary(req)
                    state.lock.withLock { state.lastRequest = summary }
                    self.log(state: state, "第\(attempt)次尝试 请求#\(index+1)：\(summary["method"] ?? "POST") \(summary["url"] ?? "")")
                    self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) 目标：\(target)")
                    do {
                        let (responseBody, _) = try await self.httpClient.send(request: req, verifySsl: verifySsl)
                        let success = self.isBookingSuccess(responseBody)
                        let units = success ? self.requestSuccessUnits(req) : 0
                        if success {
                            self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) 成功(\(units)小时): \(target)")
                        } else if attempt % 20 == 1 {
                            self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) 未成功: \(responseBody.prefix(200))")
                        }
                        state.lock.withLock {
                            state.lastResponse = ["body": responseBody, "success": success] as [String: Any]
                        }
                        return units
                    } catch {
                        if attempt % 100 == 1 {
                            self.log(state: state, "第\(attempt)次尝试 请求#\(index+1) 失败: \(error.localizedDescription)")
                        }
                        return 0
                    }
                }
            }
            for await result in group {
                successes += result
            }
        }

        if attempt % 50 == 0 {
            log(state: state, "已尝试 \(attempt) 次...")
        }

        return successes
    }

    private func runMonitorLoop(state: EngineState, params: [String: Any]) async {
        let targets = params["monitor_selections"] as? [[String: Any]] ?? []
        if targets.isEmpty {
            log(state: state, "监听下单未选择目标，请先查询并选择已约场地时间")
            return
        }

        let interval = params["monitor_interval_seconds"] as? Double ?? 20
        let maxAttempts = params["max_attempts"] as? Int ?? 100000
        let dryRun = params["dry_run"] as? Bool ?? false
        log(state: state, "开始监听下单：\(targets.count) 个目标")

        var attempt = 0
        while !state.lock.withLock({ state.stopRequested }) {
            attempt += 1
            log(state: state, "第\(attempt)轮监听场地释放")

            if dryRun {
                let released = targets
                log(state: state, "dry-run 发现 \(released.count) 个监听目标可预约，立即下单")
                let submitParams = params.merging(["selections": released]) { _, new in new }
                let requests = RequestBuilder.shared.buildSubmitRequests(params: submitParams)
                let successes = await sendRound(state: state, requests: requests, params: submitParams, attempt: attempt)
                if successes > 0 {
                    log(state: state, "监听下单 dry-run 成功，任务结束")
                    return
                }
            } else {
                do {
                    let result = try await siteStatus(state: state, params: params)
                    state.lock.withLock { state.lastResponse = result }
                    let released = availableMonitorTargets(snapshot: result["snapshot"] as? [String: Any], targets: targets)
                    if released.isEmpty {
                        log(state: state, "本轮没有监听目标释放")
                    } else {
                        log(state: state, "发现 \(released.count) 个监听目标可预约，立即下单")
                        let submitParams = params.merging(["selections": released]) { _, new in new }
                        let requests = RequestBuilder.shared.buildSubmitRequests(params: submitParams)
                        let successes = await sendRound(state: state, requests: requests, params: submitParams, attempt: attempt)
                        if successes > 0 {
                            log(state: state, "监听下单成功，任务结束")
                            return
                        }
                    }
                } catch {
                    log(state: state, "监听查询失败：\(error.localizedDescription)")
                }
            }

            if maxAttempts > 0 && attempt >= maxAttempts {
                log(state: state, "达到最大监听次数，任务结束")
                return
            }
            try? await Task.sleep(nanoseconds: UInt64(max(0.1, interval) * 1_000_000_000))
        }
    }

    private func isBookingSuccess(_ response: String) -> Bool {
        guard let data = response.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return false
        }
        if let code = optionalIntValue(json["code"]), code == 0 { return true }
        if json["success"] as? Bool == true { return true }
        let dataDict = json["data"] as? [String: Any] ?? [:]
        let successKeys = ["order_id", "order_no", "reserve_id", "reserve_no", "pay_order_id", "trade_no"]
        if successKeys.contains(where: { key in
            let value = dataDict[key] ?? json[key]
            return !String(describing: value ?? "").isEmpty && !(value is NSNull)
        }) {
            return true
        }
        let text = "\(json["msg"] ?? "") \(json["message"] ?? "") \(dataDict["msg"] ?? "")"
        let negativeWords = ["失败", "不可", "已满", "无效", "过期", "取消", "错误", "不足"]
        if negativeWords.contains(where: { text.contains($0) }) { return false }
        return ["成功", "待支付", "预约成功", "下单成功"].contains { text.contains($0) }
    }

    private func requestSuccessUnits(_ request: [String: Any]) -> Int {
        guard let body = request["body"] as? [String: Any],
              let slots = body["venues_site_time"] as? [[String: Any]],
              !slots.isEmpty else {
            return 1
        }
        return slots.count
    }

    private func requiredSuccessUnits(params: [String: Any]) -> Int {
        let rawDates = params["dates"] as? [String] ?? []
        let fallbackDate = params["date"] as? String
        let dates = rawDates.isEmpty ? [fallbackDate].compactMap { $0 }.filter { !$0.isEmpty } : rawDates
        let dateCount = max(1, Set(dates).count)
        let selections = params["monitor_enabled"] as? Bool == true
            ? params["monitor_selections"] as? [[String: Any]]
            : params["selections"] as? [[String: Any]]
        if let selections, !selections.isEmpty {
            var keys = Set<String>()
            for item in selections {
                let court = item["court"] as? [String: Any] ?? [:]
                let slot = item["time_slot"] as? [String: Any] ?? [:]
                let key = [
                    String(describing: court["site_id"] ?? ""),
                    String(describing: slot["start_time"] ?? ""),
                    String(describing: slot["end_time"] ?? ""),
                ].joined(separator: "|")
                if key != "||" {
                    keys.insert(key)
                }
            }
            if !keys.isEmpty {
                return max(1, keys.count * dateCount)
            }
        }
        if let timeSlots = params["time_slots"] as? [[String: Any]], !timeSlots.isEmpty {
            return max(1, timeSlots.count * dateCount)
        }
        return 2
    }

    private func siteStatus(state: EngineState, params: [String: Any]) async throws -> [String: Any] {
        guard let request = RequestBuilder.shared.buildSiteListRequest(params: params) else {
            return ["success": false, "message": "无法构建查询请求"]
        }
        let requestInfo = requestSummary(request)
        state.lock.withLock { state.lastRequest = requestInfo }
        log(state: state, "监听查询请求：\(requestInfo["method"] ?? "GET") \(requestInfo["url"] ?? "")")
        let (data, _) = try await httpClient.send(request: request, verifySsl: params["verify_ssl"] as? Bool ?? false)
        guard let payload = try? JSONSerialization.jsonObject(with: Data(data.utf8)) as? [String: Any] else {
            return ["success": false, "message": "响应解析失败", "request": requestInfo]
        }
        let snapshot = siteListSnapshot(from: payload, params: params)
        let success = (payload["code"] as? Int) == 0
        return ["success": success, "message": payload["msg"] ?? payload["code"] ?? "", "request": requestInfo, "snapshot": snapshot, "response": payload]
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
        if var headers = request["headers"] as? [String: String] {
            if let token = headers["wx-token"], !token.isEmpty {
                if token.count <= 8 {
                    headers["wx-token"] = String(repeating: "*", count: token.count)
                } else {
                    headers["wx-token"] = "\(token.prefix(4))...\(token.suffix(4))"
                }
            }
            summary["headers"] = headers
        }
        summary["body"] = request["body"]
        return summary
    }

    private func siteListSnapshot(from payload: [String: Any], params: [String: Any]) -> [String: Any] {
        let date = params["monitor_date"] as? String ?? params["date"] as? String ?? VenueDefaults.shared.sourceDate
        let data = payload["data"] as? [String: Any] ?? [:]
        let courts = data["list"] as? [[String: Any]] ?? []
        var items: [[String: Any]] = []
        for court in courts {
            let courtInfo: [String: Any] = [
                "site_id": court["site_id"] ?? "",
                "site_name": court["site_name"] ?? "",
            ]
            let slots = court["site_data"] as? [[String: Any]] ?? []
            for slot in slots {
                let status = slot["status"] ?? ""
                let times = String(describing: slot["times"] ?? "0")
                items.append([
                    "court": courtInfo,
                    "time_slot": [
                        "start_time": String(describing: slot["start_time"] ?? ""),
                        "end_time": String(describing: slot["end_time"] ?? ""),
                        "start_timestamp": intValue(slot["start_timestamp"]),
                        "end_timestamp": intValue(slot["end_timestamp"]),
                        "price": String(describing: slot["price"] ?? "0"),
                        "times": String(describing: slot["times"] ?? "1"),
                        "source_date": date,
                    ],
                    "status": status,
                    "available": String(describing: status) == "2" && times != "0",
                    "disabled_desc": String(describing: slot["disabled_desc"] ?? ""),
                    "disabled_reason": String(describing: slot["disabled_reason"] ?? ""),
                    "member_name": String(describing: slot["member_name"] ?? ""),
                    "mobile": String(describing: slot["mobile"] ?? ""),
                ])
            }
        }
        return ["date": date, "items": items]
    }

    private func availableMonitorTargets(snapshot: [String: Any]?, targets: [[String: Any]]) -> [[String: Any]] {
        let items = snapshot?["items"] as? [[String: Any]] ?? []
        let availableKeys = Set(items.filter { $0["available"] as? Bool == true }.map { selectionKey($0) })
        return targets.filter { availableKeys.contains(selectionKey($0)) }
    }

    private func selectionKey(_ item: [String: Any]) -> String {
        let court = item["court"] as? [String: Any] ?? [:]
        let slot = item["time_slot"] as? [String: Any] ?? [:]
        return "\(court["site_id"] ?? "")|\(slot["start_time"] ?? "")|\(slot["end_time"] ?? "")"
    }

    private func intValue(_ value: Any?) -> Int {
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? String, let int = Int(value) { return int }
        return 0
    }

    private func optionalIntValue(_ value: Any?) -> Int? {
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? String { return Int(value) }
        return nil
    }

    private func formatDuration(_ seconds: TimeInterval) -> String {
        let remaining = max(0, Int(seconds.rounded(.up)))
        let hours = remaining / 3600
        let minutes = (remaining % 3600) / 60
        let secs = remaining % 60
        if hours > 0 {
            return "\(hours)小时\(minutes)分钟\(secs)秒"
        }
        if minutes > 0 {
            return "\(minutes)分钟\(secs)秒"
        }
        return "\(secs)秒"
    }

    private func mockSiteListSnapshot(params: [String: Any]) -> [String: Any] {
        let defaults = VenueDefaults.shared
        let date = params["monitor_date"] as? String ?? params["date"] as? String ?? defaults.sourceDate
        var items: [[String: Any]] = []
        for (courtIndex, court) in defaults.courts.enumerated() {
            let courtDict: [String: Any] = ["site_id": court.siteId, "site_name": court.siteName]
            for (timeIndex, slot) in defaults.timeSlots.enumerated() {
                let occupied = (courtIndex + timeIndex) % 4 == 0 || ["19:00", "20:00"].contains(slot.startTime)
                items.append([
                    "court": courtDict,
                    "time_slot": slot.toDict(),
                    "status": occupied ? 1 : 2,
                    "available": !occupied,
                    "disabled_desc": occupied ? "已预约" : "",
                    "disabled_reason": occupied ? "mock" : "",
                    "member_name": occupied ? "Mock用户\(courtIndex + 1)" : "",
                    "mobile": occupied ? "138****\(String(format: "%04d", timeIndex + 1))" : "",
                ])
            }
        }
        return ["date": date, "items": items]
    }

    private func requestTargetDescription(_ request: [String: Any]) -> String {
        guard let body = request["body"] as? [String: Any] else { return "未知请求" }
        let date = body["venues_date"] as? String ?? "未知日期"
        let slots = body["venues_site_time"] as? [[String: Any]] ?? []
        guard let first = slots.first else { return "\(date) 未知场地 未知时间" }
        let courtName = first["site_name"] as? String ?? "未知场地"
        let times = slots.map { "\($0["start_time"] ?? "?")-\($0["end_time"] ?? "?")" }.joined(separator: ",")
        return "\(date) \(courtName) \(times)"
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
