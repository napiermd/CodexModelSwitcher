import SwiftUI
import Charts

struct UsageView: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage("harbor.usageProvider") private var providerID = "baseten"
    @AppStorage("harbor.hiddenProviders") private var hiddenProviders = ""
    @State private var accountID = ""
    @State private var historyDays = 30
    private var entries: [UsageSnapshot] { store.usageSnapshots.filter { $0.providerID == providerID } }
    private var selected: UsageSnapshot? { entries.first { $0.id == accountID } ?? entries.first }
    private var providers: [(String, String)] {
        var result = ProviderDefinition.all.filter { !hiddenProviders.split(separator: ",").contains(Substring($0.id)) }.map { ($0.id, $0.name == "OpenRouter" ? "Router" : $0.name) }
        if store.usageSnapshots.contains(where: { $0.providerID == "claude" }) { result.append(("claude", "Claude")) }
        return result
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Picker("Usage provider", selection: $providerID) {
                ForEach(providers, id: \.0) { Text($0.1).tag($0.0) }
            }.pickerStyle(.segmented).labelsHidden()
            if let snapshot = selected {
                if entries.count > 1 {
                    Picker("Account", selection: Binding(get: { selected?.id ?? "" }, set: { accountID = $0; saveAccount() })) {
                        ForEach(entries) { Text($0.accountLabel).tag($0.id) }
                    }.font(.system(size: 12)).pickerStyle(.menu)
                } else {
                    Text(snapshot.accountLabel).font(.system(size: 13, weight: .semibold)).textSelection(.enabled)
                }
                if let plan = snapshot.plan, !plan.isEmpty { Text(plan.replacingOccurrences(of: "_", with: " ").capitalized).font(.caption).foregroundStyle(.secondary) }
                if let error = snapshot.error {
                    Label(error, systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
                if !snapshot.windows.isEmpty {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(snapshot.windows) { window in quota(window, stale: snapshot.isStale) }
                    }
                }
                if snapshot.costToday != nil || snapshot.cost30Days != nil || snapshot.costMonth != nil || snapshot.tokens30Days != nil {
                    if !snapshot.windows.isEmpty { Divider() }
                    VStack(alignment: .leading, spacing: 12) {
                        let hasCost = snapshot.costToday != nil || snapshot.cost30Days != nil || snapshot.costMonth != nil
                        Text(hasCost ? (snapshot.isEstimate ? "Token value estimate" : "API spend") : "Token usage").font(.system(size: 14, weight: .semibold))
                        if hasCost { HStack(alignment: .top) {
                            metric(snapshot.isEstimate ? "Today · estimate" : "Today", value: money(snapshot.costToday))
                            Spacer()
                            metric(snapshot.costMonth != nil ? "Calendar month" : "Last 30 days", value: money(snapshot.costMonth ?? snapshot.cost30Days))
                        }
                        }
                        if snapshot.tokens30Days != nil || snapshot.tokensToday != nil {
                            HStack(alignment: .top) {
                                metric("Today's tokens", value: count(snapshot.tokensToday), prominent: false)
                                Spacer()
                                metric("30-day tokens", value: count(snapshot.tokens30Days), prominent: false)
                            }
                        }
                    }
                    if !snapshot.daily.isEmpty { history(snapshot) }
                }
                if let balance = snapshot.balance {
                    Divider()
                    HStack {
                        Text(providerID == "openrouter" ? "Key budget remaining" : "Credits balance").font(.caption)
                        Spacer()
                        Text(providerID == "openrouter" ? money(balance) : balance.formatted()).font(.subheadline.weight(.semibold)).monospacedDigit()
                    }
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text(snapshot.scope).font(.caption.weight(.medium))
                    if let note = snapshot.note { Text(note).font(.caption).foregroundStyle(.secondary) }
                    TimelineView(.periodic(from: .now, by: 60)) { _ in
                        HStack(spacing: 4) {
                            Text(snapshot.source)
                            if let updated = snapshot.updatedAt {
                                Text("·")
                                Text(updated, style: .relative)
                                Text("ago\(snapshot.isStale ? " · stale" : "")")
                            }
                        }.font(.system(size: 10)).foregroundStyle(snapshot.isStale ? Color.orange : Color.secondary)
                    }
                }.fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Text(providerID == "azure" ? "Azure billing is available in Azure" : (store.usageRefreshing ? "Reading account usage…" : "Usage has not been loaded")).font(.headline)
                    Text(providerID == "azure" ? "Harbor does not yet import Azure spend or quota. Open the dashboard for your resource usage and Cost Management." : "Refresh to read account limits and provider-reported spend.").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.padding(.vertical, 16)
            }
            Divider()
            HStack {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let cooldown = max(0, Int(ceil(60 - context.date.timeIntervalSince(store.usageLastAttempt))))
                    Button { Task { await store.refreshUsage() } } label: {
                        Label(store.usageRefreshing ? "Refreshing…" : cooldown > 0 ? "Refresh in \(cooldown)s" : "Refresh usage", systemImage: "arrow.clockwise")
                    }.disabled(providerID == "azure" || store.usageRefreshing || cooldown > 0).keyboardShortcut("r", modifiers: .command)
                }
                Spacer()
                Button {
                    let address = providerID == "claude" ? "https://claude.ai/settings/usage" : providerID == "codex-subscription" ? "https://chatgpt.com/codex/settings/usage" : ProviderDefinition.named(providerID).dashboard
                    if let url = URL(string: address) { NSWorkspace.shared.open(url) }
                } label: {
                    HStack(spacing: 4) { Text("Dashboard"); Image(systemName: "arrow.up.right") }
                }.buttonStyle(.link)
            }
        }
        .onAppear { if !providers.contains(where: { $0.0 == providerID }) { providerID = providers.first?.0 ?? "codex-subscription" }; selectSavedAccount(); Task { await store.refreshUsage() } }
        .onChange(of: providerID) { _ in selectSavedAccount() }
    }
    private func selectSavedAccount() { accountID = UserDefaults.standard.string(forKey: "harbor.usageAccount.\(providerID)") ?? "" }
    private func saveAccount() { UserDefaults.standard.set(accountID, forKey: "harbor.usageAccount.\(providerID)") }
    private func money(_ amount: Double?) -> String { amount.map { $0.formatted(.currency(code: "USD")) } ?? "Unavailable" }
    private func count(_ value: Int?) -> String { value.map { $0.formatted(.number.notation(.compactName)) } ?? "Unavailable" }
    private func metric(_ title: String, value: String, prominent: Bool = true) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.system(size: prominent ? 22 : 16, weight: .semibold)).monospacedDigit().contentTransition(.numericText())
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func quota(_ window: UsageWindow, stale: Bool) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                Text(window.title).font(.system(size: 13, weight: .semibold))
                Spacer()
                Text(window.remainingPercent.map { "\(Int($0.rounded()))% left" } ?? "Usage unavailable").font(.system(size: 13, weight: .medium)).monospacedDigit()
            }
            if let percent = window.remainingPercent {
                ProgressView(value: percent, total: 100).tint(stale ? .gray : percent <= 10 ? .orange : .accentColor)
                    .accessibilityLabel("\(window.title), \(Int(percent.rounded())) percent remaining")
            }
            if let reset = window.resetsAt {
                TimelineView(.periodic(from: .now, by: 60)) { context in
                    HStack(spacing: 4) {
                        if reset > context.date {
                            Text("Resets in")
                            Text(reset, style: .relative)
                        } else {
                            Text("Reset time passed · awaiting fresh usage")
                        }
                        Spacer()
                    }.font(.caption).foregroundStyle(.secondary)
                }.help(reset.formatted(date: .abbreviated, time: .shortened))
            }
        }
    }
    private func history(_ snapshot: UsageSnapshot) -> some View {
        let days = snapshot.history(days: historyDays)
        let range = snapshot.historyRange(days: historyDays)
        let ticks = [range.lowerBound, range.lowerBound.addingTimeInterval(Double(historyDays / 2) * 86400), range.upperBound.addingTimeInterval(-86400)]
        let hasCosts = days.contains { $0.costUSD != nil }
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(hasCosts ? "Daily \(snapshot.isEstimate ? "estimated value" : "spend")" : "Daily tokens").font(.caption.weight(.medium))
                Spacer()
                Picker("History period", selection: $historyDays) { Text("7 days").tag(7); Text("30 days").tag(30) }
                    .pickerStyle(.segmented).frame(width: 132).labelsHidden()
            }
            Chart(days) { day in
                if let date = day.timestamp, let value = hasCosts ? day.costUSD : day.tokens.map(Double.init) {
                    BarMark(x: .value("Day", date, unit: .day), y: .value(hasCosts ? "USD" : "Tokens", value))
                        .foregroundStyle(Color.accentColor.opacity(snapshot.isStale ? 0.45 : 0.8))
                        .cornerRadius(2)
                        .accessibilityLabel(day.date)
                        .accessibilityValue(hasCosts ? money(day.costUSD) : count(day.tokens))
                }
            }.chartXScale(domain: range)
                .chartXAxis {
                    AxisMarks(values: ticks) { value in
                        AxisValueLabel(anchor: value.index == 0 ? .topLeading : value.index == 2 ? .topTrailing : .top) {
                            if let date = value.as(Date.self) {
                                Text(date.formatted(Date.FormatStyle(timeZone: TimeZone(secondsFromGMT: 0)!).month(.abbreviated).day()))
                                    .font(.system(size: 9))
                            }
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let number = value.as(Double.self) {
                                Text(hasCosts ? number.formatted(.currency(code: "USD").precision(.fractionLength(0))) : number.formatted(.number.notation(.compactName)))
                            }
                        }
                    }
                }
                .frame(height: 115)
        }
    }
}
