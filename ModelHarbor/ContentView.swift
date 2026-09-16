import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage("harbor.focusedProvider") private var focusedProvider = "baseten"
    @AppStorage("harbor.menuBarDisplay") private var menuBarDisplay = MenuBarDisplay.connection.rawValue
    @AppStorage("harbor.showMenuBarIcon") private var showMenuBarIcon = true
    @AppStorage("harbor.hiddenProviders") private var hiddenProviders = ""
    @AppStorage("harbor.appearance") private var appearance = "system"
    @State private var page = "connections"
    @State private var settingsTab = "Menu bar"
    @State private var bodyHeight: CGFloat = 380
    @State private var editorSession: EditorSession?
    @State private var isShowingOpenAIAccountWarning = false
    @State private var openRouterKey = ""
    @State private var verifiedOpenRouterKey = ""
    @State private var chosenModels: Set<String> = []
    @State private var modelSearch = ""
    @State private var modelPage = 0
    @State private var savingOpenRouter = false

    private var provider: ProviderDefinition { ProviderDefinition.named(focusedProvider) }
    private var service: CodexService? { store.data.services.first { $0.id == focusedProvider } }
    private var activity: ProviderActivity { store.providerActivity[focusedProvider] ?? ProviderActivity() }
    private var connected: Bool { store.providerConnected(focusedProvider) }
    private var visibleProviders: [ProviderDefinition] {
        let visible = ProviderDefinition.all.filter { !hiddenProviders.split(separator: ",").contains(Substring($0.id)) }
        return visible.isEmpty ? ProviderDefinition.all : visible
    }
    private var maximumBodyHeight: CGFloat { max(220, (NSScreen.main?.visibleFrame.height ?? 850) - 156) }
    private var title: String {
        if editorSession != nil { return editorSession!.title }
        switch page {
        case "settings": return "Settings"
        case "add": return "Add provider"
        case "openrouter": return "Connect OpenRouter"
        default: return "Model Harbor"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView(.vertical, showsIndicators: bodyHeight > maximumBodyHeight) {
                VStack(alignment: .leading, spacing: 16) {
                    if !store.storageReady {
                        Label("Saved accounts are locked", systemImage: "lock")
                        Button("Unlock saved accounts") { store.unlockAccounts() }.buttonStyle(.borderedProminent)
                    }
                    pageContent.disabled(!store.storageReady)
                    if !store.errorMessage.isEmpty {
                        Label(store.errorMessage, systemImage: "exclamationmark.circle")
                            .font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                    } else if !store.statusMessage.isEmpty {
                        Text(store.statusMessage).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .background(GeometryReader { proxy in Color.clear.preference(key: PanelHeightKey.self, value: proxy.size.height) })
            }
            .frame(height: min(bodyHeight, maximumBodyHeight))
            Divider()
            footer
        }
        .frame(width: 410)
        .fixedSize(horizontal: false, vertical: true)
        .background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(appearance == "dark" ? .dark : (appearance == "light" ? .light : nil))
        .onPreferenceChange(PanelHeightKey.self) { height in
            if abs(bodyHeight - height) > 1 { bodyHeight = height }
        }
        .background(PanelWindowSizer(height: min(bodyHeight, maximumBodyHeight) + 102))
        .onAppear {
            if !visibleProviders.contains(where: { $0.id == focusedProvider }) { focusedProvider = visibleProviders.first!.id }
            Task { await store.refreshConnectionStatus() }
        }
        .alert("Add Codex account", isPresented: $isShowingOpenAIAccountWarning) {
            Button("Cancel", role: .cancel) {}
            Button("Continue login") { store.addOpenAIAccount() }
        } message: {
            Text("Codex opens sign-in in your browser. Use a separate browser profile to keep your existing saved accounts signed in.")
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            if page != "connections" || editorSession != nil {
                Button { page = "connections"; editorSession = nil; store.clearError() } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.plain).frame(width: 24, height: 28).help("Back to connections")
            } else {
                Image("HarborMark").resizable().scaledToFit().frame(width: 30, height: 30)
            }
            Text(title).font(.system(size: 16, weight: .semibold))
            Spacer()
            if editorSession != nil {
                Button("Save") { saveEditorSession() }.buttonStyle(.borderedProminent)
            } else if page == "connections" {
                Button { store.clearError(); page = "settings" } label: { Image(systemName: "gearshape").font(.system(size: 16)) }
                    .buttonStyle(.borderless).frame(width: 28, height: 28).help("Settings").accessibilityLabel("Settings")
            }
        }.padding(.horizontal, 18).frame(height: 58)
    }

    @ViewBuilder private var pageContent: some View {
        if let editorSession {
            ServiceEditorView(title: editorSession.title, originalID: editorSession.originalID, form: Binding(
                get: { self.editorSession?.form ?? editorSession.form }, set: { self.editorSession?.form = $0 }))
        } else {
            switch page {
            case "settings": settings
            case "add": addProvider
            case "openrouter": openRouterSetup
            default: connections
            }
        }
    }

    private var connections: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 4) {
                ForEach(visibleProviders) { entry in
                    Button {
                        focusedProvider = entry.id
                        store.clearError()
                    } label: {
                        VStack(spacing: 7) {
                            Image(systemName: entry.symbol).font(.system(size: 18, weight: .medium)).frame(height: 20)
                            HStack(spacing: 4) {
                                Text(entry.name).font(.system(size: 10, weight: .medium))
                                Circle().fill(store.providerConnected(entry.id) ? Color.green : Color.secondary.opacity(0.55)).frame(width: 5, height: 5)
                            }
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 10)
                        .foregroundStyle(focusedProvider == entry.id ? Color.primary : Color.secondary)
                        .background(focusedProvider == entry.id ? Color.accentColor.opacity(0.20) : Color.clear, in: RoundedRectangle(cornerRadius: 9))
                        .contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityLabel("\(entry.name), \(store.providerConnectionLabel(entry.id))")
                }
            }
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(provider.name).font(.system(size: 22, weight: .semibold))
                    Text(connectionDetail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Label(connectionTitle, systemImage: connected ? "checkmark.circle.fill" : "circle.dashed")
                    .font(.system(size: 12, weight: .medium)).foregroundStyle(connected ? Color.green : Color.secondary)
                    .padding(.top, 5)
            }
            activityView
            Divider()
            if let service, !service.models.isEmpty {
                HStack { Text("Models").font(.system(size: 12, weight: .semibold)); Spacer(); Text("\(service.models.count) available").font(.caption).foregroundStyle(.secondary) }
                VStack(spacing: 0) {
                    ForEach(service.models) { model in
                        HStack(spacing: 9) {
                            Image(systemName: "cube").font(.system(size: 12)).foregroundStyle(.secondary)
                            Text(displayName(model)).font(.system(size: 13)).lineLimit(2)
                            Spacer(minLength: 8)
                            Text(effortLabel(model)).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                        }.padding(.vertical, 7).accessibilityElement(children: .combine)
                    }
                }
                Text("Choose a model in each Codex task. Each task keeps its choice.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Connect \(provider.name) to make its models available in Codex.")
                    .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                connectionAction
                Spacer()
                Button { store.clearError(); page = "add" } label: { Label("Add provider", systemImage: "plus") }.buttonStyle(.bordered)
            }
        }
    }

    private var connectionTitle: String {
        if store.proxyStatus != .active { return "Offline" }
        if activity.active > 0 { return "Working" }
        return store.providerConnectionLabel(focusedProvider)
    }
    private var connectionDetail: String {
        if focusedProvider == "baseten" {
            return connected ? "Direct API · Credential ready" : (store.basetenState == "needs_reconnect" ? "Direct API · Reconnect required" : "Direct API · Unlock once for this session")
        }
        if focusedProvider == "grok-oauth" && store.grokIsSignedIn { return "Browser sign-in · \(store.grokAccount)" }
        if focusedProvider == "openrouter" { return connected ? "API key · Saved in Keychain" : "One connection, multiple model providers" }
        return "Uses the account signed in to Codex"
    }
    private var activityView: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 7) {
                Image(systemName: activity.active > 0 ? "waveform" : "clock").foregroundStyle(.secondary)
                if activity.active > 0 {
                    Text("\(activity.active) request\(activity.active == 1 ? "" : "s") in progress").font(.caption)
                } else if let failure = activity.lastFailure, failure > (activity.lastSuccess ?? .distantPast) {
                    Text(activity.httpStatus.flatMap { $0 >= 400 ? "Last request returned HTTP \($0)" : nil } ?? "Last request did not complete").font(.caption).foregroundStyle(.orange)
                } else if let date = activity.lastSuccess {
                    Text("Last response \(date, style: .relative) ago").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(focusedProvider == "codex-subscription" && store.codexConfigured ? "No completed request this session" : (connected ? "Ready for the next request" : "No connection established")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if activity.completed > 0 { Text("\(activity.completed) completed").font(.caption).monospacedDigit().foregroundStyle(.secondary).help("Completed requests since Harbor started") }
            }
            if activity.active > 0 && !activity.model.isEmpty {
                Text(activity.model).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
        }
    }
    @ViewBuilder private var connectionAction: some View {
        switch focusedProvider {
        case "baseten":
            if connected {
                Menu("Manage connection") {
                    Button("Open Baseten dashboard") { open(provider.dashboard) }
                    Button("Reconnect credentials…") { store.reconnectBaseten() }
                }.fixedSize()
            } else { Button(store.isBasetenReconnectRunning ? "Connecting…" : "Connect Baseten") { store.reconnectBaseten() }.disabled(store.isBasetenReconnectRunning || store.proxyStatus != .active).buttonStyle(.borderedProminent) }
        case "grok-oauth":
            Button(store.isGrokLoginRunning ? "Signing in…" : (connected ? "Switch account…" : "Sign in to Grok")) { store.signInGrok() }.disabled(store.isGrokLoginRunning).buttonStyle(.bordered)
        case "openrouter":
            if connected {
                Menu("Manage connection") {
                    Button("Choose models…") { beginOpenRouter() }
                    Button("Open OpenRouter dashboard") { open(provider.dashboard) }
                    Button("Disconnect") { Task { await store.disconnectOpenRouter() } }
                }.fixedSize()
            } else { Button("Connect OpenRouter") { beginOpenRouter() }.buttonStyle(.bordered) }
        default: Button("Manage accounts…") { settingsTab = "Providers"; page = "settings" }.buttonStyle(.bordered)
        }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 18) {
            Picker("Settings section", selection: $settingsTab) { ForEach(["Menu bar", "Providers", "Advanced"], id: \.self) { Text($0) } }.pickerStyle(.segmented).labelsHidden()
            if settingsTab == "Menu bar" {
                Text("Make the menu bar useful").font(.headline)
                Picker("Display", selection: $menuBarDisplay) { ForEach(MenuBarDisplay.allCases) { Text($0.title).tag($0.rawValue) } }
                Toggle("Show Harbor icon", isOn: $showMenuBarIcon).disabled(menuBarDisplay == "icon")
                Text("Connection follows the provider selected here. Activity follows the most recent request across your tasks.").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Divider()
                Picker("Appearance", selection: $appearance) { Text("System").tag("system"); Text("Light").tag("light"); Text("Dark").tag("dark") }
                Text("Visible providers").font(.headline)
                ForEach(ProviderDefinition.all) { entry in
                    Toggle(entry.name, isOn: Binding(get: { !hiddenProviders.split(separator: ",").contains(Substring(entry.id)) }, set: { visible in
                        var hidden = Set(hiddenProviders.split(separator: ",").map(String.init))
                        if visible { hidden.remove(entry.id) } else if hidden.count < ProviderDefinition.all.count - 1 { hidden.insert(entry.id) }
                        hiddenProviders = hidden.sorted().joined(separator: ",")
                        if hidden.contains(focusedProvider) { focusedProvider = ProviderDefinition.all.first { !hidden.contains($0.id) }!.id }
                    }))
                }
            } else if settingsTab == "Providers" {
                ForEach(ProviderDefinition.all) { entry in
                    HStack {
                        Image(systemName: entry.symbol).frame(width: 22)
                        VStack(alignment: .leading, spacing: 3) { Text(entry.name).font(.subheadline.weight(.medium)); Text(entry.method).font(.caption).foregroundStyle(.secondary) }
                        Spacer()
                        Button(store.providerConnected(entry.id) ? "Manage" : "Connect") { focusedProvider = entry.id; if entry.id == "openrouter" { beginOpenRouter() } else { page = "connections" } }
                    }
                }
                Divider()
                Text("Saved Codex accounts").font(.headline)
                if let accounts = store.data.services.first(where: { $0.id == "openai" }) { legacySection(accounts) }
                let custom = store.data.services.filter { !LiveRouting.supports($0.id) && $0.id != "openai" }
                ForEach(custom) { legacySection($0) }
                Button("Add custom provider…") { editorSession = EditorSession(title: "Custom provider", originalID: nil, form: ServiceFormData()) }
            } else {
                Text("Task routing").font(.headline)
                Toggle("Repair inactive task routes automatically", isOn: Binding(get: { store.taskRepairsEnabled }, set: store.setTaskRepairsEnabled))
                    .disabled(store.savingTaskRepairs || store.proxyStatus != .active)
                Text(store.pendingTaskRepairs > 0 ? "\(store.pendingTaskRepairs) routes are waiting for their tasks to become inactive." : (store.taskRepairState == "error" ? "Repair needs attention. Toggle repair off and on to retry." : "Model choices stay with each task."))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Divider()
                Text("New task default").font(.headline)
                Menu(store.selectedModel?.name ?? "Choose model") {
                    ForEach(store.data.services.filter { LiveRouting.supports($0.id) }) { service in
                        Section(LiveRouting.providerName(service.id)) { ForEach(service.models) { model in Button(model.name) { store.select(serviceID: service.id, modelID: model.id) } } }
                    }
                }
                Text("Applies to new tasks. Existing tasks keep their selected model.").font(.caption).foregroundStyle(.secondary)
                Button("Open Codex configuration") { NSWorkspace.shared.open(AppPaths.codexConfig) }
                Divider()
                Text("Model Harbor").font(.headline)
                Text("Open source, maintained by Andrew Napier.").font(.caption).foregroundStyle(.secondary)
                HStack { Button("Source & documentation") { open("https://github.com/napiermd/model-harbor") }; Spacer(); Button("Quit Harbor") { NSApplication.shared.terminate(nil) } }
            }
        }.controlSize(.regular)
    }

    private var addProvider: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Your models, your connections").font(.headline)
            Text("Connect a supported provider, or configure a custom Responses endpoint.").font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            ForEach(ProviderDefinition.all) { entry in
                HStack(spacing: 12) {
                    Image(systemName: entry.symbol).font(.title3).frame(width: 26)
                    VStack(alignment: .leading, spacing: 3) { Text(entry.name).font(.subheadline.weight(.semibold)); Text(entry.method).font(.caption).foregroundStyle(.secondary) }
                    Spacer()
                    Button(store.providerConnected(entry.id) ? "Manage" : "Connect") {
                        focusedProvider = entry.id
                        if entry.id == "openrouter" { beginOpenRouter() } else { page = "connections" }
                    }
                }
                Divider()
            }
            Button("Custom provider…") { editorSession = EditorSession(title: "Custom provider", originalID: nil, form: ServiceFormData()) }
            Text("Custom endpoints use a direct Codex connection. Switching to one may require reopening Codex.").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var filteredModels: [OpenRouterModel] {
        store.openRouterModels.filter { modelSearch.isEmpty || $0.name.localizedCaseInsensitiveContains(modelSearch) || $0.id.localizedCaseInsensitiveContains(modelSearch) }
    }
    private var openRouterSetup: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Connect with an OpenRouter API key").font(.headline)
            Text("The key is stored in macOS Keychain. Requests use your OpenRouter account and its billing.").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            SecureField("OpenRouter API key", text: $openRouterKey).textFieldStyle(.roundedBorder)
                .onChange(of: openRouterKey) { _ in verifiedOpenRouterKey = "" }
            HStack {
                Button(store.connectingOpenRouter ? "Checking…" : "Verify key & load models") {
                    Task { if await store.fetchOpenRouterModels(key: openRouterKey) { verifiedOpenRouterKey = openRouterKey; modelPage = 0 } }
                }.disabled(openRouterKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.connectingOpenRouter).buttonStyle(.borderedProminent)
                Spacer()
                Button("Get a key ↗") { open("https://openrouter.ai/settings/keys") }.buttonStyle(.link)
            }
            if !verifiedOpenRouterKey.isEmpty && verifiedOpenRouterKey == openRouterKey {
                Divider()
                HStack { Text("Choose models").font(.headline); Spacer(); Text("\(chosenModels.count) selected").font(.caption).foregroundStyle(.secondary) }
                TextField("Search tool-capable models", text: $modelSearch).textFieldStyle(.roundedBorder).onChange(of: modelSearch) { _ in modelPage = 0 }
                ForEach(Array(filteredModels.dropFirst(modelPage * 7).prefix(7))) { model in
                    Toggle(isOn: Binding(get: { chosenModels.contains(model.id) }, set: { if $0 { chosenModels.insert(model.id) } else { chosenModels.remove(model.id) } })) {
                        VStack(alignment: .leading, spacing: 2) { Text(model.name).font(.caption).lineLimit(1); Text(model.id).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1) }
                    }.toggleStyle(.checkbox)
                }
                if filteredModels.isEmpty { Text("No matching models.").font(.caption).foregroundStyle(.secondary) }
                HStack {
                    Button("Previous") { modelPage -= 1 }.disabled(modelPage == 0)
                    Spacer()
                    Text("\(filteredModels.count) matches").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Next") { modelPage += 1 }.disabled((modelPage + 1) * 7 >= filteredModels.count)
                }
                Button(savingOpenRouter ? "Saving…" : "Save connection") {
                    savingOpenRouter = true
                    Task {
                        if await store.connectOpenRouter(key: openRouterKey, models: chosenModels) { page = "connections"; focusedProvider = "openrouter"; openRouterKey = ""; verifiedOpenRouterKey = "" }
                        savingOpenRouter = false
                    }
                }.buttonStyle(.borderedProminent).disabled(chosenModels.isEmpty || savingOpenRouter || store.proxyStatus != .active)
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 6) {
            Circle().fill(store.proxyStatus == .active ? Color.green : Color.secondary).frame(width: 6, height: 6)
            Text(store.proxyStatus == .active ? "Bridge online" : "Bridge offline")
            Spacer()
            if page == "connections" { Text("\(ProviderDefinition.all.filter { store.providerConnected($0.id) }.count) connections") }
            else { Text("Model Harbor") }
        }.font(.system(size: 11)).foregroundStyle(.secondary).padding(.horizontal, 18).frame(height: 42)
    }
    private func beginOpenRouter() {
        store.clearError(); page = "openrouter"; verifiedOpenRouterKey = ""; modelSearch = ""; modelPage = 0
        let service = store.data.services.first { $0.id == "openrouter" }
        openRouterKey = service?.apiKey ?? ((try? CredentialStore.read("provider:openrouter")) ?? "")
        chosenModels = Set(service?.models.map(\.id) ?? [])
    }
    private func displayName(_ model: CodexModel) -> String {
        model.name.replacingOccurrences(of: "moonshotai/", with: "").replacingOccurrences(of: "deepseek-ai/", with: "").replacingOccurrences(of: "zai-org/", with: "").replacingOccurrences(of: "-", with: " ")
    }
    private func effortLabel(_ model: CodexModel) -> String {
        guard focusedProvider == "baseten" else { return "" }
        return ["moonshotai/Kimi-K3", "zai-org/GLM-5.3", "deepseek-ai/DeepSeek-V4-Pro-0813"].contains(model.id) ? "xhigh" : (model.id == "moonshotai/Kimi-K2.7-Code" ? "thinking" : "high")
    }
    private func open(_ url: String) { if let url = URL(string: url) { NSWorkspace.shared.open(url) } }
    private func legacySection(_ service: CodexService) -> some View {
        ServiceSectionView(service: service, onAddOpenAIAccount: { isShowingOpenAIAccountWarning = true }, onEdit: {
            store.clearError(); editorSession = EditorSession(title: "Edit provider", originalID: service.id, form: ServiceFormData(service: service))
        }).environmentObject(store)
    }
    private func saveEditorSession() {
        guard let editorSession else { return }
        store.saveService(originalID: editorSession.originalID, form: editorSession.form)
        if store.errorMessage.isEmpty { self.editorSession = nil; page = "settings"; settingsTab = "Providers" }
    }
}

