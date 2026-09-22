# Dify Marketplace policy determination — AssetFare 0.0.5

Re-verified 2026-09-19 against Dify's **current** plugin submission requirements
and review guidelines, for the question: does adding an **unsigned**,
non-custodial `prepare` tool and `session` lifecycle tools change this plugin's
classification or block it from the Dify Marketplace?

## Determination: ALLOWED (no blocker)

An unsigned / no-sign / no-submit / no-credential prepare + session-lifecycle
tool that only calls a fixed HTTPS API (`api.assetfare.dev`) to return an
**unsigned** transaction bundle is **allowed** on the Dify Marketplace. It does
**not** receive a special "financial / crypto / execution / wallet / signing"
classification, and there is **no** rule that bans, gates, or specially restricts
blockchain / crypto / wallet / transaction-construction plugins.

By Dify's own risk taxonomy the plugin is **Low risk** (fixed documented HTTPS
API; no code/command/SQL execution, no filesystem/browser automation, no
arbitrary URL fetching, no sensitive personal data), so it takes the normal,
lightest review path.

**No prepare or session tool was dropped.** Everything the caller-approved v2
contract specifies is implemented; nothing was silently shrunk, and no capability
is falsely reduced in the docs.

## Supporting rule text

Authoritative doc: `docs/plugin-submission-requirements.md` in the marketplace
repo (mirrored at docs.dify.ai). Verbatim risk-level language:

- **Low risk (this plugin):** "The plugin only calls fixed, documented
  third-party HTTPS APIs and does not execute user-controlled code, commands,
  SQL, file operations, browser automation, or arbitrary network requests."
- **High risk (none of which this plugin does):** "The plugin can execute
  commands or code, run SQL, access databases, use SSH/SFTP, operate on
  filesystems, automate browsers, proxy or crawl arbitrary URLs, bundle
  executables, or handle health, financial, biometric, children, authentication,
  location, or other sensitive personal data."

Nuance on "financial": in this policy it appears **only** as a *sensitive
personal data* category (health / biometric / children / financial records),
i.e. handling a person's private financial data — **not** a ban on
financial-transaction functionality. Constructing an unsigned on-chain bundle
does not handle "financial sensitive personal data," and because the tool never
signs, never submits, never touches a key or credential, and never moves funds,
it trips no high-risk or sensitive-capability item. (If the prepare call were
characterized as a third-party "write action" it could be argued up to Medium,
but returning an unsigned bundle is quote-style; either way it is publishable.)

## Requirements satisfied for listing

- **manifest.yaml** — accurate author/name/version/type/runner/icon/privacy;
  version incremented to 0.0.5; `privacy: PRIVACY.md` declared. ✔
- **README (English)** — setup, usage, required APIs/connection, source repo link,
  explicit security boundary. ✔
- **PRIVACY.md** — declares exactly what each tool sends (including public wallet
  addresses / tx hashes for prepare/session and the hashed session token), states
  no personal data / no credentials collected, and discloses the third-party API
  it calls. ✔
- **Package hygiene** — no secrets / `.env` / private keys / `.git` / venvs /
  caches in the package (see `.difyignore`); the design takes no credential. ✔
- **PR disclosure** — declare risk level **Low**; sensitive capabilities: none.

## Blockers

None. The prepare and full session lifecycle tools are all shipped as the policy
allows. (Deployment ordering — activating the new tools only after the upstream
6-chain API guarantees the fail-closed handoff — is an operational concern, not a
marketplace-policy blocker.)

## Sources

- https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-submission-requirements.md
- https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-review-guidelines.md
- https://docs.dify.ai/en/develop-plugin/publishing/standards/privacy-protection-guidelines
- https://github.com/langgenius/dify-plugins/blob/main/README.md
- https://docs.dify.ai/plugins/publish-plugins/publish-to-dify-marketplace
