# Restore saved provider connections without changing task settings

## Scope

An explicit connection-menu action restores an existing saved Azure or OpenRouter connection after maintenance. It verifies a selected saved model before publishing the credential to inference. It does not edit the provider catalog, Codex configuration, task choices, conversation history, or credential store. It does not restart or replace a gateway.

The interface uses one provider and one selected route per action. The server accepts a bounded batch so its validation and publication remain atomic if a later caller restores both providers. Success establishes only the exact routes listed in the receipt. Restoring another provider changes the shared configuration revision and expires earlier route proofs.

## Caller and boundary

The caller first reads authenticated status and compares the running independent runtime with the bundled candidate. A pending gateway update is reported before reading Keychain. Only an explicit action can read the selected provider's saved credential; startup and status polling never invoke restoration.

`POST /harbor/providers/restore` carries:

- `expected_runtime`: protocol version, artifact digest, boot UUID, and independent mode from authenticated status.
- `expected_configuration_revision`: the captured configuration fingerprint.
- `connections`: Azure endpoint/key, OpenRouter key, or both.
- `required_models`: one to eight exact saved Harbor routes, with at least one for each supplied provider.

The control helper checks the expected runtime against the authenticated handshake before sending the credential-bearing payload on that same socket. Automatic reconnect and replay remain disabled. The helper bounds the entire operation to 45 seconds.

The server rejects unknown fields, arbitrary routes, an Azure endpoint that differs from saved settings, changed runtime/configuration, and replacement of a populated connection with different credentials. A missing connection can be restored with retained tracked turns intact. Each continuation still passes the original runtime, credential-binding, and uncertain-delivery checks before upstream dispatch. A different restored key cannot take over existing history. Untracked admissions block restoration because their requests have no recorded turn binding. Restoration probes do not create untracked admissions. Rows belonging to older runtimes remain untouched.

## Verification and publication

Probe immutable staged credentials outside the configuration lock. No candidate credential is temporarily installed in global connection state. Any failed route, cancellation, configuration race, or occupancy conflict prevents publication. After every required route passes, recheck context and publish the connection batch once under the lock. Record successful proofs against the resulting revision.

The receipt identifies runtime, resulting revision, restored/already-present providers, and exact verified routes. It contains no keys, conversation content, or raw upstream errors. If the response is lost after possible publication, the interface reports an unknown outcome and does not replay or roll back credentials.

## Required checks

- Reject stale identity before sensitive payload transmission; no reconnect or replay.
- Invalid batch member, failed route, or concurrent configuration change publishes no credentials.
- Preserve active/tool-gap ownership and uncertain delivery; never transfer a turn between runtimes.
- Verify the selected saved route with staged credentials; no catalog/default/config/credential-store writes.
- Restrict Keychain access to explicit selection of the corresponding provider.
- Bound cancellation and timeouts; terminate only the control helper.
- Run the production handler with isolated state and fixture upstreams, plus Swift caller tests, packaged lifecycle checks, and signature verification.

Independent review removed an overly broad tracked-turn restriction: completed HTTP requests retain their turn rows, so that restriction would have prevented restoration after an ordinary restart. The existing binding checks protect tracked history without deleting ownership.

## Verified implementation

- 375 Python tests passed, including 22 new restoration/control tests. Fixture checks cover atomic batch publication, exact-route verification, saved endpoint constraints, same-socket expected identity, context/configuration races, retained bindings after restart, uncertain history, disconnects, slow request bodies, and unchanged local configuration.
- 97 Swift tests passed. Restoration tests cover preflight before credential access, provider scoping, exact receipts, no local writes, lost replies, cancellation before dispatch, helper isolation, and authentication cancellation/timeout completing before a blocked read returns.
- All 10 packaged gateway lifecycle tests passed against the signed app resources. The signed macOS app built successfully; its Python sources matched the checked-out modules. Website checks passed.
- Independent review found one cancellation defect; it was fixed with a per-read authentication context and a cancellable continuation. A focused re-review found no remaining P1/P2 findings.

The 45-second control-helper ceiling starts after credential acquisition. Credential authentication has its own 45-second ceiling and Cancel invalidates that authentication operation. Server restoration has a 40-second total budget, including request-body reads; each route probe has at most 10 seconds. A late credential result is discarded.

These checks use synthetic credentials and isolated local upstreams. This unit does not establish live gateway handoff, real saved-key restoration, or real Azure history continuation. The installed retained runtime must still be updated during coordinated maintenance before the new restore endpoint can be used.
