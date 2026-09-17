import SwiftUI

struct ModelsSettingsView: View {
    @EnvironmentObject private var store: AppStore
    var onAddModels: (String) -> Void

    private var visibleCount: Int {
        store.data.pickerServices.reduce(0) { $0 + store.data.visibleModels(in: $1).count }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Your Codex model list").font(.headline)
                Spacer()
                Text("\(visibleCount) shown").font(.caption).foregroundStyle(.secondary)
            }
            Text("Keep the models you use in the picker. Hidden models remain available to tasks already using them.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 7) {
                Text("New task default").font(.subheadline.weight(.medium))
                Menu(defaultName) {
                    ForEach(store.data.pickerServices) { service in
                        let models = store.data.visibleModels(in: service)
                        if !models.isEmpty {
                            Section(LiveRouting.providerName(service.id)) {
                                ForEach(models) { model in
                                    Button(model.name) { store.select(serviceID: service.id, modelID: model.id) }
                                }
                            }
                        }
                    }
                }
                Text("Existing tasks keep their own selection.").font(.caption).foregroundStyle(.secondary)
            }
            ForEach(store.data.pickerServices) { service in
                Divider()
                providerSection(service)
            }
            Divider()
            Label("Picker changes need one Codex reopen", systemImage: "arrow.clockwise")
                .font(.caption.weight(.medium))
            Text("Finish active tasks first. Once the list is loaded, switch between these models without restarting.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Button("Add another connection…") { onAddModels("") }
        }
    }

    private var defaultName: String {
        guard let selection = store.data.selectedModel else { return "Choose model" }
        return "\(store.selectedModel?.name ?? selection.modelID) · \(LiveRouting.providerName(selection.serviceID))"
    }

    private func providerSection(_ service: CodexService) -> some View {
        let enabled = !store.data.modelPicker.hiddenProviders.contains(service.id)
        let containsDefault = store.data.selectedModel?.serviceID == service.id
        return VStack(alignment: .leading, spacing: 8) {
            Toggle(isOn: Binding(get: { !store.data.modelPicker.hiddenProviders.contains(service.id) },
                                set: { store.setProviderModelsVisible(service.id, visible: $0) })) {
                HStack {
                    Text(LiveRouting.providerName(service.id)).font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(store.data.visibleModels(in: service).count)/\(service.models.count)")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }.toggleStyle(.switch).controlSize(.small)
                .accessibilityLabel("\(LiveRouting.providerName(service.id)) models in Codex")
                .accessibilityValue(enabled ? "Shown" : "Hidden")
                .disabled(containsDefault)
                .help(containsDefault ? "Choose a default from another provider to hide this group." : "Show or hide this provider in the Codex picker.")
            if containsDefault {
                Text("Includes your new-task default").font(.caption2).foregroundStyle(.secondary)
            }
            if enabled {
                ForEach(service.models) { model in
                    let selection = SelectedModel(serviceID: service.id, modelID: model.id)
                    let isDefault = selection == store.data.selectedModel
                    Toggle(isOn: Binding(get: { store.data.modelPicker.isVisible(selection) },
                                        set: { store.setModelVisible(selection, visible: $0) })) {
                        HStack {
                            Text(model.name).lineLimit(2)
                            if isDefault { Text("Default").font(.caption2).foregroundStyle(.secondary) }
                        }
                    }.font(.system(size: 12)).disabled(isDefault)
                        .help(isDefault ? "Choose another default before hiding this model." : "Show \(model.name) in Codex.")
                }
            } else {
                Text("Hidden from the picker. Connection retained.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if service.id == "azure" {
                Button("Find Azure deployments…") { onAddModels("azure") }.font(.caption)
                Text("Azure is the host. Add a deployment from your resource, then verify it before it appears here.")
                    .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else if service.id == "openrouter" {
                Button("Browse OpenRouter models…") { onAddModels("openrouter") }.font(.caption)
            }
        }
    }
}
