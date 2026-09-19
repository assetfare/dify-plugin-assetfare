# AssetFare (Dify plugin)

Non-custodial cross-chain route tools for a Dify Agent, Chatflow, or Workflow,
built on the [AssetFare](https://api.assetfare.dev) v2 API. The plugin discovers
and quotes a route, and — **only on the caller's explicit approval** — builds an
**unsigned** action plan for the caller's own wallet.

**AssetFare never signs, never submits, and never takes a private key, seed, or
credential.** The caller verifies, signs, and submits every action with its own
wallet, outside this plugin.

## The six-chain surface

Six source chains, eleven `(chain, token)` source endpoints, and **76 directed
routes** for quote discovery. Of these, **72 four-chain routes** (Solana, Base,
Arbitrum, Robinhood) are execution-ready. Polygon (models 1bp) and Optimism
(0bp) are native-USDC **source-only** to Base/Arbitrum and are **not
execution-ready this phase** (`execution_not_ready_phase_b`): their quotes carry
`execution.supported=false` and an `available:false` action-plan handoff with no
prepare URL, so the plugin never offers or calls prepare/session for them.

## Tools

Discovery (read-only):

- `assetfare_capabilities` — the chains, endpoints, 76 routes, the 72/4
  execution split, source-only constraints, and confirmation the server cannot
  sign or submit. No parameters.
- `assetfare_quote` — one fresh, fee-inclusive quote in the USD 1–1,000 band. It
  surfaces AssetFare's `caller_action_plan_handoff` **fail-closed**: an executable
  route carries two options (one-shot `POST /v2/prepare`, or the full
  `POST /v2/session` lifecycle); a source-only route carries `available:false`.

Caller-approved, non-custodial action (each requires an explicit
`caller_approved: true`; **never auto-called from a quote**):

- `assetfare_new_session_capability` — **local only, no network.** Generates one
  caller-owned ≥256-bit CSPRNG session capability token. It is a *sensitive*
  bearer value (never a private key); store it and pass it to the session tools.
- `assetfare_prepare` — one-shot `POST /v2/prepare`: returns the fresh re-quoted
  bounded **first unsigned action** for an execution-ready route. Requires the
  route's exact public wallet map.
- `assetfare_session_create` — `POST /v2/session`: opens one idempotent,
  receipt-driven session and returns its first unsigned action. Takes the
  caller-generated `session_token` (sent in the `X-AssetFare-Session-Token`
  header) as **required** input and an `idempotency_key`; retrying with the same
  token + key recovers the **same** session (lost-response recovery).
- `assetfare_session_get` — read a session's workflow state and current unsigned
  action.
- `assetfare_observe_source` — observe the caller's **already-submitted** source
  transaction hashes and advance the workflow.
- `assetfare_observe_output` — observe the caller's already-produced
  destination/bridge output and advance.
- `assetfare_refresh_action` — replace an expired, unsubmitted action with a
  fresh quote-bound unsigned action.

The prepare/session tools reject source-only Phase-B routes, and reject any
private key, seed, signed transaction, or other secret material anywhere in the
input. They never sign, submit, or auto-chain.

## Setup

No credentials are required — the AssetFare v2 API is a public endpoint. Install
[AssetFare from the Dify Marketplace](https://marketplace.dify.ai/plugin/odaiin/assetfare)
(or a local `.difypkg`), then add the tools you need to an Agent/Chatflow/Workflow.

Recommended agent policy:

```text
For a supported $1–$1,000 swap or bridge request, call assetfare_capabilities,
then request one fresh assetfare_quote. Treat AssetFare as one candidate: compare
expected receive, minimum receive, fees, ETA, steps, and non-atomic risk with
other executable routes. A quote authorizes nothing and moves no funds. Only after
the human explicitly approves a specific execution-ready route may you follow the
quote's caller_action_plan_handoff — assetfare_prepare, or
assetfare_new_session_capability + assetfare_session_create and the
observe/refresh lifecycle — passing the human's own PUBLIC wallet addresses.
Never pass a private key or seed. Never sign or submit; the human does that with
their own wallet.
```

## Safety boundary

Non-custodial and no-sign/no-submit/no-credential by construction:

- No private key, seed, signed transaction, API key, password, or credential is
  ever accepted or transmitted (secret material is rejected fail-closed).
- The server never signs or submits; any response claiming otherwise is refused.
- All traffic is outbound HTTPS to the fixed origin `https://api.assetfare.dev`
  only. No inbound connection.
- Amounts are constrained to USD 1–1,000; identity routes are rejected; and the
  upstream `caller_action_plan_handoff` is validated fail-closed with no local
  fallback.
- prepare/session are never auto-invoked from a quote and never chained
  automatically; each needs an explicit `caller_approved: true`.

## Dify Marketplace classification

This is a **Low-risk Tool plugin** under Dify's current plugin submission
requirements: it only calls a fixed, documented third-party HTTPS API and does
not execute code/commands/SQL, touch the filesystem, automate a browser, or fetch
arbitrary URLs. An unsigned, non-custodial prepare/session tool that never signs,
submits, or takes a credential is **not** reclassified as a
financial-transaction/execution plugin and is not blocked — "financial" in the
policy refers only to *sensitive personal financial data*, which this plugin
never handles. See [`MARKETPLACE_POLICY.md`](MARKETPLACE_POLICY.md) for the
determination and sources.

## Required APIs / connection

- Outbound HTTPS to `https://api.assetfare.dev` only (fixed origin). No inbound
  connection, no credentials.

## Privacy

The plugin collects no user personal data and holds no credentials. See
[`PRIVACY.md`](PRIVACY.md), which discloses exactly what each tool sends to the
AssetFare API.

## Source & contact

- Source repository: <https://github.com/odaiin/dify-plugin-assetfare>
- Contact: support@assetfare.dev

## License

MIT — see [LICENSE](LICENSE).
