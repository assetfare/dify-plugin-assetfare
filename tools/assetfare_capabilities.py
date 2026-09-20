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
            "Request assetfare_quote for a specific route (all 76 routes are execution-ready, including "
            "the 4 directional source-only Polygon/Optimism native-USDC routes). Then, ONLY on explicit caller approval, follow the "
            "quote's caller_action_plan_handoff: either assetfare_prepare (one-shot unsigned first "
            "bundle) or assetfare_new_session_capability + assetfare_session_create and the "
            "session_get/observe_source/observe_output/refresh_action lifecycle. Never auto-call any of "
            "these from a quote. This plugin never signs or submits, and never takes a private key."
        )
        yield self.create_json_message(value)
