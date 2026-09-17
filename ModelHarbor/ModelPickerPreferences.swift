import Foundation

struct ModelPickerPreferences: Codable, Equatable {
    var hiddenProviders: Set<String> = []
    var hiddenModels: Set<String> = []

    func isVisible(_ selection: SelectedModel) -> Bool {
        !hiddenProviders.contains(selection.serviceID) && !hiddenModels.contains(LiveRouting.modelID(for: selection))
    }
}

extension AppData {
    var pickerServices: [CodexService] {
        var supported = services.filter { LiveRouting.supports($0.id) }
        if let index = supported.firstIndex(where: { $0.id == selectedModel?.serviceID }) {
            supported.insert(supported.remove(at: index), at: 0)
        }
        return supported
    }

    func visibleModels(in service: CodexService) -> [CodexModel] {
        service.models.filter { modelPicker.isVisible(SelectedModel(serviceID: service.id, modelID: $0.id)) }
    }

    mutating func setPickerVisibility(_ visible: Bool, for selection: SelectedModel) throws {
        guard LiveRouting.supports(selection.serviceID),
              let service = services.first(where: { $0.id == selection.serviceID }) else { throw AppError.missingService }
        guard service.models.contains(where: { $0.id == selection.modelID }) else { throw AppError.missingModel }
        guard visible || selection != selectedModel else {
            throw ProviderError.message("Choose another new-task default before hiding this model.")
        }
        let slug = LiveRouting.modelID(for: selection)
        if visible {
            modelPicker.hiddenProviders.remove(selection.serviceID)
            modelPicker.hiddenModels.remove(slug)
        } else {
            modelPicker.hiddenModels.insert(slug)
        }
    }

    mutating func setPickerVisibility(_ visible: Bool, forProvider id: String) throws {
        guard LiveRouting.supports(id) else { throw AppError.missingService }
        guard visible || selectedModel?.serviceID != id else {
            throw ProviderError.message("Choose a new-task default from another provider before hiding these models.")
        }
        if visible { modelPicker.hiddenProviders.remove(id) }
        else { modelPicker.hiddenProviders.insert(id) }
    }
}
