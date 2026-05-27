import SwiftUI

struct ContentView: View {
    @EnvironmentObject var settings: AppSettings
    @StateObject private var commandCenter = WebViewCommandCenter.shared
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            BookingWebView()
                .environmentObject(settings)
                .navigationTitle("羽毛球抢票")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItemGroup(placement: .topBarLeading) {
                        Button(action: { WebViewCommandCenter.shared.previousTab() }) {
                            Image(systemName: "chevron.left")
                        }
                        Button(action: { WebViewCommandCenter.shared.nextTab() }) {
                            Image(systemName: "chevron.right")
                        }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(action: { commandCenter.toggleRunState() }) {
                            Image(systemName: commandCenter.isRunning ? "stop.fill" : "play.fill")
                        }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(action: { showSettings = true }) {
                            Image(systemName: "gear")
                        }
                    }
                }
                .sheet(isPresented: $showSettings) {
                    SettingsView()
                        .environmentObject(settings)
                }
        }
    }
}
