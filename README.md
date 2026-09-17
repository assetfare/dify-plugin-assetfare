# AssetFare (Dify plugin)

Two **read-only** tools that let a Dify Agent, Chatflow, or Workflow discover and
quote a non-custodial cross-chain route from the
[AssetFare](https://api.assetfare.dev) v2 API.

- `assetfare_capabilities` — the four supported chains, nine `(chain, token)`
  asset endpoints, 72 directed routes, and a check that the server cannot sign or
  submit. No parameters.
- `assetfare_quote` — one fresh, fee-inclusive quote for a route in the
  USD 1–1,000 band. Parameters: `from_chain`, `from_token`, `to_chain`,
  `to_token`, `amount_usd`.

## Setup

No credentials are required — the AssetFare v2 API is a read-only public
endpoint. Install the plugin from the Dify Marketplace (or a local `.difypkg`),
then add either tool to an Agent/Chatflow/Workflow. No API key, connection, or
authorization step is needed.

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
