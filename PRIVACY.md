# Privacy Policy — AssetFare Read-Only Quotes

This Dify Marketplace plugin is strictly read-only. It collects, stores, and
logs no personal data and requires no credentials.

## Data sent

The plugin sends requests only to `https://api.assetfare.dev` over HTTPS:

- `assetfare_capabilities`: no caller parameters; reads `/v2/capabilities` and
  `/v2/status`.
- `assetfare_quote`: sends only `from_chain`, `from_token`, `to_chain`,
  `to_token`, and finite `amount_usd` to `/v2/quote`.

It does **not** request, receive, or transmit a wallet address, private key,
seed phrase, signature, signed transaction, password, API key, session token,
transaction hash, balance, account, name, or email.

The AssetFare API may receive ordinary HTTP metadata from the Dify runtime,
including its egress IP, User-Agent, and the `x-assetfare-channel: dify` label,
for security, capacity, and privacy-safe aggregate analytics. See
<https://assetfare.dev/privacy/>.

## No execution capability

The package contains no wallet authentication, prepare, session, transaction-
construction, signing, submission, funding, swap, or bridge-execution tool. A
quote authorizes nothing and cannot move funds.

## Storage and retention

The plugin stores nothing and retains nothing between calls.

Questions: `support@assetfare.dev`
Source: <https://github.com/odaiin/dify-plugin-assetfare>
