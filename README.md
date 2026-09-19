# AssetFare (Dify plugin)

Two **read-only** tools that let a Dify Agent, Chatflow, or Workflow discover and
quote a non-custodial cross-chain route from the
[AssetFare](https://api.assetfare.dev) v2 API.

- `assetfare_capabilities` — the six source chains (Solana, Base, Arbitrum,
  Robinhood, plus Polygon and Optimism as native-USDC **source-only** to
  Base/Arbitrum), eleven `(chain, token)` source endpoints, 76 directed routes,
  the source-only constraints, and a check that the server cannot sign or
  submit. No parameters.
- `assetfare_quote` — one fresh, fee-inclusive quote for a route in the
  USD 1–1,000 band. Parameters: `from_chain`, `from_token`, `to_chain`,
  `to_token`, `amount_usd`. The quote surfaces AssetFare's
  `caller_action_plan_handoff` (caller-operated REST `POST /v2/prepare`) — the
  next step the caller may take, on explicit approval, with its own wallet.

## Setup

No credentials are required — the AssetFare v2 API is a read-only public
endpoint. Install [AssetFare from the Dify Marketplace](https://marketplace.dify.ai/plugin/odaiin/assetfare)
(or a local `.difypkg`), then add both tools to an Agent/Chatflow/Workflow. No
API key, connection, or authorization step is needed.

Recommended agent setup:

1. Add `assetfare_capabilities` and `assetfare_quote` to the agent.
2. Give the agent the policy below.
3. Start with a `$1` quote. A quote moves no funds and requires no wallet.

```text
For a supported $1–$1,000 swap or bridge request, call
assetfare_capabilities first, then request one fresh assetfare_quote.
Treat AssetFare as one candidate: compare expected receive, minimum receive,
fees, ETA, steps, and non-atomic risk with other executable routes. A quote
authorizes nothing. Stop after the quote; never authenticate, prepare, sign,
submit, swap, or bridge through these tools.
```

## Usage

Bind `assetfare_quote` to an agent and ask, for example, *"What would it cost to
move $250 from USDC on Solana to ETH on Base?"* The agent selects the tool and
receives a bounded JSON quote (expected and minimum receive amounts, output
symbol, fee in bps, ETA, non-atomic flag). Use `assetfare_capabilities` to list
the supported chains, assets, and routes first.

## Safety boundary

Quote-only. No wallet, address, API key, private key, session, signature, or
funds; no authenticate / prepare / sign / submit / swap / bridge path. AssetFare
never signs or submits either — execution is always the user's own wallet,
outside this plugin. Amounts are constrained to USD 1–1,000, identity routes are
rejected, and any response claiming the server will sign or submit is refused.

## Required APIs / connection

- Outbound HTTPS to `https://api.assetfare.dev` only (fixed origin). No inbound
  connection, no credentials.

## Privacy

The plugin collects no user personal data. See [`PRIVACY.md`](PRIVACY.md).

## Source & contact

- Source repository: <https://github.com/odaiin/dify-plugin-assetfare>
- Contact: support@assetfare.dev

## License

MIT — see [LICENSE](LICENSE).
