import AppKit
import SwiftUI

@main
struct ModelHarborApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore.shared
    var body: some Scene {
        MenuBarExtra {
            ContentView().environmentObject(store)
        } label: {
            HarborMenuBarLabel().environmentObject(store)
        }.menuBarExtraStyle(.window)
    }
}

private struct HarborMenuBarLabel: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage("harbor.focusedProvider") private var focusedProvider = "baseten"
    @AppStorage("harbor.menuBarDisplay") private var display = MenuBarDisplay.connection.rawValue
    @AppStorage("harbor.showMenuBarIcon") private var showIcon = true
    private var providerID: String { display == "activity" && !store.lastProviderID.isEmpty ? store.lastProviderID : focusedProvider }
    private var provider: ProviderDefinition { ProviderDefinition.named(providerID) }
    private var activity: ProviderActivity { store.providerActivity[providerID] ?? ProviderActivity() }
    private var text: String {
        switch MenuBarDisplay(rawValue: display) ?? .connection {
        case .icon: return ""
        case .name: return "Harbor"
        case .count: return "\(ProviderDefinition.all.filter { store.providerConnected($0.id) }.count) connected"
        case .model: return store.lastRequestedModel.isEmpty ? "Harbor · Ready" : String(store.lastRequestedModel.split(separator: "/").last ?? "Harbor")
        case .activity: return "\(provider.name) · \(activity.active > 0 ? "\(activity.active) running" : "Idle")"
        case .connection: return "\(provider.name) · \(store.providerConnectionLabel(providerID))"
        }
    }
    var body: some View {
        HStack(spacing: 5) {
            if showIcon || display == "icon" { Image(systemName: activity.active > 0 ? "waveform" : "h.square").font(.system(size: 14, weight: .medium)) }
            if !text.isEmpty { Text(text).font(.system(size: 12)).lineLimit(1).truncationMode(.middle).frame(maxWidth: 220) }
        }.accessibilityLabel("Model Harbor. \(text.isEmpty ? "Open connections" : text)")
    }
}
