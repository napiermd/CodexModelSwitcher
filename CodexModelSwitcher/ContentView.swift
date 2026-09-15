import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore
    @State private var editorSession: EditorSession?
    @State private var isShowingOpenAIAccountWarning = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
            Divider()
            bodySection
                .disabled(!store.storageReady)
            if editorSession == nil {
                Divider()
                footer
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
            }
        }
        .frame(width: panelWidth)
        .frame(maxHeight: maxPanelHeight, alignment: .top)
        .background {
            WindowTransparencyConfigurator()
                .allowsHitTesting(false)
        }
        .onDisappear {
            store.clearStatusMessage()
        }
        .alert("Add OpenAI account", isPresented: $isShowingOpenAIAccountWarning) {
            Button("Cancel", role: .cancel) {}
            Button("Continue Login") {
                store.addOpenAIAccount()
            }
        } message: {
            Text("Codex will open login in your browser. If you log out of an existing ChatGPT account during this flow, that saved account may stop working. To keep saved accounts valid, use a separate browser profile or private browser for the new account.")
        }
    }

    private var panelWidth: CGFloat {
        390
    }

    private var maxPanelHeight: CGFloat {
        ((NSScreen.main?.visibleFrame.height ?? 900) * 0.7).rounded(.down)
    }

    private var maxBodyHeight: CGFloat {
        editorSession == nil ? 360 : maxPanelHeight - 52
    }

    private var header: some View {
        HStack(spacing: 10) {
            if editorSession != nil {
                Button {
                    store.clearError()
                    editorSession = nil
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.borderless)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .help("Back")

                Text(editorSession?.title ?? "")
                    .font(.system(.headline, design: .rounded).weight(.semibold))
                    .lineLimit(1)

                Spacer()

                Button("Save") {
                    saveEditorSession()
                }
                .buttonStyle(.borderedProminent)
            } else {
                Image(systemName: "terminal")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.tint)
                    .frame(width: 34, height: 34)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 13, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: 13, style: .continuous)
                            .strokeBorder(.white.opacity(0.18), lineWidth: 1)
                    }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Codex Models")
                        .font(.system(.headline, design: .rounded).weight(.semibold))
                    Text(currentSelectionText)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Button {
                    store.clearError()
                    editorSession = EditorSession(
                        title: "Add Provider",
                        originalID: nil,
                        form: ServiceFormData()
                    )
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.borderless)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .help("Add service")
            }
        }
    }

    @ViewBuilder
    private var bodySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let editorSession {
                ViewThatFits(in: .vertical) {
                    editorView(for: editorSession)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                    ScrollView {
                        editorView(for: editorSession)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                    }
                }
            } else {
                serviceList
            }

            if !store.errorMessage.isEmpty {
                Text(store.errorMessage)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 12)
            } else if !store.statusMessage.isEmpty {
                Text(store.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 12)
            }
        }
        .frame(maxHeight: maxBodyHeight, alignment: .top)
    }

    private var serviceList: some View {
        ScrollView {
            serviceStack
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
        }
        .frame(height: 300)
    }

    private var serviceStack: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(store.data.services.enumerated()), id: \.element.id) { index, service in
                VStack(spacing: 0) {
                    ServiceSectionView(
                        service: service,
                        onAddOpenAIAccount: {
                            isShowingOpenAIAccountWarning = true
                        },
                        onEdit: {
                            store.clearError()
                            editorSession = EditorSession(
                                title: "Edit Service",
                                originalID: service.id,
                                form: ServiceFormData(service: service)
                            )
                        }
                    )
                    .environmentObject(store)

                    if index < store.data.services.count - 1 {
                        Divider()
                            .padding(.leading, 8)
                    }
                }
            }
        }
    }

    private func editorView(for editorSession: EditorSession) -> some View {
        ServiceEditorView(
            title: editorSession.title,
            originalID: editorSession.originalID,
            form: Binding(
                get: { self.editorSession?.form ?? editorSession.form },
                set: { self.editorSession?.form = $0 }
            )
        )
        .id(editorSession.id)
    }

    private func saveEditorSession() {
        guard let editorSession else { return }
        store.saveService(originalID: editorSession.originalID, form: editorSession.form)
        if store.errorMessage.isEmpty {
            self.editorSession = nil
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            proxyStatusView
                .frame(maxWidth: .infinity, alignment: .leading)

            FooterOptionsButton(
                selectedReasoningEffort: store.data.modelReasoningEffort,
                onSelectReasoningEffort: { store.setReasoningEffort($0) },
                onOpenConfig: { NSWorkspace.shared.open(AppPaths.codexConfig) },
                onQuit: { NSApplication.shared.terminate(nil) }
            )
            .frame(width: 22, height: 22)
            .help("Settings")
        }
        .font(.caption)
    }

    private var proxyStatusView: some View {
        HStack(spacing: 4) {
            switch store.proxyStatus {
            case .starting:
                ProgressView()
                    .controlSize(.small)
                    .scaleEffect(0.55)
                    .frame(width: 10, height: 10)
            case .notRunning:
                Circle()
                    .fill(.secondary.opacity(0.5))
                    .frame(width: 10, height: 10)
            case .active:
                Circle()
                    .fill(.green)
                    .frame(width: 10, height: 10)
            case .error:
                Circle()
                    .fill(.red)
                    .frame(width: 10, height: 10)
            }

            Text("Grok connection: \(proxyStatusText)")
                .foregroundStyle(.secondary)
        }
        .help(proxyStatusHelp)
    }

    private var proxyStatusText: String {
        switch store.proxyStatus {
        case .starting:
            return "Starting"
        case .notRunning:
            return "Not running"
        case .active:
            return "Active"
        case .error:
            return "Error"
        }
    }

    private var proxyStatusHelp: String {
        switch store.proxyStatus {
        case .starting:
            return "Grok connection is starting"
        case .notRunning:
            return "Grok connection is not running"
        case .active:
            return "Grok is ready. Keep this app open while using Grok."
        case .error:
            return "Grok connection failed. Reopen this app to retry."
        }
    }

    private var currentSelectionText: String {
        guard let service = store.selectedService, let model = store.selectedModel else {
            return "No model selected"
        }

        return "\(service.name) / \(model.name)"
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
                } else {
                    providerActions
                }
            }

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
                    .disabled(service.id == "xai" && store.proxyStatus != .active)
                    .padding(.vertical, 2)
                    .padding(.horizontal, 6)
                    .background(isSelected(model) ? Color.accentColor.opacity(0.14) : Color.clear)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                }
            }

            if service.id == "xai" {
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
                title: "Reasoning effort",
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
