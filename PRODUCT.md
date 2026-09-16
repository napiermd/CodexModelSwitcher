# Model Harbor

## Platform

A native macOS 13+ menu-bar app with a static public website on GitHub Pages.

## Users and purpose

Andrew Napier maintains Model Harbor for people who use Codex with multiple model providers. A user can retain separate models in separate Codex tasks and change a model within an existing Harbor task without restarting between turns.

## Mechanism

A local authenticated Responses bridge maps explicit model IDs to the current Codex subscription, Grok OAuth, direct Baseten, or selected OpenRouter models. Codex stores each task's model. Harbor manages shared connections and a default for new tasks. Saved Codex account switching remains a separate operation that requires restarting Codex.

## Connection truth

The connections panel reports provider readiness and activity from Harbor's local bridge. Baseten readiness comes from its cached credential state. Grok uses browser sign-in. OpenRouter verifies an API key, loads tool-capable models, and saves the chosen models and credentials through the existing secure storage path.

Codex shows Configured when its local account is present. It counts as Connected only after a completed upstream response in the current bridge session, unless a later authentication failure invalidates that evidence. The connected-provider count uses these provider checks. Active, completed, and failed requests are session activity, not account quota or billing data.

The menu bar can show the selected provider's connection, recent request activity, the last requested model, a connection count, the Harbor name, or an icon. Settings also controls appearance, visible providers, task repair, saved accounts, and the default for new tasks.

## Public scope

Source preview under MIT, with upstream credit. macOS source builds are available. There is no notarized public installer. Account eligibility, model availability, and protocol compatibility can vary. Provider catalogs require one Codex restart to load.

## Ownership and brand

Model Harbor is Andrew Napier's fork of Hieu Nguyen's CodexModelSwitcher. Preserve the upstream history and attribution. Use the Model Harbor name and its navy/platinum aperture monogram throughout the app and public project. The generated master is `assets/brand/harbor-icon-master.png`; its exact generation prompt is retained beside it. The native app uses adaptive macOS styling and a cool blue accent. The website retains its warm paper and editorial typography. Public material must contain no real account details or credentials.

## Website

Purpose confirmed by Andrew: make the program shareable, understandable, customizable, and useful to the community. Implementation choice: static HTML, CSS, and a small JavaScript demo, published through GitHub Pages. The demo is explicitly illustrative. It never controls the installed app.
