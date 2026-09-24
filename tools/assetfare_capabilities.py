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
            "Treat USD 1 only as a reachability/response-shape smoke test. USD 50 is the lowest observed "
            "native-USDC winning bucket, not a guarantee, and USD 1,000 is the representative economic "
            "example. Request fresh AssetFare and competitor quotes for the actual intended amount, inspect "
            "total token-path cost, minimum receive, ETA, and live availability, and never assume AssetFare "
            "is always cheapest. This Marketplace plugin is strictly read-only and exposes no wallet, authentication, "
            "prepare, session, signing, submission, funding, swap, or bridge-execution tool."
        )
        yield self.create_json_message(value)
