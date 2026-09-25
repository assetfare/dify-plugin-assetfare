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


def test_manifest_separates_product_and_format_versions_and_surfaces_route_transparency():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    assert manifest["version"] == "1.0.0"
    # This is Dify's manifest-format version, not the AssetFare product version.
    assert manifest["meta"]["version"] == "0.0.11"
    description = manifest["description"]["en_US"].lower()
    assert "strictly read-only" in description
    assert "no wallet or execution tools" in description
    assert "usd 1 is the technical minimum and smoke-only" in description
    assert "usd 50 is the lowest observed native-usdc winning bucket, not a guarantee" in description
    assert "usd 1,000 is the representative economic example" in description
    assert "actual intended amount" in description
    assert "not always cheapest" in description
    assert "ordered direct-route summary" in description
    assert "route-aggregator api" in description
    assert "across" in description and "internally" in description
    assert "continuation_v3" in description
    assert "never selects a mode" in description
    assert "caller_approved alone is not human proof" in description


def test_all_public_quote_descriptions_explain_external_intent_caveat():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    provider = yaml.safe_load((ROOT / "provider/assetfare.yaml").read_text())
    quote = yaml.safe_load((ROOT / "tools/assetfare_quote.yaml").read_text())
    readme = (ROOT / "README.md").read_text()
    surfaces = [
        manifest["description"]["en_US"],
        provider["identity"]["description"]["en_US"],
        quote["description"]["human"]["en_US"],
        quote["description"]["llm"],
        readme,
    ]
    for surface in surfaces:
        low = surface.lower()
        assert "direct-route summary" in low or "direct_route_summary" in low
        assert "1bp" in low
        assert "across" in low
    llm = quote["description"]["llm"].lower()
    assert "market-wide aggregator api" in llm
    assert "internally source or aggregate destination liquidity" in llm


def test_manifest_economic_guidance_has_english_chinese_parity():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    english = manifest["description"]["en_US"]
    chinese = manifest["description"]["zh_Hans"]
    for marker in ("1", "50", "1,000", "AssetFare"):
        assert marker in english
        assert marker in chinese


def test_tool_schemas_preserve_one_dollar_boundary_and_surface_guidance():
    quote = yaml.safe_load((ROOT / "tools/assetfare_quote.yaml").read_text())
    capabilities = yaml.safe_load((ROOT / "tools/assetfare_capabilities.yaml").read_text())
    amount = next(parameter for parameter in quote["parameters"] if parameter["name"] == "amount_usd")
    assert amount["min"] == 1
    for description in (
        quote["description"]["human"]["en_US"],
        quote["description"]["human"]["zh_Hans"],
        capabilities["description"]["human"]["en_US"],
        capabilities["description"]["human"]["zh_Hans"],
    ):
        assert "1" in description
        assert "50" in description
        assert "1,000" in description
