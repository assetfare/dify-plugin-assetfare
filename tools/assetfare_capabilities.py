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
        yield self.create_json_message(result)
