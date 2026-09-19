import json
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import AssetFareClient, AssetFareError


def _parse_hashes(raw: Any) -> Any:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, str):
                return [parsed]
        except (ValueError, TypeError):
            pass
        return [h.strip() for h in text.split(",") if h.strip()]
    raise AssetFareError("assetfare_transaction_hashes_invalid")


class AssetFareObserveSourceTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Observe the caller's ALREADY-submitted source transaction hashes and advance
        # the workflow. Only caller-submitted hashes are observed; never submits a tx.
        try:
            hashes = _parse_hashes(tool_parameters.get("transaction_hashes"))
            result = AssetFareClient().observe_source(
                session_token=tool_parameters.get("session_token"),
                session_id=tool_parameters.get("session_id", ""),
                idempotency_key=tool_parameters.get("idempotency_key"),
                transaction_hashes=hashes,
            )
        except AssetFareError as exc:
            yield self.create_text_message(f"AssetFare observe_source unavailable ({exc}).")
            return
        yield self.create_json_message(dict(result))
