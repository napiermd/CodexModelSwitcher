import SwiftUI

struct AzureSetupView: View {
    @EnvironmentObject private var store: AppStore
    var onConnected: () -> Void
    @State private var endpoint = ""
    @State private var key = ""
    @State private var deploymentName = ""
    @State private var effort = "none"
    @State private var context = "128000"
    @State private var vision = false
    @State private var advanced = false
    @State private var hideBaseten = true
    @State private var makeDefault = true

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Your Azure resource").font(.headline)
            Text("Use the endpoint and key from Azure. Harbor stores the key in macOS Keychain.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 6) {
                Text("Resource endpoint").font(.caption.weight(.medium))
                TextField("https://your-resource.openai.azure.com", text: $endpoint).textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Azure resource endpoint")
                Text("You can include /openai/v1/.").font(.caption2).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("API key").font(.caption.weight(.medium))
                SecureField("Azure OpenAI API key", text: $key).textFieldStyle(.roundedBorder)
            }
            Divider()
            Text("Add a deployment").font(.headline)
            TextField("Deployment name from Azure", text: $deploymentName).textFieldStyle(.roundedBorder)
                .accessibilityLabel("Azure deployment name")
            Text("Use the deployment name, which may differ from the model name. Add more deployments here later.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            DisclosureGroup("Deployment options", isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 10) {
                    Picker("Reasoning", selection: $effort) {
                        Text("Deployment default").tag("none")
                        Text("Low").tag("low"); Text("Medium").tag("medium")
                        Text("High").tag("high"); Text("Extra high").tag("xhigh")
                    }
                    Text("Harbor checks the selected setting and uses it for this deployment. Change it here and verify again to use another effort.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Toggle("Enable image input", isOn: $vision)
                    HStack { Text("Context limit"); TextField("128000", text: $context).textFieldStyle(.roundedBorder) }
                    Text("Set the token limit from your deployment's model specifications. Harbor cannot discover this limit from the key.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.padding(.top, 8)
            }
            Divider()
            Toggle("Use for new tasks", isOn: $makeDefault)
            Toggle("Hide Baseten in Harbor", isOn: $hideBaseten)
            Text("Existing tasks keep their model. Hidden providers remain configured; show them again in Settings → Providers.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("Verification sends one small, billable request to test tool calling and the selected options.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button(store.connectingAzure ? "Verifying…" : "Verify & add deployment") {
                    Task {
                        let model = AzureDeployment(name: deploymentName.trimmingCharacters(in: .whitespacesAndNewlines),
                                                    effort: effort, context: Int(context) ?? 0, vision: vision)
                        if await store.connectAzure(endpoint: endpoint, key: key, deployment: model, makeDefault: makeDefault) {
                            var hidden = Set((UserDefaults.standard.string(forKey: "harbor.hiddenProviders") ?? "").split(separator: ",").map(String.init))
                            hidden.remove("azure")
                            if hideBaseten { hidden.insert("baseten") }
                            UserDefaults.standard.set(hidden.sorted().joined(separator: ","), forKey: "harbor.hiddenProviders")
                            UserDefaults.standard.removeObject(forKey: "harbor.azureEndpointDraft")
                            key = ""
                            onConnected()
                        }
                    }
                }.buttonStyle(.borderedProminent)
                    .disabled(key.isEmpty || deploymentName.isEmpty || endpoint.isEmpty || store.proxyStatus != .active)
                Spacer()
                Link("Azure portal ↗", destination: URL(string: "https://ai.azure.com")!).font(.caption)
            }
        }
        .disabled(store.connectingAzure)
        .onAppear {
            let saved = store.data.services.first { $0.id == "azure" }
            endpoint = saved?.baseURL ?? UserDefaults.standard.string(forKey: "harbor.azureEndpointDraft") ?? ""
            key = saved?.apiKey ?? ""
        }
        .onDisappear { key = "" }
    }
}
