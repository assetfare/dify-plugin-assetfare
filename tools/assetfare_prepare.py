import json
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


def _parse_wallets(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            raise AssetFareError("assetfare_wallets_invalid")
    raise AssetFareError("assetfare_wallets_invalid")


class AssetFarePrepareTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Explicit caller-approved one-shot POST /v2/prepare. Requires caller_approved
        # is literally true and the route's exact PUBLIC wallet map. Never auto-called
        # from a quote; rejects source-only Phase-B routes and any private key / seed /
        # signed transaction. AssetFare never signs or submits.
        event_signer = tool_parameters.get("event_signer_public")
        event_signer = event_signer if (isinstance(event_signer, str) and event_signer.strip()) else None
        try:
            wallets = _parse_wallets(tool_parameters.get("wallets"))
            result = AssetFareClient().prepare(
                caller_approved=tool_parameters.get("caller_approved"),
                from_chain=tool_parameters.get("from_chain", ""),
                from_token=tool_parameters.get("from_token", ""),
                to_chain=tool_parameters.get("to_chain", ""),
                to_token=tool_parameters.get("to_token", ""),
                amount_usd=tool_parameters.get("amount_usd"),
                wallets=wallets,
                event_signer_public=event_signer,
            )
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare prepare unavailable ({exc}).")
            return
        yield self.create_json_message(dict(result))