private struct PanelHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

private struct PanelWindowSizer: NSViewRepresentable {
    var height: CGFloat
    func makeNSView(context: Context) -> SizingView { SizingView() }
    func updateNSView(_ view: SizingView, context: Context) { view.targetHeight = height; view.resizeWindow() }
    final class SizingView: NSView {
        var targetHeight: CGFloat = 480
        override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); resizeWindow() }
        func resizeWindow() {
            DispatchQueue.main.async { [weak self] in
                guard let self, let window = self.window, window.title == "Model Harbor" else { return }
                let screen = window.screen ?? NSScreen.main
                let target = min(self.targetHeight, (screen?.visibleFrame.height ?? 850) - 60)
                guard abs(window.contentLayoutRect.height - target) > 1 else { return }
                let top = window.frame.maxY
                window.setContentSize(NSSize(width: 410, height: target))
                var frame = window.frame
                frame.origin.y = top - frame.height
                if let visible = screen?.visibleFrame { frame.origin.y = max(visible.minY, frame.origin.y) }
                window.setFrame(frame, display: true)
            }
        }
    }
}

private struct EditorSession: Identifiable {
    let id = UUID()
    let title: String
    let originalID: String?
    var form: ServiceFormData
}

private struct ServiceSectionView: View {
    @EnvironmentObject private var store: AppStore
    let service: CodexService
    let onAddOpenAIAccount: () -> Void
    let onEdit: () -> Void
    @State private var isHovering = false
    @State private var isOpenAIAccountDropdownOpen = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if service.id == "openai" {
                    openAIAccountMenu
                } else {
                    Text(service.name)
                        .font(.system(.subheadline, design: .rounded).weight(.semibold))
                }

