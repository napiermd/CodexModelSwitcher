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
    @State private var deployments: [String] = []
    @State private var discoveryMessage = ""
    @State private var findingDeployments = false
    @State private var startedAt: Date?
    @State private var operation: Task<Void, Never>?

    private var busy: Bool { findingDeployments || store.connectingAzure }
    private var missingInput: String? {
        if endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Enter your Azure resource endpoint." }
        if key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Paste your API key into the secure field." }
        if deploymentName.isEmpty { return "Find your deployments and choose one, or enter its exact name below." }
        if store.proxyStatus != .active { return "Wait for the Harbor bridge to connect before verifying." }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            form.disabled(busy)
            if let startedAt, busy {
                HStack(alignment: .center) {
                    ProgressView().controlSize(.small)
                    TimelineView(.periodic(from: startedAt, by: 1)) { clock in
                        Text("\(findingDeployments ? "Finding deployments" : "Checking image and tool support")… \(Int(clock.date.timeIntervalSince(startedAt)))s")
                            .font(.caption).monospacedDigit()
                    }
                    Spacer()
                    Button("Cancel") { operation?.cancel() }
                }
                Text(findingDeployments ? "Discovery stops after 20 seconds." : "Verification stops after 60 seconds. You can cancel at any time.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .onAppear {
            let saved = store.data.services.first { $0.id == "azure" }
            endpoint = saved?.baseURL ?? UserDefaults.standard.string(forKey: "harbor.azureEndpointDraft") ?? ""
            key = saved?.apiKey ?? ""
            deploymentName = saved?.models.last?.id ?? ""
            restoreDeploymentOptions()
        }
        .onChange(of: endpoint) { _ in resetDiscovery() }
        .onChange(of: key) { _ in resetDiscovery() }
        .onChange(of: deploymentName) { _ in restoreDeploymentOptions() }
        .onDisappear { operation?.cancel(); key = "" }
    }

    private var form: some View {
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
            Button("Find deployments") { findDeployments() }
                .disabled(key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || endpoint.isEmpty)
            if !discoveryMessage.isEmpty {
                Text(discoveryMessage).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            Text("Add a deployment").font(.headline)
            if !deployments.isEmpty {
                Picker("Deployment", selection: $deploymentName) {
                    Text("Choose a deployment…").tag("")
                    if !deploymentName.isEmpty && !deployments.contains(deploymentName) { Text(deploymentName).tag(deploymentName) }
                    ForEach(deployments, id: \.self) { Text($0).tag($0) }
                }
            }
            TextField("Or enter the deployment name from Azure", text: $deploymentName).textFieldStyle(.roundedBorder)
                .accessibilityLabel("Azure deployment name")
            Text("The deployment name can differ from the model name. Discovery only lists deployments already created in this resource.")
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
            if let missingInput { Text(missingInput).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            HStack {
                Button(store.connectingAzure ? "Verifying…" : "Verify & add deployment") { connect() }
                    .buttonStyle(.borderedProminent).disabled(missingInput != nil)
                Spacer()
                Link("Azure portal ↗", destination: URL(string: "https://ai.azure.com")!).font(.caption)
            }
        }
    }

    private func resetDiscovery() { deployments = []; discoveryMessage = "" }

    private func restoreDeploymentOptions() {
        guard let path = store.data.services.first(where: { $0.id == "azure" })?.catalogPath,
              let bytes = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let catalog = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let entry = (catalog["models"] as? [[String: Any]])?.first(where: { $0["slug"] as? String == deploymentName }) else { return }
        effort = entry["default_reasoning_level"] as? String ?? "none"
        context = String(entry["context_window"] as? Int ?? 128000)
        vision = (entry["input_modalities"] as? [String] ?? []).contains("image")
    }

    private func findDeployments() {
        findingDeployments = true; startedAt = Date(); discoveryMessage = ""; store.clearError()
        operation = Task {
            defer { findingDeployments = false; startedAt = nil }
            do {
                deployments = try await AzureAPI.discoverDeployments(endpoint: endpoint, key: key)
                discoveryMessage = deployments.isEmpty ? "Key accepted. No ready deployments were returned. Create one in Azure or enter its name manually." : "Key accepted. \(deployments.count) deployments found."
            } catch {
                if Task.isCancelled { discoveryMessage = "Discovery cancelled." }
                else if (error as? URLError)?.code == .timedOut { discoveryMessage = "Azure did not return deployments within 20 seconds. Try again or enter the name manually." }
                else { discoveryMessage = error.localizedDescription }
            }
        }
    }

    private func connect() {
        startedAt = Date()
        operation = Task {
            defer { startedAt = nil }
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
    }
}
