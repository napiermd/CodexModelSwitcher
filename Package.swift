// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "HarborCore",
    platforms: [.macOS(.v13)],
    products: [.library(name: "HarborCore", targets: ["HarborCore"])],
    targets: [
        .target(name: "HarborCore", path: "ModelHarbor",
            exclude: ["AppStore.swift", "ContentView.swift", "AppDelegate.swift", "ModelHarborApp.swift",
                      "InstallationVerification.swift", "VisualEffects.swift", "Support", "Assets.xcassets", "ModelHarbor.entitlements"],
            sources: ["Types.swift", "Utils.swift", "CodexConfigWriter.swift", "CredentialStore.swift", "OpenAIAuthManager.swift", "GrokAdapter.swift", "ConfigValidation.swift", "LiveRouting.swift", "BasetenCatalog.swift"]),
        .testTarget(name: "HarborCoreTests", dependencies: ["HarborCore"], path: "Tests", exclude: ["test_grok_adapter.py", "test_baseten_pacing.py", "test_task_repair.py", "test_task_repair_api.py", "test_harbor_team.py", "test_baseten_reasoning.py", "__pycache__"])
    ])
