from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION_STEMS = {
    "assetfare_prepare",
    "assetfare_new_session_capability",
    "assetfare_session_create",
    "assetfare_session_get",
    "assetfare_observe_source",
    "assetfare_observe_output",
    "assetfare_refresh_action",
}


def test_marketplace_provider_exposes_exactly_two_read_only_tools():
    provider = yaml.safe_load((ROOT / "provider/assetfare.yaml").read_text())
    assert provider["tools"] == [
        "tools/assetfare_capabilities.yaml",
        "tools/assetfare_quote.yaml",
    ]


def test_no_action_tool_source_or_schema_is_shipped():
    shipped = {path.stem for path in (ROOT / "tools").glob("assetfare_*")}
    assert ACTION_STEMS.isdisjoint(shipped)
    assert shipped == {"assetfare_capabilities", "assetfare_quote"}


def test_client_has_no_action_methods_and_no_old_fee_maximum():
    source = (ROOT / "utils/client.py").read_text()
    for method in (
        "def prepare(",
        "def session_create(",
        "def session_get(",
        "def observe_source(",
        "def observe_output(",
        "def refresh_action(",
        "def new_session_capability(",
    ):
        assert method not in source
    assert "min(amount/10_000,5.0)" not in source
    assert '"estimated_usd":amount/10_000' in source


def test_manifest_is_read_only_008():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    assert manifest["version"] == "0.0.8"
    assert manifest["meta"]["version"] == "0.0.8"
    description = manifest["description"]["en_US"].lower()
    assert "strictly read-only" in description
    assert "no wallet or execution tools" in description
    assert "no business maximum" in description
    assert "no service-fee maximum" in description

