import SwiftUI

struct ContentView: View {
    @EnvironmentObject var settings: AppSettings
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            BookingWebView()
                .environmentObject(settings)
                .navigationTitle("羽毛球抢票")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
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
