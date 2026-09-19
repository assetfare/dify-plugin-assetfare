from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


class AssetFareSessionGetTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Read a v2 session's current workflow state and current unsigned action.
        # Requires the caller's session capability token. Read-only; never submits.
        try:
            result = AssetFareClient().session_get(
                session_token=tool_parameters.get("session_token"),
                session_id=tool_parameters.get("session_id", ""),
            )
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare session_get unavailable ({exc}).")
            return
        yield self.create_json_message(dict(result))
