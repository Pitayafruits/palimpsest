# Security

Do not report passwords, tokens, private Confluence URLs or document content in public issues. For a vulnerability, use GitHub's private vulnerability reporting when available, or contact the repository owner through a non-public channel before disclosing details.

Only HTTPS sites are accepted. Credentials are scoped to the configured site; attachment requests reject other origins and redirects. Windows remembers credentials using current-user DPAPI; macOS uses Keychain. Stored document baselines and backups are local files, not encrypted by this application.

Palimpsest currently supports password-based Basic Authentication for compatible Server / Data Center deployments. It does not bypass SSO, CAPTCHA, MFA or administrator account restrictions.
