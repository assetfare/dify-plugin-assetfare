from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


class AssetFareRefreshActionTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Replace an expired, unsubmitted session action with a fresh quote-bound
        # unsigned action. Requires the caller's session capability token. Never signs.
        try:
            result = AssetFareClient().refresh_action(
                session_token=tool_parameters.get("session_token"),
                session_id=tool_parameters.get("session_id", ""),
                idempotency_key=tool_parameters.get("idempotency_key"),
            )
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare refresh_action unavailable ({exc}).")
            return
        yield self.create_json_message(dict(result))
