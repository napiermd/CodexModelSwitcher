import AppKit
import SwiftUI

struct GeneralSettingsView: View {
    @StateObject private var lifecycle = LifecyclePreferences.shared
    @StateObject private var loginItem = LoginItemController.shared
    @StateObject private var warmUp = WarmUpController.shared
    @AppStorage("harbor.appearance") private var appearance = "system"
    @State private var isWarmUpPromptExpanded = false

    private var dailyDate: Binding<Date> {
        Binding(
            get: {
                var components = DateComponents()
                components.hour = warmUp.dailyMinute / 60
                components.minute = warmUp.dailyMinute % 60
                return Calendar.current.date(from: components) ?? Date()
            },
            set: { newValue in
                let components = Calendar.current.dateComponents([.hour, .minute], from: newValue)
                warmUp.dailyMinute = (components.hour ?? 9) * 60 + (components.minute ?? 0)
            }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            windowSection
            Divider()
            startupSection
            Divider()
            appearanceSection
            Divider()
            warmUpSection
        }
        .controlSize(.regular)
        .onAppear { loginItem.refresh() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in loginItem.refresh() }
    }

    // MARK: Window

    private var windowSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Window").font(.headline)
            Picker("Show in", selection: $lifecycle.presence) {
                ForEach(HarborPresence.allCases) { Text($0.title).tag($0) }
            }
            Picker("On close", selection: $lifecycle.closeBehavior) {
                ForEach(HarborCloseBehavior.allCases) { Text($0.title).tag($0) }
            }
            Toggle("Open window when launched manually", isOn: $lifecycle.openWindowAtLaunch)
            Text("Closing the window keeps Harbor serving tasks. Quit from the menu bar or Dock.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Startup

    private var startupSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Startup").font(.headline)
            Toggle("Launch at login", isOn: Binding(
                get: { loginItem.isEnabled }, set: { loginItem.setEnabled($0) }
            )).disabled(loginItem.isChanging)
            if !loginItem.statusMessage.isEmpty {
                Text(loginItem.statusMessage).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !loginItem.errorMessage.isEmpty {
                Text(loginItem.errorMessage).font(.caption).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button("Open Login Items settings…") { loginItem.openSystemSettings() }
                .buttonStyle(.link).controlSize(.small)
            Text("Login launches run quietly in the menu bar. This setting does not reopen Codex.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Appearance

    private var appearanceSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Appearance").font(.headline)
            Picker("Appearance", selection: $appearance) {
                Text("System").tag("system")
                Text("Light").tag("light")
                Text("Dark").tag("dark")
            }
        }
    }

    // MARK: Warm-up

    private var warmUpSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Warm-up").font(.headline)
            Picker("Mode", selection: $warmUp.mode) {
                ForEach(WarmUpMode.allCases) { Text($0.title).tag($0) }
            }
            if warmUp.mode == .daily {
                DatePicker("Time", selection: dailyDate, displayedComponents: .hourAndMinute)
            }
            HStack(spacing: 10) {
                Button(warmUp.isRunning ? "Warming…" : "Warm up now") {
                    Task { await warmUp.warmNow() }
                }
                .disabled(warmUp.isRunning)
                .buttonStyle(.bordered)
                if !warmUp.statusMessage.isEmpty {
                    Text(warmUp.statusMessage).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            DisclosureGroup("Prompt", isExpanded: $isWarmUpPromptExpanded) {
                TextEditor(text: $warmUp.prompt)
                    .font(.system(.body, design: .monospaced))
                    .frame(height: 60)
                    .scrollContentBackground(.hidden)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            .font(.caption)
            Text("Sends one short low-effort request to the current Codex account. It uses subscription quota and does not raise rate limits.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

}

struct CodexRestartSettingsView: View {
    @StateObject private var lifecycle = LifecyclePreferences.shared
    @StateObject private var restart = CodexRestartController.shared
    // MARK: Codex restart

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Codex restart").font(.headline)
            Picker("Close method", selection: $lifecycle.codexCloseMethod) {
                ForEach(CodexCloseMethod.allCases) { Text($0.title).tag($0) }
            }
            Toggle("Reopen Codex after closing", isOn: $lifecycle.reopenCodex)
            if lifecycle.codexCloseMethod == .force {
                Label("Force close may discard unsaved work in Codex.", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            Button(restart.isRestarting ? "Working…" : (lifecycle.reopenCodex ? "Restart Codex…" : "Close Codex…")) {
                restart.requestRestart()
            }
            .disabled(restart.isRestarting)
            .buttonStyle(.bordered)
            if !restart.statusMessage.isEmpty {
                Text(restart.statusMessage).font(.caption).foregroundStyle(.secondary)
            }
            Text("Only needed for saved-account direct connection changes. Live task model changes do not require a restart.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

#if !DISABLE_PREVIEWS
#Preview {
    GeneralSettingsView()
}
#endif
