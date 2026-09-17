from typing import Any

from dify_plugin import ToolProvider


class AssetFareProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # This provider requires no credentials (read-only public API). Validation
        # is a no-op and performs no network call.
        return None
