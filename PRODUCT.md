# Model Harbor

## Platform

A native macOS 13+ menu-bar app with a static public website on GitHub Pages.

## Users and purpose

Andrew Napier maintains Model Harbor for people who use Codex with multiple model providers. A user can retain separate models in separate Codex tasks and change a model within an existing Harbor task without restarting between turns.

## Mechanism

A local authenticated Responses bridge maps explicit model IDs to the current Codex subscription, Grok OAuth, or direct Baseten. Codex stores each task's model. Harbor manages shared connections and a default for new tasks. Saved Codex account switching remains a separate operation that requires restarting Codex.

## Public scope

Source preview under MIT, with upstream credit. macOS source builds are available. There is no notarized public installer. Account eligibility, model availability, and protocol compatibility can vary. Provider catalogs require one Codex restart to load.

## Ownership and brand

Model Harbor is Andrew Napier's fork of Hieu Nguyen's CodexModelSwitcher. Preserve the upstream history and attribution. Use the Model Harbor name and its teal branching-route icon throughout the app and public project. Public material must contain no real account details or credentials.

## Website

Purpose confirmed by Andrew: make the program shareable, understandable, customizable, and useful to the community. Implementation choice: static HTML, CSS, and a small JavaScript demo, published through GitHub Pages. The demo is explicitly illustrative. It never controls the installed app.
