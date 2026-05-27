import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var showFileImporter = false
    @State private var importedRequestFile: String?
    @State private var showAlert = false
    @State private var alertMessage = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("运行模式") {
                    Picker("模式", selection: $settings.mode) {
                        ForEach(AppMode.allCases, id: \.self) { mode in
                            Text(mode.displayName).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)

                    if settings.mode == .remote {
                        Text("连接远程 Python 后端，所有抢票逻辑在服务器执行")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        Text("使用本地 Swift 引擎执行抢票，无需服务器")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if settings.mode == .remote {
                    Section("服务器配置") {
                        TextField("服务器地址", text: $settings.serverURL)
                            .keyboardType(.URL)
                            .textContentType(.URL)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                        Text("例如: http://192.168.1.100:8765")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if settings.mode == .local {
                    Section("请求模板") {
                        Button("导入 request.txt") {
                            showFileImporter = true
                        }
                        if let file = importedRequestFile {
                            Text("已导入: \(file)")
                                .font(.caption)
                                .foregroundStyle(.green)
                        }
                        Text("从抓包工具导出的请求文件，用于构建抢票请求")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("客户端信息") {
                    LabeledContent("Client ID", value: String(settings.clientId.prefix(8)) + "...")
                }
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .fileImporter(isPresented: $showFileImporter, allowedContentTypes: [.plainText]) { result in
                handleFileImport(result)
            }
            .alert("提示", isPresented: $showAlert) {
                Button("确定") {}
            } message: {
                Text(alertMessage)
            }
        }
    }

    private func handleFileImport(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            guard url.startAccessingSecurityScopedResource() else {
                alertMessage = "无法访问文件"
                showAlert = true
                return
            }
            defer { url.stopAccessingSecurityScopedResource() }
            do {
                let data = try Data(contentsOf: url)
                let docsDir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
                let dest = docsDir.appendingPathComponent("request.txt")
                try data.write(to: dest)
                importedRequestFile = url.lastPathComponent
                alertMessage = "导入成功"
                showAlert = true
            } catch {
                alertMessage = "导入失败: \(error.localizedDescription)"
                showAlert = true
            }
        case .failure(let error):
            alertMessage = "选择文件失败: \(error.localizedDescription)"
            showAlert = true
        }
    }
}
