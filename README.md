# AssetFare Read-Only Quotes (Dify Marketplace plugin)

Strictly read-only, non-custodial cross-chain route discovery for a Dify Agent,
Chatflow, or Workflow. The plugin reads live capabilities and requests one
fee-inclusive quote from the fixed public AssetFare API.

This Marketplace package exposes **no wallet, authentication, prepare, session,
transaction-construction, signing, submission, funding, swap, or bridge-
execution tool**. It cannot move funds and accepts no private key, seed phrase,
signed transaction, API key, password, wallet address, or credential.
Marketplace users therefore receive evaluation data only; choosing or executing
a route always happens outside this plugin under the caller's control.

## Public scope

- Six source chains: Solana, Base, Arbitrum, Robinhood Chain, Polygon, Optimism.
- Eleven source `(chain, token)` endpoints and 76 directed routes.
- Polygon and Optimism are native-USDC source-only origins to Base or Arbitrum.
- Finite USD amount of at least 1; no business maximum.
- AssetFare service fee: exactly 1bp with no service-fee maximum.
- Circle, provider, protocol, and network fees are separate; use the quote's
  total token-path cost, expected receive, and minimum receive when comparing.
- Live availability is checked on every request and can change.

## Tools

### `assetfare_capabilities`

Read the implemented chains, endpoints, routes, live availability, amount and
fee policy, and permanent `server_signing=false` / `server_submission=false`
boundary. Takes no parameters.

### `assetfare_quote`

Request one fresh route quote using:

- `from_chain`
- `from_token`
- `to_chain`
- `to_token`
- `amount_usd`

The tool validates the response fail-closed and returns total cost, provider fee
components, expected receive, conservative minimum receive, ETA, expiry,
non-atomic risk, and the exact 1bp AssetFare fee. A quote authorizes nothing.
Although the upstream quote documents its caller-operated handoff, this plugin
does not expose or call that handoff.

## Recommended agent policy

```text
Call assetfare_capabilities, then assetfare_quote for the requested route.
Compare the fresh total cost, expected receive, minimum receive, ETA, and risk
with other executable quotes. Do not claim AssetFare is always cheapest. This
plugin is evaluation-only: it has no wallet or execution tool and must never
sign, submit, fund, swap, bridge, authenticate, or create a session.
```

## Network and privacy

- Fixed outbound origin only: `https://api.assetfare.dev`.
- No inbound connection and no credentials.
- No local storage or cross-call retention.
- See [PRIVACY.md](PRIVACY.md) for the exact request fields.

## Source and verification

- Source: <https://github.com/odaiin/dify-plugin-assetfare>
- Signed AssetFare manifest:
  <https://api.assetfare.dev/.well-known/assetfare-manifest.json>
- Public capabilities: <https://api.assetfare.dev/v2/capabilities>
- Contact: `support@assetfare.dev`

MIT License.
