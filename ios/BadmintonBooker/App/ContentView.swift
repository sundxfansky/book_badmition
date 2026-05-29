import SwiftUI

struct ContentView: View {
    @EnvironmentObject var settings: AppSettings
    @StateObject private var commandCenter = WebViewCommandCenter()
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            BookingWebView(commandCenter: commandCenter)
                .environmentObject(settings)
                .navigationTitle("羽毛球抢票")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItemGroup(placement: .topBarLeading) {
                        Button(action: { commandCenter.previousTab() }) {
                            Image(systemName: "chevron.left")
                        }
                        Button(action: { commandCenter.nextTab() }) {
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
