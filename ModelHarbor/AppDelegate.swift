import AppKit
import Foundation
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let preferences = LifecyclePreferences.shared
    private let lifecycleController = WindowLifecycleController()
    private var mainWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        lifecycleController.applyPresence(preferences.presence)
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(openWindow(_:)),
            name: .harborOpenWindow,
            object: nil
        )

        if ProcessInfo.processInfo.arguments.contains("--migrate-vault") {
            Task { @MainActor in await InstallationVerification.migrateVault() }
            return
        }

        if !ProcessInfo.processInfo.arguments.contains("--verify-accounts") {
            _ = AppStore.shared
            if preferences.openWindowAtLaunch && !Self.wasLaunchedAsLoginItem {
                showMainWindow()
            }
        }
        if ProcessInfo.processInfo.arguments.contains("--verify-accounts") {
            Task { @MainActor in await InstallationVerification.run() }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationWillTerminate(_ notification: Notification) {
        if !ProcessInfo.processInfo.arguments.contains("--verify-accounts") {
            AppStore.shared.stopAdapter()
        }
    }

    @objc private func openWindow(_ notification: Notification) {
        showMainWindow()
        if notification.userInfo?["settings"] as? Bool == true {
            DispatchQueue.main.async { NotificationCenter.default.post(name: .harborShowSettings, object: nil) }
        }
    }

    private func showMainWindow() {
        if mainWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 410, height: 480),
                styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            window.title = "Model Harbor"
            window.isReleasedWhenClosed = false
            window.contentViewController = NSHostingController(
                rootView: ContentView()
                    .environmentObject(AppStore.shared)
                    .environmentObject(preferences)
                    .background(.regularMaterial)
            )
            window.center()
            lifecycleController.attach(to: window)
            mainWindow = window
        }
        lifecycleController.applyPresence(preferences.presence)
        if mainWindow?.isMiniaturized == true { mainWindow?.deminiaturize(nil) }
        mainWindow?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private static var wasLaunchedAsLoginItem: Bool {
        HarborLaunchEvent.isLoginItem(NSAppleEventManager.shared().currentAppleEvent)
    }
}
