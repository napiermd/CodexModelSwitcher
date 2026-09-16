//
//  ModelHarborApp.swift
//  ModelHarbor
//
//  Created by Hieu on 20/6/26.
//

import AppKit
import SwiftUI

@main
struct ModelHarborApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore.shared

    var body: some Scene {
        MenuBarExtra {
            ContentView()
                .environmentObject(store)

        } label: {
            Label("Harbor", systemImage: "signpost.right.and.left")
        }
        .menuBarExtraStyle(.window)
    }
}
