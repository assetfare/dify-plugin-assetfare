from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareError, new_session_capability


class AssetFareNewSessionCapabilityTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Local-only: generate one caller-owned >=256-bit CSPRNG session capability
        # token. Makes NO network call. The token is a SENSITIVE bearer capability
        # (never a private key); the caller stores it and passes it to
        # assetfare_session_create and every session get/observe/refresh call.
        try:
            result = new_session_capability()
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare session capability unavailable ({exc}).")
            return
        yield self.create_json_message(dict(result))
