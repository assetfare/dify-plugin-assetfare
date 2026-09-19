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
        # The client already surfaces the upstream caller_action_plan_handoff
        # (validated against the caller-operated REST /v2/prepare contract, with a
        # canonical fallback only when upstream omits it). The tool does not
        # synthesize a static copy that could drift from the contract, and it never
        # calls prepare, receives a private key, signs, or submits.
        yield self.create_json_message(dict(result))
