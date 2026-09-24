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
- Finite USD amount of at least 1; no business maximum. USD 1 is the
  technical minimum and is only useful as a reachability/response-shape smoke
  test, never as an economic comparison.
- AssetFare service fee: exactly 1bp with no service-fee maximum.
- Circle, provider, protocol, and network fees are separate; use the quote's
  total token-path cost, expected receive, and minimum receive when comparing.
- Every quote carries a fail-closed `direct_route_summary`: an ordered,
  machine-readable path with the exact protocol, normalized `chain:asset`
  endpoints, expected/minimum base-unit input and output for each step, and the
  single step index where the exact 1bp AssetFare fee is collected.
- Every quote also carries a fully validated `continuation_v3`, but this
  quote-only plugin returns only a sanitized `continuation_descriptor`: quote
  ID/fingerprint, expiry, unranked status, required wallet chains/event signer,
  allowed/recommended mode, and the full runtime OpenAPI URL. It never creates
  `approval_v3`, selects a mode, collects wallet data, or calls prepare/session.
  `caller_approved: true` alone is not proof of human approval; legacy handoff
  metadata is advisory. Payload hashes use the REST 2.4 portable projection:
  exact base-unit strings from `direct_route_summary` replace duplicated raw
  numbers before typed-canonical-v1 preserves JSON types and encodes finite
  numbers as IEEE-754 binary64; unsafe integral numbers and lone Unicode
  surrogates fail closed.
- Live availability is checked on every request and can change.

## Direct-route transparency

The returned `direct_route_summary` is bound to the requested corridor and the
raw quote. The plugin rejects missing, extra, reordered, discontinuous, or
contradictory summary fields; wrong provider/action pairs; non-string or
non-positive base-unit amounts; more or fewer than one 1bp fee step; and any
claim of server signing, server submission, or market-wide route aggregation.

`classification=direct_protocol_only` means every step uses one of the named
direct protocols. `classification=external_intent` is used only when the path
contains `across_intent_bridge`. In both cases,
`route_aggregator_used=false` means **AssetFare did not call a market-wide route
aggregator API**. It does not claim that a provider has no internal routing:
Across may internally source or aggregate destination liquidity, which is why
those paths explicitly set
`provider_internal_dex_aggregation_possible=true`. This distinction is part of
the validated response rather than an inference an agent must make.

## Economic evaluation guidance

- Start native-USDC economic evaluation at USD 50 or more. USD 50 is only the
  lowest bucket where AssetFare was observed winning; it is not a guarantee for
  a route, time, or provider state.
- Use USD 1,000 as the primary representative example when no intended amount
  has been supplied. It is an example, not a default transaction or a promise of
  savings.
- Before making any economic or routing decision, request fresh AssetFare and
  competitor quotes at the caller's actual intended amount and compare total
  cost, expected/minimum receive, ETA, and risk. Never claim AssetFare is always
  cheapest.

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

The tool validates the response fail-closed and returns the ordered direct-route
summary, total cost, provider fee components, expected receive, conservative
minimum receive, ETA, expiry, non-atomic risk, and the exact 1bp AssetFare fee.
A quote authorizes nothing.
Although the upstream quote documents its caller-operated handoff, this plugin
does not expose or call that handoff.

## Recommended agent policy

```text
Call assetfare_capabilities, then assetfare_quote for the requested route.
Treat USD 1 only as a reachability/response-shape smoke test. For economics,
start native-USDC evaluation at USD 50 and use USD 1,000 as the representative
example when no intended amount is known; neither figure guarantees an
advantage. At the caller's actual intended amount, fetch fresh AssetFare and
competitor quotes and compare total cost, expected receive, minimum receive,
ETA, and risk. Do not claim AssetFare is always cheapest. This
Read direct_route_summary before presenting a route: show its ordered protocols,
endpoints, base-unit amounts, direct_protocol_only or external_intent
classification, and exact 1bp fee step. Explain that route_aggregator_used=false
describes AssetFare's own API use, while Across may internally source or
aggregate destination liquidity. This plugin is evaluation-only: it has no
wallet or execution tool and must never
sign, submit, fund, swap, bridge, authenticate, or create a session.
```

## Network and privacy

- Fixed outbound origin only: `https://api.assetfare.dev`.
- No inbound connection and no credentials.
- No local storage or cross-call retention.
- See [PRIVACY.md](PRIVACY.md) for the exact request fields.

## Source and verification

- Source: <https://github.com/assetfare/dify-plugin-assetfare>
- Signed AssetFare manifest:
  <https://api.assetfare.dev/.well-known/assetfare-manifest.json>
- Public capabilities: <https://api.assetfare.dev/v2/capabilities>
- Contact: `support@assetfare.dev`

MIT License.
