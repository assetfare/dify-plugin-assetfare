from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


class AssetFareCapabilitiesTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            result = AssetFareClient().get_capabilities()
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare capabilities unavailable ({exc}).")
            return
        value = dict(result)
        value["next_step"] = (
            "Request assetfare_quote for a specific route; then, only on explicit caller "
            "approval, follow the quote's caller_action_plan_handoff to the caller-operated "
            "POST /v2/prepare. This plugin never prepares, signs, or submits."
        )
        yield self.create_json_message(value)
