from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


class AssetFareQuoteTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            result = AssetFareClient().get_quote(
                from_chain=tool_parameters.get("from_chain", ""),
                from_token=tool_parameters.get("from_token", ""),
                to_chain=tool_parameters.get("to_chain", ""),
                to_token=tool_parameters.get("to_token", ""),
                amount_usd=tool_parameters.get("amount_usd"),
            )
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare quote unavailable ({exc}).")
            return
        value = dict(result)
        value["caller_action_plan_handoff"] = {
            "kind": "caller_operated_rest_prepare",
            "url": "https://api.assetfare.dev/v2/prepare",
            "method": "POST",
            "requires_explicit_caller_approval": True,
            "requires_public_wallet_addresses": True,
            "request_fields": [
                "from_chain",
                "from_token",
                "to_chain",
                "to_token",
                "amount_usd",
                "wallets",
                "event_signer_public",
            ],
            "assetfare_server_signing": False,
            "assetfare_server_submission": False,
            "caller_must_verify_sign_and_submit": True,
            "note": "Guidance only: this Dify tool does not call prepare or receive a private key.",
        }
        yield self.create_json_message(value)
