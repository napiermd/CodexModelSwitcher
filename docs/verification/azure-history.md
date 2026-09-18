# Azure reasoning-history preservation

The direct Azure route used a translator originally written for Grok. That translator removed every reasoning item before sending a follow-up request. A second recursive conversion could rewrite tool-shaped dictionaries inside opaque provider metadata. Both defects are reproduced and fixed in the source candidate described here.

## Behavior

`AzureTranslation` retains reasoning, compaction, and unknown typed history items in their original order. Their IDs, encrypted strings, summaries, and extension fields remain intact. The request asks Azure to return encrypted reasoning for subsequent turns.

Known message and tool item IDs still receive the existing normalization. This preserves the fix for Codex custom-tool result IDs such as `ctco_…` reaching Azure as function-call results. Tool `call_id` links and known namespace/custom-tool mappings remain intact.

Output conversion visits only actual response output items and recognized streaming event positions. It leaves opaque metadata untouched, including dictionaries that resemble tool calls. Unchanged streaming events retain their data bytes. Dispatcher suppression applies only to mapped function calls.

Harbor cannot determine ciphertext origin from its contents. The selected Azure resource validates the supplied history once. A rejection returns unchanged under the existing error-body limits. Harbor does not remove reasoning, replay a modified request, substitute a model, or rewrite saved conversations. This policy does not guarantee that history from another provider or account will be accepted. Existing conversations that previously continued only because their foreign reasoning was removed may now receive an explicit Azure validation error. Real continuation and cross-provider behavior must be checked before promoting this candidate.

A mandatory local provenance registry would also classify valid pre-upgrade history as unknown. Azure API keys do not provide a stable account identity that distinguishes key rotation from account changes. Such a registry needs a separate identity and migration design if future policy requires rejection before provider validation.

## Verified locally

- Eight regression tests cover ordered opaque input, tool links, nested metadata, unchanged streaming events, dispatcher suppression, and malformed `include` values.
- The actual HTTP handler returns JSON and streaming reasoning output, then preserves that output in the next tool-result request.
- A simulated `400 invalid_encrypted_content` reaches the caller after exactly one unchanged upstream attempt.
- The full Python suite passes 335 tests with warnings treated as errors.
- Independent review found no blocking correctness or comment issues.
- The signed candidate builds successfully. The packaged gateway passes ten lifecycle tests and is staged as `azure-history-preservation-20260917`.

## Live verification remains open

`scripts/verify-azure-history.py` runs five bounded requests through an isolated source gateway: a JSON tool round trip, a streaming tool round trip, and an invalid-ciphertext negative control. It reads an Azure key from stdin and emits only aggregate results. It never changes the installed gateway, task histories, or provider configuration. No real ciphertext or credential belongs in an evidence file.

The attempted run stopped before any Azure request because the isolated Keychain reader timed out. Do not interpret the installed app's earlier successful stateless probe as evidence that this candidate preserves real Azure continuation.

This candidate has not replaced the installed gateway. Full native namespace/custom-tool passthrough, tool-image shape preservation, stored `previous_response_id` routing, cross-account ciphertext compatibility, and actual desktop history persistence remain separate checks. The existing tool-image conversion is unchanged.
