# Security policy

Model Harbor handles authentication and forwards conversations to model providers. Review [the architecture](docs/architecture.md) before using it with sensitive data. This is community software without an independent security audit or a notarized public release.

## Report privately

Use [GitHub's private vulnerability reporting form](https://github.com/napiermd/model-harbor/security/advisories/new). Include the affected commit, a minimal reproduction with synthetic data, the impact, and any proposed fix. Do not include a real token, password, Keychain export, or private conversation.

If the form is unavailable, open an issue asking for a private contact method without disclosing the vulnerability. There is no guaranteed response time or bug-bounty program.

## Scope

Security fixes target the current `main` branch. Provider-authentication flows and endpoints can change independently. Do not assume an older build continues to enforce the same behavior.

Expected properties include loopback-only binding, authenticated bridge requests, fixed provider destinations, credentials excluded from metadata, private local configuration, and no silent fallback to another billing method. A failure of one of these properties is worth reporting.

Use the same signing identity across installed updates to preserve Keychain trust. The project does not recommend disabling macOS protections or changing 1Password's security policy. If a credential was exposed, revoke it through the owning provider and remove it from any public report.
