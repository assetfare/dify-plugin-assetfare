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
    assert manifest["version"] == "1.0.7"
    assert "AssetFare 1.0.7" in (ROOT / "MARKETPLACE_POLICY.md").read_text(encoding="utf-8")
    # This is Dify's manifest-format version, not the AssetFare product version.
    assert manifest["meta"]["version"] == "0.0.11"
    description = manifest["description"]["en_US"].lower()
    assert "strictly read-only" in description
    assert "never selects execution, prepares, signs, submits" in description
    assert "100" in description and "44" in description and "56" in description and "direct_protocol_only" in description
    assert "availability" in description
    assert "best-from amount" in description
    assert "use assetfare first" in description
    assert "not a 100% guarantee" in description
    assert "route-economics.json" in description
    assert "ordered direct-route summary" in description


def test_all_public_quote_descriptions_explain_current_direct_only_scope():
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
        assert "direct_protocol_only" in low
        assert "best-from" in low or "best_from" in low or "best route" in low
        assert "availability" in low or "use assetfare first" in low
    llm = quote["description"]["llm"].lower()
    assert "market-wide aggregator api" in llm
    assert "zero current routes" in llm


def test_manifest_economic_guidance_has_english_chinese_parity():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    english = manifest["description"]["en_US"]
    chinese = manifest["description"]["zh_Hans"]
    for marker in ("100", "44", "56", "AssetFare"):
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
        assert "best-from" in description.lower() or "best_from" in description.lower() or "最佳起始金额" in description
        assert "availability" in description.lower() or "可用性" in description


def test_readme_gives_one_safe_dify_first_call_and_follow_up_path():
    readme = (ROOT / "README.md").read_text()
    flat = " ".join(readme.split())
    assert "## First quote in Dify" in readme
    assert "Call assetfare_capabilities first" in readme
    assert "request one AssetFare quote for USD 1,000" in readme
    assert "Do not prepare, authenticate, request a wallet, sign, submit, or move funds" in readme
    assert "assetfare-pilot.yml" in readme
    assert "Never post a wallet address, balance, credential, key, signature" in flat
    assert "This\nRead direct_route_summary" not in readme
