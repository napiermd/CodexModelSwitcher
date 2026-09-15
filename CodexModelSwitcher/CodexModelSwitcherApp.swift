//
//  CodexModelSwitcherApp.swift
//  CodexModelSwitcher
//
//  Created by Hieu on 20/6/26.
//

import AppKit
import SwiftUI

@main
struct CodexModelSwitcherApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore.shared

    var body: some Scene {
        MenuBarExtra {
            ContentView()
                .environmentObject(store)

        } label: {
            Label("Models", systemImage: "arrow.triangle.2.circlepath")
        }
        .menuBarExtraStyle(.window)
    }
}
