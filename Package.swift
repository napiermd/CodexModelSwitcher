// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "SwitcherCore",
    platforms: [.macOS(.v13)],
    products: [.library(name: "SwitcherCore", targets: ["SwitcherCore"])],
    targets: [
        .target(name: "SwitcherCore", path: "CodexModelSwitcher",
            exclude: ["AppStore.swift", "ContentView.swift", "AppDelegate.swift", "CodexModelSwitcherApp.swift",
                      "InstallationVerification.swift", "VisualEffects.swift", "Support", "Assets.xcassets", "CodexModelSwitcher.entitlements"],
            sources: ["Types.swift", "Utils.swift", "CodexConfigWriter.swift", "CredentialStore.swift", "OpenAIAuthManager.swift", "GrokAdapter.swift", "ConfigValidation.swift"]),
        .testTarget(name: "SwitcherCoreTests", dependencies: ["SwitcherCore"], path: "Tests", exclude: ["test_grok_adapter.py", "__pycache__"])
    ])
