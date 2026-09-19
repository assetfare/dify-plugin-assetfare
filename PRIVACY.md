# Privacy Policy — AssetFare (Dify plugin)

**The plugin collects, stores, and logs no user personal data. It never requests,
receives, or transmits a private key, seed phrase, signed transaction, password,
API key, or any other credential.**

## What the plugin sends

The plugin sends only a bounded, caller-supplied request to the AssetFare API
(`https://api.assetfare.dev`) over HTTPS, and only when the corresponding tool is
invoked:

- `assetfare_capabilities`: no parameters (a `GET` to `/v2/capabilities` and
  `/v2/status`).
- `assetfare_quote`: `from_chain`, `from_token`, `to_chain`, `to_token`, and
  `amount_usd` (a `POST` to `/v2/quote`). No wallet, no address.
- `assetfare_new_session_capability`: **no network call at all.** It generates a
  session capability token locally.
- `assetfare_prepare` and `assetfare_session_create`: on the caller's explicit
  approval only, a `POST` to `/v2/prepare` or `/v2/session` carrying the route
  intent above plus the caller's **public** wallet address(es) (and, for a Solana
  CCTP route only, a **public** event-signer key). These are public on-chain
  identifiers, never private keys or seeds.
- `assetfare_session_get`, `assetfare_observe_source`, `assetfare_observe_output`,
  `assetfare_refresh_action`: a `GET`/`POST` to the `/v2/session/{id}` lifecycle
  carrying the session id, an idempotency key, and — for observations — the
  caller's own already-submitted **public** transaction hashes.

The caller-generated **session capability token** is a sensitive bearer value. It
is sent to AssetFare only in the `X-AssetFare-Session-Token` header (the server
stores only its hash), and the plugin never logs it. It is not a private key and
cannot move funds.

No private key, seed, signed transaction, API key, credential, account, balance,
email, name, or other directly identifying personal information is requested,
read, stored, or transmitted by the plugin. Public wallet addresses and public
transaction hashes are pseudonymous on-chain identifiers supplied by the caller
solely to construct an unsigned action; the plugin retains none of them.

## Third-party service it calls

Because a plugin is responsible for what the services it calls receive, note that
the destination API — **AssetFare** (`https://api.assetfare.dev`) — receives the
route-intent request above (and, for the prepare/session tools, the caller's
public wallet addresses, public transaction hashes, and the hash of the session
token) together with standard HTTP request metadata (the calling server's egress
IP address and `User-Agent`, plus an `x-assetfare-channel: dify` header) and may
log that metadata for operational and analytics purposes. See AssetFare's terms and privacy notice at
<https://assetfare.dev> and the public status/manifest at
`https://api.assetfare.dev/.well-known/assetfare-manifest.json`.

## Storage and retention

The plugin itself stores nothing and retains nothing between calls. It holds no
credentials (none are required).

## Contact

Questions: support@assetfare.dev · <https://github.com/odaiin/dify-plugin-assetfare>
