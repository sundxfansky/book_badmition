import Foundation

struct VenueDefaults {
    static let shared = VenueDefaults()

    let sourceDate = "2026/05/28"

    let courts: [CourtInfo] = [
        CourtInfo(siteId: 3692729935134806, siteName: "1号场"),
        CourtInfo(siteId: 3692729935134807, siteName: "2号场"),
        CourtInfo(siteId: 3692729935134808, siteName: "3号场"),
        CourtInfo(siteId: 3692729935134809, siteName: "4号场"),
        CourtInfo(siteId: 3692729935134810, siteName: "5号场"),
        CourtInfo(siteId: 3692729935134811, siteName: "6号场"),
        CourtInfo(siteId: 3704542185717805, siteName: "7号场"),
        CourtInfo(siteId: 3713773110689834, siteName: "6楼V1号场"),
        CourtInfo(siteId: 3713764789190689, siteName: "6楼V2号场"),
    ]

    let timeSlots: [TimeSlotInfo] = [
        TimeSlotInfo(startTime: "07:00", endTime: "08:00", startTimestamp: 1779922800, endTimestamp: 1779926400, price: "75", times: "1"),
        TimeSlotInfo(startTime: "08:00", endTime: "09:00", startTimestamp: 1779926400, endTimestamp: 1779930000, price: "75", times: "1"),
        TimeSlotInfo(startTime: "09:00", endTime: "10:00", startTimestamp: 1779930000, endTimestamp: 1779933600, price: "75", times: "1"),
        TimeSlotInfo(startTime: "10:00", endTime: "11:00", startTimestamp: 1779933600, endTimestamp: 1779937200, price: "75", times: "1"),
        TimeSlotInfo(startTime: "11:00", endTime: "12:00", startTimestamp: 1779937200, endTimestamp: 1779940800, price: "75", times: "1"),
        TimeSlotInfo(startTime: "12:00", endTime: "13:00", startTimestamp: 1779940800, endTimestamp: 1779944400, price: "75", times: "1"),
        TimeSlotInfo(startTime: "13:00", endTime: "14:00", startTimestamp: 1779944400, endTimestamp: 1779948000, price: "75", times: "1"),
        TimeSlotInfo(startTime: "14:00", endTime: "15:00", startTimestamp: 1779948000, endTimestamp: 1779951600, price: "130", times: "1"),
        TimeSlotInfo(startTime: "15:00", endTime: "16:00", startTimestamp: 1779951600, endTimestamp: 1779955200, price: "130", times: "1"),
        TimeSlotInfo(startTime: "16:00", endTime: "17:00", startTimestamp: 1779955200, endTimestamp: 1779958800, price: "130", times: "1"),
        TimeSlotInfo(startTime: "17:00", endTime: "18:00", startTimestamp: 1779958800, endTimestamp: 1779962400, price: "130", times: "1"),
        TimeSlotInfo(startTime: "18:00", endTime: "19:00", startTimestamp: 1779962400, endTimestamp: 1779966000, price: "130", times: "1"),
        TimeSlotInfo(startTime: "19:00", endTime: "20:00", startTimestamp: 1779966000, endTimestamp: 1779969600, price: "130", times: "1"),
        TimeSlotInfo(startTime: "20:00", endTime: "21:00", startTimestamp: 1779969600, endTimestamp: 1779973200, price: "130", times: "1"),
        TimeSlotInfo(startTime: "21:00", endTime: "22:00", startTimestamp: 1779973200, endTimestamp: 1779976800, price: "130", times: "1"),
    ]
}

struct CourtInfo {
    let siteId: Int
    let siteName: String
}

struct TimeSlotInfo {
    let startTime: String
    let endTime: String
    let startTimestamp: Int
    let endTimestamp: Int
    let price: String
    let times: String

    func toDict() -> [String: Any] {
        [
            "start_time": startTime,
            "end_time": endTime,
            "start_timestamp": startTimestamp,
            "end_timestamp": endTimestamp,
            "price": price,
            "times": times,
            "source_date": VenueDefaults.shared.sourceDate,
        ]
    }
}
