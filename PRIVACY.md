# Privacy Policy — AssetFare (Dify plugin)

**The plugin does not collect, store, log, or transmit any user personal data.**

## What the plugin sends

The plugin sends only a **non-personal route intent** to the AssetFare API
(`https://api.assetfare.dev`) over HTTPS:

- `assetfare_capabilities`: no parameters (a `GET` to `/v2/capabilities` and
  `/v2/status`).
- `assetfare_quote`: `from_chain`, `from_token`, `to_chain`, `to_token`, and
  `amount_usd` (a `POST` to `/v2/quote`).

None of these are personal identifiers. No wallet address, private key, API key,
credential, account, balance, email, name, IP-derived profile, or other
personally identifiable information is requested, read, stored, or transmitted by
the plugin.

## Third-party service it calls

Because a plugin is responsible for what the services it calls receive, note that
the destination API — **AssetFare** (`https://api.assetfare.dev`) — receives the
route-intent request above together with standard HTTP request metadata (the
calling server's egress IP address and `User-Agent`, plus an
`x-assetfare-channel: dify` header) and may log that metadata for operational and
analytics purposes. See AssetFare's terms and privacy notice at
<https://assetfare.dev> and the public status/manifest at
`https://api.assetfare.dev/.well-known/assetfare-manifest.json`.

## Storage and retention

The plugin itself stores nothing and retains nothing between calls. It holds no
credentials (none are required).

## Contact

Questions: support@assetfare.dev · <https://github.com/odaiin/dify-plugin-assetfare>
