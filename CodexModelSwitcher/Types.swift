import Foundation

struct CodexService: Identifiable, Codable, Equatable {
    var id: String
    var name: String
    var baseURL: String
    var envKey: String
    var apiKey: String
    var useCompatibilityProxy: Bool
    var catalogPath: String?
    var usesExistingProvider: Bool?
    var models: [CodexModel]

    var requiresAPIKey: Bool {
        !envKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    init(
        id: String,
        name: String,
        baseURL: String,
        envKey: String,
        apiKey: String,
        useCompatibilityProxy: Bool = false,
        models: [CodexModel],
        catalogPath: String? = nil,
        usesExistingProvider: Bool = false
    ) {
        self.id = id
        self.name = name
        self.baseURL = baseURL
        self.envKey = envKey
        self.apiKey = apiKey
        self.useCompatibilityProxy = useCompatibilityProxy
        self.models = models
        self.catalogPath = catalogPath
        self.usesExistingProvider = usesExistingProvider
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case baseURL
        case envKey
        case apiKey
        case useCompatibilityProxy
        case models
        case catalogPath
        case usesExistingProvider
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(name, forKey: .name)
        try container.encode(baseURL, forKey: .baseURL)
        try container.encode(envKey, forKey: .envKey)
        try container.encode(useCompatibilityProxy, forKey: .useCompatibilityProxy)
        try container.encode(models, forKey: .models)
        try container.encode(catalogPath, forKey: .catalogPath)
        try container.encode(usesExistingProvider, forKey: .usesExistingProvider)
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        baseURL = try container.decode(String.self, forKey: .baseURL)
        envKey = try container.decode(String.self, forKey: .envKey)
        apiKey = try container.decodeIfPresent(String.self, forKey: .apiKey) ?? ""
        useCompatibilityProxy = try container.decodeIfPresent(Bool.self, forKey: .useCompatibilityProxy) ?? false
        models = try container.decode([CodexModel].self, forKey: .models)
        catalogPath = try container.decodeIfPresent(String.self, forKey: .catalogPath)
        usesExistingProvider = try container.decodeIfPresent(Bool.self, forKey: .usesExistingProvider)
    }
}

struct CodexModel: Identifiable, Codable, Equatable {
    var id: String
    var name: String
}

struct SelectedModel: Codable, Equatable {
    var serviceID: String
    var modelID: String
}

enum ReasoningEffort: String, CaseIterable, Codable, Identifiable, Equatable {
    case none
    case minimal
    case low
    case medium
    case high
    case xhigh

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .none: return "None"
        case .minimal: return "Minimal"
        case .low: return "Low"
        case .medium: return "Medium"
        case .high: return "High"
        case .xhigh: return "Extra high"
        }
    }

    static func from(rawValue: String?) -> ReasoningEffort? {
        guard let rawValue else { return nil }
        return ReasoningEffort(rawValue: rawValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
    }
}

struct AppData: Codable {
    var services: [CodexService]
    var selectedModel: SelectedModel?
    var openAIAccounts: [OpenAIAccount]
    var selectedOpenAIAccountID: String?
    var modelReasoningEffort: ReasoningEffort

    static let empty = AppData(services: [], selectedModel: nil)

    init(
        services: [CodexService],
        selectedModel: SelectedModel?,
        openAIAccounts: [OpenAIAccount] = [],
        selectedOpenAIAccountID: String? = nil,
        modelReasoningEffort: ReasoningEffort = .medium
    ) {
        self.services = services
        self.selectedModel = selectedModel
        self.openAIAccounts = openAIAccounts
        self.selectedOpenAIAccountID = selectedOpenAIAccountID
        self.modelReasoningEffort = modelReasoningEffort
    }

    enum CodingKeys: String, CodingKey {
        case services
        case selectedModel
        case openAIAccounts
        case selectedOpenAIAccountID
        case modelReasoningEffort
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        services = try container.decode([CodexService].self, forKey: .services)
        selectedModel = try container.decodeIfPresent(SelectedModel.self, forKey: .selectedModel)
        openAIAccounts = try container.decodeIfPresent([OpenAIAccount].self, forKey: .openAIAccounts) ?? []
        selectedOpenAIAccountID = try container.decodeIfPresent(String.self, forKey: .selectedOpenAIAccountID)
        modelReasoningEffort = try container.decodeIfPresent(ReasoningEffort.self, forKey: .modelReasoningEffort) ?? .medium
    }
}

struct OpenAIAccount: Identifiable, Codable, Equatable {
    var id: String
    var name: String
    var authJSON: String
    var accountID: String?
    var email: String?
    var credentialStatus: OpenAIAccountCredentialStatus
    var credentialMessage: String?
    var createdAt: Date

    var displayName: String {
        name.isEmpty || name == "Current Codex account" || name == "Codex account" ? (email ?? name) : name
    }

    init(
        id: String,
        name: String,
        authJSON: String,
        accountID: String?,
        email: String?,
        credentialStatus: OpenAIAccountCredentialStatus = .unchecked,
        credentialMessage: String? = nil,
        createdAt: Date
    ) {
        self.id = id
        self.name = name
        self.authJSON = authJSON
        self.accountID = accountID
        self.email = email
        self.credentialStatus = credentialStatus
        self.credentialMessage = credentialMessage
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case authJSON
        case accountID
        case email
        case credentialStatus
        case credentialMessage
        case createdAt
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(name, forKey: .name)
        try container.encode(accountID, forKey: .accountID)
        try container.encode(email, forKey: .email)
        try container.encode(credentialStatus, forKey: .credentialStatus)
        try container.encode(credentialMessage, forKey: .credentialMessage)
        try container.encode(createdAt, forKey: .createdAt)
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        authJSON = try container.decodeIfPresent(String.self, forKey: .authJSON) ?? ""
        accountID = try container.decodeIfPresent(String.self, forKey: .accountID)
        email = try container.decodeIfPresent(String.self, forKey: .email)
        credentialStatus = try container.decodeIfPresent(
            OpenAIAccountCredentialStatus.self,
            forKey: .credentialStatus
        ) ?? .unchecked
        credentialMessage = try container.decodeIfPresent(String.self, forKey: .credentialMessage)
        createdAt = try container.decode(Date.self, forKey: .createdAt)
    }
}

enum OpenAIAccountCredentialStatus: String, Codable {
    case unchecked
    case valid
    case invalid
}

enum ProxyServerStatus: Equatable {
    case starting
    case notRunning
    case active
    case error
}

struct ServiceFormData: Equatable {
    var id: String
    var name: String
    var baseURL: String
    var envKey: String
    var apiKey: String
    var useCompatibilityProxy: Bool
    var modelsText: String

    init(service: CodexService? = nil) {
        id = service?.id ?? ""
        name = service?.name ?? ""
        baseURL = service?.baseURL ?? ""
        envKey = service?.envKey ?? ""
        apiKey = service?.apiKey ?? ""
        useCompatibilityProxy = service?.useCompatibilityProxy ?? false
        modelsText = service?.models.map(\.id).joined(separator: "\n") ?? ""
    }
}
