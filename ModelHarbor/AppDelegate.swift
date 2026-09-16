import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var settingsWindow: NSWindow?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
        if ProcessInfo.processInfo.arguments.contains("--migrate-vault") {
            Task { @MainActor in await InstallationVerification.migrateVault() }
            return
        }
        if !ProcessInfo.processInfo.arguments.contains("--verify-accounts") {
            _ = AppStore.shared
            showSettings()
        }
        if ProcessInfo.processInfo.arguments.contains("--verify-accounts") {
            Task { @MainActor in await InstallationVerification.run() }
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSettings()
        return true
    }

    private func showSettings() {
        if settingsWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 390, height: 470),
                styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            window.title = "Model Harbor"
            window.isReleasedWhenClosed = false
            window.contentViewController = NSHostingController(rootView: ContentView().environmentObject(AppStore.shared).background(.regularMaterial))
            window.center()
            settingsWindow = window
        }
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        if !ProcessInfo.processInfo.arguments.contains("--verify-accounts") { AppStore.shared.stopAdapter() }
    }
}