                Spacer()

                if service.id == "openai" {
                    openAIAccountActions
                } else if service.id != "codex-subscription" {
                    providerActions
                }
            }

            if LiveRouting.supports(service.id) {
                DisclosureGroup("\(service.models.count) models available") {
                    ForEach(service.models) { model in
                        Text(model.name).font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 2)
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
            } else {
            VStack(spacing: 3) {
                ForEach(service.models) { model in
                    Button {
                        store.select(serviceID: service.id, modelID: model.id)
                    } label: {
                        HStack {
                            Image(systemName: isSelected(model) ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(isSelected(model) ? Color.accentColor : Color.secondary)
                            Text(model.name)
                                .font(.body)
                                .lineLimit(1)
                            Spacer()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled((service.id == "xai" || LiveRouting.supports(service.id)) && store.proxyStatus != .active)
                    .padding(.vertical, 2)
                    .padding(.horizontal, 6)
                    .background(isSelected(model) ? Color.accentColor.opacity(0.14) : Color.clear)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                }
            }

            }

            if service.id == "codex-subscription" {
                Text("Uses the subscription signed in to Codex.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if service.id == "openai" {
                Text("Switching saved accounts changes the direct connection and requires restarting Codex. Use Current subscription for model changes while Codex stays open.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if service.id == "baseten" {
                Label(store.basetenState == "ready" ? "Unlocked for this session" : (store.basetenState == "needs_reconnect" ? "Reconnect to continue" : "Unlock once per Harbor session"),
                      systemImage: store.basetenState == "ready" ? "checkmark.circle.fill" : "key")
                    .font(.caption).foregroundStyle(.secondary)
                Button(store.isBasetenReconnectRunning ? "Connecting…" : "Reconnect Baseten") {
                    store.reconnectBaseten()
                }
                .disabled(store.isBasetenReconnectRunning || store.proxyStatus != .active)
                .help("Read the current key from 1Password again after a key change or canceled unlock.")
            } else if service.id == "grok-oauth" {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        if store.grokIsSignedIn {
                            Label("Signed in", systemImage: "checkmark.circle.fill")
                                .font(.caption).foregroundStyle(.tint)
                        }
                        Text(store.grokAccount).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(store.isGrokLoginRunning ? "Signing in…" : (store.grokIsSignedIn ? "Switch account" : "Sign in")) { store.signInGrok() }
                        .disabled(store.isGrokLoginRunning)
                }
                Text("Uses your Grok browser sign-in.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if service.id == "xai" {
                Text("Grok uses xAI API billing. Keep this switcher open while using Grok.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if service.requiresAPIKey && service.apiKey.isEmpty {
                Label("API key missing", systemImage: "key.slash")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 7)
        .contentShape(Rectangle())
        .onHover { hovering in
            isHovering = hovering
        }
    }

    private func isSelected(_ model: CodexModel) -> Bool {
        store.data.selectedModel == SelectedModel(serviceID: service.id, modelID: model.id)
    }

    private var providerActions: some View {
        HStack(spacing: 4) {
            Button {
                onEdit()
            } label: {
                Image(systemName: "pencil")
                    .font(.system(size: 12, weight: .medium))
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .help("Edit service")
            .opacity(isHovering ? 1 : 0)
            .allowsHitTesting(isHovering)

            Button(role: .destructive) {
                store.deleteService(service)
            } label: {
                Image(systemName: "trash")
                    .font(.system(size: 12, weight: .medium))
                    .frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .help("Delete service")
            .disabled(store.data.services.count == 1)
            .opacity(isHovering ? 1 : 0)
            .allowsHitTesting(isHovering)
        }
    }

    private var openAIAccountActions: some View {
        HStack(spacing: 4) {
            Menu {
                Button("Import accounts from Codex Switcher") { store.importExistingAccounts() }
                Button("Save current Codex account") { store.importCurrentOpenAIAccount() }
            } label: {
                Image(systemName: "square.and.arrow.down")
            }
            .help("Import saved Codex accounts into Keychain")
            if store.isOpenAIAccountLoginRunning {
                ProgressView()
                    .controlSize(.small)
                    .scaleEffect(0.65)
                    .frame(width: 22, height: 22)
            } else {
                Button {
                    onAddOpenAIAccount()
                } label: {
                    Image(systemName: "person.crop.circle.badge.plus")
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 22, height: 22)
                }
                .buttonStyle(.borderless)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .help("Add OpenAI account")
            }

            if let selectedAccount = selectedAccount {
                Button(role: .destructive) {
                    store.deleteOpenAIAccount(selectedAccount)
                } label: {
                    Image(systemName: "minus.circle")
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 22, height: 22)
                }
                .buttonStyle(.borderless)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .help("Delete saved OpenAI account")
            }
        }
    }

    private var openAIAccountMenu: some View {
        VStack(alignment: .leading, spacing: 2) {
            Button {
                isOpenAIAccountDropdownOpen.toggle()
            } label: {
                HStack(spacing: 5) {
                    Text("Codex")
                        .font(.system(.subheadline, design: .rounded).weight(.semibold))
                    Text(selectedOpenAIAccountName)
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .popover(isPresented: $isOpenAIAccountDropdownOpen, arrowEdge: .top) {
                openAIAccountDropdown
            }
            .onChange(of: isOpenAIAccountDropdownOpen) { isOpen in
                if isOpen {
                    store.checkOpenAIAccounts()
                }
            }

            if let selectedAccount = selectedAccount,
               isOpenAIAccountInvalid(selectedAccount) {
                Text(selectedAccount.credentialMessage ?? invalidOpenAIAccountMessage)
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
        }
    }

    private var openAIAccountDropdown: some View {
        VStack(alignment: .leading, spacing: 4) {
            if store.data.openAIAccounts.isEmpty {
                Text("Using default login")
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
            } else {
                ForEach(store.data.openAIAccounts) { account in
                    Button {
                        store.selectOpenAIAccount(account.id)
                        isOpenAIAccountDropdownOpen = false
                    } label: {
                        accountMenuLabel(account)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(6)
        .frame(width: 280, alignment: .leading)
        .onAppear {
            store.checkOpenAIAccounts()
        }
    }

    private var selectedAccount: OpenAIAccount? {
        guard let accountID = store.data.selectedOpenAIAccountID else {
            return nil
        }
        return store.data.openAIAccounts.first { $0.id == accountID }
    }

    private var selectedOpenAIAccountName: String {
        selectedAccount?.displayName ?? "default"
    }

    private func accountMenuLabel(_ account: OpenAIAccount) -> some View {
        HStack(alignment: .center, spacing: 8) {
            accountCredentialIcon(account)

            VStack(alignment: .leading, spacing: 1) {
                Text(account.displayName)
                    .font(.body)
                    .foregroundStyle(.primary)
                    .lineLimit(1)

                if let email = account.email, email != account.displayName {
                    Text(email)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if isOpenAIAccountInvalid(account) {
                    Text(account.credentialMessage ?? invalidOpenAIAccountMessage)
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .lineLimit(2)
                }
            }

            Spacer()

            if store.data.selectedOpenAIAccountID == account.id {
                Image(systemName: "checkmark")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.blue)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private func accountCredentialIcon(_ account: OpenAIAccount) -> some View {
        if store.checkingOpenAIAccountIDs.contains(account.id) {
            ProgressView()
                .controlSize(.small)
                .frame(width: 16, height: 16)
        } else {
            if isOpenAIAccountInvalid(account) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.orange)
                    .frame(width: 16, height: 16)
            } else {
                Color.clear
                    .frame(width: 16, height: 16)
            }
        }
    }

    private func isOpenAIAccountInvalid(_ account: OpenAIAccount) -> Bool {
        !store.checkingOpenAIAccountIDs.contains(account.id) && account.credentialStatus == .invalid
    }

    private var invalidOpenAIAccountMessage: String {
        "Not valid. Please re-login."
    }
}

struct ServiceEditorView: View {
    let title: String
    let originalID: String?
    @Binding var form: ServiceFormData

    init(
        title: String,
        originalID: String?,
        form: Binding<ServiceFormData>
    ) {
        self.title = title
        self.originalID = originalID
        _form = form
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 10) {
                fieldRow("Name", text: $form.name, prompt: "OpenRouter")
                fieldRow("Base URL", text: $form.baseURL, prompt: "https://api.example.com/v1")
                fieldRow("Env key", text: $form.envKey, prompt: "EXAMPLE_API_KEY")

                GridRow {
                    Text("Proxy")
                        .foregroundStyle(.secondary)
                    Text("Unavailable pending compatibility checks")
                        .font(.caption)
                }

                GridRow(alignment: .top) {
                    Text("API key")
                        .foregroundStyle(.secondary)
                        .padding(.top, 5)
                    SecureField("Provider API key", text: $form.apiKey)
                }

                GridRow(alignment: .top) {
                    Text("Models")
                        .foregroundStyle(.secondary)
                        .padding(.top, 5)
                    TextEditor(text: $form.modelsText)
                        .font(.system(.body, design: .monospaced))
                        .frame(height: 120)
                        .scrollContentBackground(.hidden)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay {
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(.white.opacity(0.16), lineWidth: 1)
                        }
                }
            }

            Text("One model ID per line. New API keys are saved in macOS Keychain. Linked providers keep their existing authentication.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func fieldRow(_ label: String, text: Binding<String>, prompt: String) -> some View {
        GridRow {
            Text(label)
                .foregroundStyle(.secondary)
            TextField(prompt, text: text)
                .textFieldStyle(.roundedBorder)
        }
    }
}

/// Ellipsis button that presents an AppKit menu (no disclosure chevron).
private struct FooterOptionsButton: NSViewRepresentable {
    let selectedReasoningEffort: ReasoningEffort
    let onSelectReasoningEffort: (ReasoningEffort) -> Void
    let onOpenConfig: () -> Void
    let onQuit: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(
            onSelectReasoningEffort: onSelectReasoningEffort,
            onOpenConfig: onOpenConfig,
            onQuit: onQuit
        )
    }

    func makeNSView(context: Context) -> NSButton {
        let button = NSButton(frame: .zero)
        button.bezelStyle = .inline
        button.isBordered = false
        button.image = NSImage(
            systemSymbolName: "ellipsis",
            accessibilityDescription: "Settings"
        )
        button.imagePosition = .imageOnly
        button.imageScaling = .scaleProportionallyDown
        button.contentTintColor = .secondaryLabelColor
        button.target = context.coordinator
        button.action = #selector(Coordinator.showMenu(_:))
        button.setButtonType(.momentaryChange)
        button.focusRingType = .none
        context.coordinator.button = button
        return button
    }

    func updateNSView(_ nsView: NSButton, context: Context) {
        context.coordinator.selectedReasoningEffort = selectedReasoningEffort
        context.coordinator.onSelectReasoningEffort = onSelectReasoningEffort
        context.coordinator.onOpenConfig = onOpenConfig
        context.coordinator.onQuit = onQuit
    }

    final class Coordinator: NSObject {
        var selectedReasoningEffort: ReasoningEffort = .medium
        var onSelectReasoningEffort: (ReasoningEffort) -> Void
        var onOpenConfig: () -> Void
        var onQuit: () -> Void
        weak var button: NSButton?

        init(
            onSelectReasoningEffort: @escaping (ReasoningEffort) -> Void,
            onOpenConfig: @escaping () -> Void,
            onQuit: @escaping () -> Void
        ) {
            self.onSelectReasoningEffort = onSelectReasoningEffort
            self.onOpenConfig = onOpenConfig
            self.onQuit = onQuit
        }

        @objc func showMenu(_ sender: NSButton) {
            let menu = NSMenu()
            menu.autoenablesItems = false

            let header = NSMenuItem(
                title: "New task reasoning effort",
                action: nil,
                keyEquivalent: ""
            )
            header.isEnabled = false
            menu.addItem(header)

            for effort in ReasoningEffort.allCases {
                let item = NSMenuItem(
                    title: effort.displayName,
                    action: #selector(selectReasoningEffort(_:)),
                    keyEquivalent: ""
                )
                item.target = self
                item.representedObject = effort.rawValue
                item.state = effort == selectedReasoningEffort ? .on : .off
                menu.addItem(item)
            }

            menu.addItem(.separator())

            let openConfig = NSMenuItem(
                title: "Open config.toml",
                action: #selector(openConfig),
                keyEquivalent: ""
            )
            openConfig.target = self
            menu.addItem(openConfig)

            let quit = NSMenuItem(
                title: "Quit",
                action: #selector(quitApp),
                keyEquivalent: ""
            )
            quit.target = self
            menu.addItem(quit)

            let point = NSPoint(x: sender.bounds.maxX, y: sender.bounds.minY)
            menu.popUp(positioning: nil, at: point, in: sender)
        }

        @objc private func selectReasoningEffort(_ sender: NSMenuItem) {
            guard let raw = sender.representedObject as? String,
                  let effort = ReasoningEffort(rawValue: raw) else {
                return
            }
            onSelectReasoningEffort(effort)
        }

        @objc private func openConfig() {
            onOpenConfig()
        }

        @objc private func quitApp() {
            onQuit()
        }
    }
}

#Preview {
    ContentView()
        .environmentObject(AppStore())
}
