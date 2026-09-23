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
            "Request assetfare_quote for a specific implemented route, inspect total token-path cost, "
            "minimum receive, ETA, and live availability, then compare it with other fresh executable "
            "quotes. This Marketplace plugin is strictly read-only and exposes no wallet, authentication, "
            "prepare, session, signing, submission, funding, swap, or bridge-execution tool."
        )
        yield self.create_json_message(value)
