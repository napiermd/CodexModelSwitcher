// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "HarborCore",
    platforms: [.macOS(.v13)],
    products: [.library(name: "HarborCore", targets: ["HarborCore"])],
    targets: [
        .target(name: "HarborCore", path: "ModelHarbor",
            exclude: ["AppStore.swift", "GeneralSettingsView.swift", "UsageView.swift", "ContentView.swift", "AzureSetupView.swift", "ModelsSettingsView.swift", "AppDelegate.swift", "ModelHarborApp.swift",
                      "InstallationVerification.swift", "VisualEffects.swift", "Support", "Assets.xcassets", "ModelHarbor.entitlements"],
            sources: ["Types.swift", "ModelPickerPreferences.swift", "Utils.swift", "CodexConfigWriter.swift", "CredentialStore.swift", "OpenAIAuthManager.swift", "GrokAdapter.swift", "ConfigValidation.swift", "LiveRouting.swift", "BasetenCatalog.swift", "ProviderPresentation.swift", "AzureProvider.swift", "Usage.swift", "WarmUpController.swift", "LoginItemController.swift", "LifecyclePreferences.swift", "WindowLifecycleController.swift", "CodexRestartController.swift"]),
        .testTarget(name: "HarborCoreTests", dependencies: ["HarborCore"], path: "Tests", exclude: ["test_bifrost_pilot.py", "test_gateway_service.py", "test_gateway_runtime.py", "test_gateway_lifecycle.py", "test_stage_update.py", "test_azure_provider.py", "test_grok_adapter.py", "test_provider_usage.py", "test_baseten_images.py", "test_baseten_pacing.py", "test_task_repair.py", "test_task_repair_api.py", "test_harbor_team.py", "test_baseten_reasoning.py", "test_provider_connections.py", "__pycache__"])
    ])
