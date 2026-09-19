from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


class AssetFareObserveOutputTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Observe the caller's ALREADY-produced destination/bridge output and advance
        # the workflow. Never submits a transaction.
        tx = tool_parameters.get("transaction_hash")
        tx = tx if (isinstance(tx, str) and tx.strip()) else None
        try:
            result = AssetFareClient().observe_output(
                session_token=tool_parameters.get("session_token"),
                session_id=tool_parameters.get("session_id", ""),
                idempotency_key=tool_parameters.get("idempotency_key"),
                transaction_hash=tx,
            )
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare observe_output unavailable ({exc}).")
            return
        yield self.create_json_message(dict(result))
