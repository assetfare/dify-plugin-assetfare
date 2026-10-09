import copy
import hashlib
import json
import os
import struct
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.client import AssetFareClient, AssetFareError, _AVAILABILITY_ONLY_ROUTES, _COMPARE_REQUIRED_ROUTES, _PRICE_VERIFIED_ROUTES, _ROUTES, _SOURCE_ONLY_ENDPOINTS, _SOURCE_ONLY_ROUTES

# Fixed "now" so the fixed-timestamp quote fixture (as_of 2026-09-17T00:00:00Z,
# ttl 30) is fresh (10s in). Tests inject this so they do not depend on wall time.
FIXED_NOW = datetime(2026, 9, 17, 0, 0, 10, tzinfo=UTC)

PREPARE_URL = "https://api.assetfare.dev/v2/prepare"
SESSION_URL = "https://api.assetfare.dev/v2/session"
QUOTE_PAYLOAD_SHA256_SPEC = (
    "sha256(AssetFare typed-canonical-v1 bytes of the quote without continuation_v3 after exact base-unit "
    "substitution: n=null; t/f=boolean; d=<IEEE-754 binary64 big-endian 16 lowercase hex> for each finite JSON "
    "number; s=<UTF-8 byte length>:<Unicode scalar text with lone surrogates forbidden>; a=<count>:[items]; "
    "o=<count>:{UTF-8-byte-sorted string-key/value pairs}; every non-substituted integral JSON number must be "
    "within +/-9007199254740991; substituted paths are "
    "intent.estimated_input_base, route.input_base, route.expected_output_base, route.minimum_output_base, and "
    "every route.steps[i].expected_input_base/floor_input_base/expected_output_base/minimum_output_base from "
    "direct_route_summary exact decimal strings)"
)
REQUEST_FIELDS = [
    "caller_approved",
    "from_chain",
    "from_token",
    "to_chain",
    "to_token",
    "amount_usd",
    "wallets",
    "event_signer_public",
]

EVM = "0x" + "a" * 40
SOL = "So11111111111111111111111111111111111111112"

EVALUATION_GUIDANCE = {
    "schema_version": 5,
    "route_minimum_usd": 1,
    "reachability_smoke_usd": 1,
    "reachability_smoke_scope": "connectivity_only_not_economic_evaluation",
    "route_specific_guidance": {
        "version": "assetfare-route-economic-guidance-v4",
        "url": "https://assetfare.dev/route-economics.json",
        "required_on_every_quote": True,
        "verified_best_from_only": True,
        "nullable_when_unverified": True,
        "controls_recommendation_only_when_verified": True,
        "values_change_with_market": True,
        "catalog_routes": 100,
        "public_active_routes": 100,
        "public_inactive_routes": 0,
        "availability_only_routes": 10,
        "compare_required_routes": 46,
        "nonrecommended_routes": 56,
        "economic_guidance_blocks_prepare_or_session": False,
    },
    "documentation_example_usd": 1000,
    "documentation_example_scope": "example_only_not_route_guidance_or_minimum",
    "sol_input_caveat": (
        "SOL-input routes add a source swap, so compare their full fee-inclusive route economics separately."
    ),
    "historical_observation": {
        "route": "solana:USDC->base:USDC",
        "observed_competitive_bucket_usd": 500,
        "evidence_as_of": "2026-09-29",
        "not_generalizable": True,
    },
    "not_a_minimum": True,
    "not_guaranteed_best": True,
    "always_compare_fresh_at_intended_amount": True,
}

PUBLIC_EVALUATION_GUIDANCE = EVALUATION_GUIDANCE

ECONOMIC_GUIDANCE = {
    "version": "assetfare-route-economic-guidance-v4",
    "as_of": "2026-10-09",
    "route_count": 100,
    "public_active_route_count": 100,
    "public_inactive_route_count": 0,
    "verified_best_from_route_count": 44,
    "availability_only_route_count": 10,
    "compare_required_route_count": 46,
    "nonrecommended_route_count": 56,
    "currency": "USD",
    "technical_quote_minimum_usd": 1,
    "economic_guidance_is_non_enforcing": True,
    "economic_guidance_blocks_execution": False,
    "amount_is_never_rejected_by_economic_guidance": True,
    "values_change_with_market": True,
    "fresh_quote_and_caller_decision_control": True,
    "update_policy": "daily_measurement_with_three_day_recommendation_hysteresis_availability_separate",
    "first_use_zero_allowance_scenario": True,
    "expected_output_ranking": True,
    "incomplete_cost_never_promoted": True,
    "tested_ceiling_usd": 10000,
    "advisory_start_distribution": {"50":8,"100":5,"250":3,"500":6,"1000":2,"2500":5,"5000":12,"10000":3},
    "recommendation_status_counts": {"active_price_verified":44,"active_availability_only":10,"active_compare_required":46},
}
ROUTE_ECONOMIC_GUIDANCE = {"advisory_start_usd":1000,"best_from_usd":1000,"best_from_verified":True,"availability_only":False,"public_activation_status":"active_price_verified","public_active":True,"recommendation_status":"active_price_verified","recommended_action":"price_recommended_at_or_above_best_from_otherwise_no_price_recommendation","confidence":"paired_all_in_snapshot","basis":"offline_fixture_only","economic_selection_policy":"advisory_only_no_execution_gate","economic_guidance_blocks_execution":False,"tested_amounts_usd":[50,100,250,500,1000,2500,5000,10000],"tested_ceiling_usd":10000,"not_an_execution_minimum":True,"not_a_best_price_guarantee":True,"fresh_quote_required":True,"quoted_amount_usd":1000,"price_recommended_for_amount":True}


def af(**kwargs):
    kwargs.setdefault("utcnow", lambda: FIXED_NOW)
    return AssetFareClient(**kwargs)


ENDPOINTS = [
    ("solana", "SOL"),
    ("solana", "USDC"),
    ("solana", "USDG"),
    ("base", "USDC"),
    ("base", "ETH"),
    ("arbitrum", "ETH"),
    ("arbitrum", "USDC"),
    ("robinhood", "ETH"),
    ("robinhood", "USDG"),
    ("optimism", "USDC"),
    ("polygon", "USDC"),
    ("ethereum", "USDC"),
    ("hyperevm", "USDC"),
    ("xlayer", "USDC"),
    ("sei", "USDC"),
    ("sonic", "USDC"),
    ("monad", "USDC"),
    ("avalanche", "USDC"),
    ("cronos", "USDC"),
    ("injective", "USDC"),
    ("linea", "USDC"),
    ("aptos", "USDC"),
    ("unichain", "USDC"),
    ("ink", "USDC"),
]

# Chains that may only be a source, never a destination. Their exact supported
# corridors are defined by the production client's source-only route set.
SOURCE_ONLY_CHAINS = {"ethereum", "hyperevm", "optimism", "polygon", "sei", "sonic", "xlayer", "monad", "avalanche", "cronos", "injective", "linea", "aptos", "unichain", "ink"}
DESTINATION_ENDPOINTS = [(c, t) for (c, t) in ENDPOINTS if c not in SOURCE_ONLY_CHAINS]


def all_routes():
    """Return the exact 100 available execution-ready directed routes."""
    return [tuple(route.replace("->", ":").split(":")) for route in sorted(_ROUTES)]


def valid_caps():
    return {
        "status": "capped_public_agent_release",
        "public_api_enabled": True,
        "directed_conversion_routes": 100,
        "unsigned_route_plans_ready": 100,
        "execution_ready_routes": 100,
        "execution_implemented_routes": 100,
        "currently_prepare_ready_routes": 100,
        "temporarily_unavailable_routes": [],
        "temporarily_unavailable_route_count": 0,
        "execution_availability": {"status":"available","provider":"circle_iris","provider_dependent_routes":96,"recent_fee_snapshot_usable":True,"guarantees_future_availability":False},
        "phase_b_blocked_routes": 0,
        "server_signing": False,
        "server_submission": False,
        "chains": ["aptos", "arbitrum", "avalanche", "base", "cronos", "ethereum", "hyperevm", "injective", "ink", "linea", "monad", "optimism", "polygon", "robinhood", "sei", "solana", "sonic", "unichain", "xlayer"],
        "asset_endpoints": [{"chain": c, "token": t} for c, t in ENDPOINTS],
        "source_only_asset_endpoints": [{"chain": chain, "token": token} for chain, token in sorted(_SOURCE_ONLY_ENDPOINTS)],
        "source_only_routes": sorted(_SOURCE_ONLY_ROUTES),
        "blocked_source_only_routes": [],
        "amount_usd": {"minimum": 1, "maximum": None, "policy": "no_business_maximum"},
        "evaluation_guidance": dict(EVALUATION_GUIDANCE),
        "economic_guidance": copy.deepcopy(ECONOMIC_GUIDANCE),
        "route_product_policy": {"primary_direct_route_count":100,"external_coverage_only_route_count":0,"paxos_direct_ingress_routes":[],"active_route_count":100,"inactive_route_count":0,"inactive_routes":[],"price_recommended_route_count":44,"availability_only_route_count":10,"compare_required_route_count":46,"nonrecommended_route_count":56,"economic_guidance_blocks_execution":False,"amount_conditioned_routes":{route:1000 for route in _PRICE_VERIFIED_ROUTES},"economic_guidance": copy.deepcopy(ECONOMIC_GUIDANCE), "economic_guidance_url": "https://assetfare.dev/route-economics.json","automatic_external_fallback_forbidden":True},
    }


def valid_status():
    return {"status": "capped_public_agent_release", "server_signing": False, "server_submission": False}


def prepare_option():
    return {
        "kind": "one_shot_first_unsigned_bundle",
        "method": "POST",
        "url": PREPARE_URL,
        "requires_explicit_caller_approval": True,
        "requires_public_wallet_addresses": True,
        "assetfare_never_signs_submits_or_auto_calls": True,
        "note": "Stateless: returns only the first unsigned bundle.",
    }


def session_option():
    return {
        "kind": "caller_approved_full_workflow_session",
        "method": "POST",
        "url": SESSION_URL,
        "lifecycle_urls": {
            "create": {"method": "POST", "url": SESSION_URL},
            "read": {"method": "GET", "url": SESSION_URL + "/{session_id}"},
            "observe_source": {"method": "POST", "url": SESSION_URL + "/{session_id}/observe-source"},
            "observe_output": {"method": "POST", "url": SESSION_URL + "/{session_id}/observe-output"},
            "refresh_action": {"method": "POST", "url": SESSION_URL + "/{session_id}/refresh-action"},
        },
        "requires_explicit_caller_approval": True,
        "requires_public_wallet_addresses": True,
        "assetfare_never_signs_submits_or_auto_calls": True,
        "note": "Idempotent multi-step lifecycle.",
    }


def executable_handoff():
    return {
        "kind": "caller_operated_rest_prepare",
        "url": PREPARE_URL,
        "method": "POST",
        "requires_explicit_caller_approval": True,
        "requires_public_wallet_addresses": True,
        "request_fields": list(REQUEST_FIELDS),
        "assetfare_server_signing": False,
        "assetfare_server_submission": False,
        "caller_must_verify_sign_and_submit": True,
        "requires_fresh_requote": True,
        "automatic_prepare_call_forbidden": True,
        "options": [prepare_option(), session_option()],
        "note": "Guidance only: the quote endpoint does not prepare, sign, submit, or receive a private key.",
        "available": True,
    }


def prepare_option_v2():
    return {**prepare_option(), "preview_or_manual_first_action_only": True, "not_a_session": True, "do_not_start_session_after_submission": True}


def session_option_v2():
    return {**session_option(), "recommended_for_multistep": True}


def executable_handoff_v2():
    return {
        "kind": "caller_operated_rest_prepare", "url": PREPARE_URL, "method": "POST",
        "requires_explicit_caller_approval": True, "requires_public_wallet_addresses": True,
        "request_fields": list(REQUEST_FIELDS), "assetfare_server_signing": False, "assetfare_server_submission": False,
        "caller_must_verify_sign_and_submit": True, "requires_fresh_requote": True, "automatic_prepare_call_forbidden": True,
        "schema_version": 2, "selection": "choose_exactly_one", "mutually_exclusive": True, "do_not_call_both": True,
        "selection_before_signing": True, "once_any_action_submitted_do_not_start_other_mode": True,
        "enforcement": "advisory_caller_side", "options": [prepare_option_v2(), session_option_v2()],
        "note": "Machine-readable v2.", "available": True,
    }


def direct_route_fixture(fc, ft, tc, tt, *, source_only=False):
    if source_only:
        mode = f"{fc}_source_cctp" if fc in {"optimism", "polygon"} else "aptos_move_cctp_direct" if fc == "aptos" else "cctp_direct_composition"
        raw_steps = [{
            "index": 0, "kind": "direct_bridge", "provider": "circle_cctp",
            "from": fc, "to": tc, "asset": "USDC", "route_fee_bps": 1,
            "expected_input_base": 1, "floor_input_base": 1,
            "expected_output_base": 1, "minimum_output_base": 1,
        }]
        if fc in {"optimism", "polygon"}:
            raw_steps.append({
                "index": 1, "kind": "direct_receive", "provider": "circle_cctp_receive",
                "chain": tc, "from": "USDC", "to": "USDC", "source_chain": fc,
                "cctp_mode": "no_forward", "destination_native_gas_required": True,
                "route_fee_bps": 0, "expected_input_base": 1, "floor_input_base": 1,
                "expected_output_base": 1, "minimum_output_base": 1,
            })
    elif tc == "robinhood" and fc != "robinhood":
        mode = "robinhood_paxos_ingress_composition"
        raw_steps = [
            {"index":0,"kind":"direct_bridge","provider":"circle_cctp","from":fc,"to":"solana","asset":"USDC","route_fee_bps":1,"minimum_guard_bps":17,"expected_input_base":1,"floor_input_base":1,"expected_output_base":2,"minimum_output_base":1},
            {"index":1,"kind":"direct_swap","provider":"orca_whirlpool","chain":"solana","from":"USDC","to":"USDG","route_fee_bps":0,"minimum_guard_bps":17,"expected_input_base":2,"floor_input_base":1,"expected_output_base":3,"minimum_output_base":1},
            {"index":2,"kind":"direct_bridge","provider":"paxos_usdg_layerzero_oft","from":"solana","to":"robinhood","asset":"USDG","route_fee_bps":0,"minimum_guard_bps":16,"expected_input_base":3,"floor_input_base":1,"expected_output_base":4,"minimum_output_base":1},
        ]
    else:
        mode = "cctp_direct_composition"
        source_swap_provider = "raydium_clmm" if fc == "solana" and ft == "SOL" else "orca_whirlpool" if fc == "solana" else "uniswap_v3"
        raw_steps = [
            {
                "index": 0, "kind": "direct_swap", "provider": source_swap_provider,
                "chain": fc, "from": ft, "to": "USDC", "route_fee_bps": 1,
                "expected_input_base": 1, "floor_input_base": 1,
                "expected_output_base": 2, "minimum_output_base": 1,
            },
            {
                "index": 1, "kind": "direct_bridge", "provider": "circle_cctp",
                "from": fc, "to": tc, "asset": "USDC", "route_fee_bps": 0,
                "expected_input_base": 2, "floor_input_base": 1,
                "expected_output_base": 3, "minimum_output_base": 1,
            },
            {
                "index": 2, "kind": "direct_swap", "provider": "uniswap_v3",
                "chain": tc, "from": "USDC", "to": tt, "route_fee_bps": 0,
                "expected_input_base": 3, "floor_input_base": 1,
                "expected_output_base": 4, "minimum_output_base": 1,
            },
        ]
    summary_steps = []
    for row in raw_steps:
        bridge = row["kind"] == "direct_bridge"
        receive = row["kind"] == "direct_receive"
        external = row["provider"] == "across_intent_bridge"
        source_asset = row.get("from_asset") if external else row.get("asset")
        destination_asset = row.get("to_asset") if external else row.get("asset")
        summary_steps.append({
            "index": row["index"],
            "action": "bridge" if bridge else "receive" if receive else "swap",
            "provider": row["provider"],
            "from": f"{row['from']}:{source_asset}" if bridge else f"{row['chain']}:{row['from']}",
            "to": f"{row['to']}:{destination_asset}" if bridge else f"{row['chain']}:{row['to']}",
            "expected_input_base": str(row["expected_input_base"]),
            "minimum_input_base": str(row["floor_input_base"]),
            "expected_output_base": str(row["expected_output_base"]),
            "minimum_output_base": str(row["minimum_output_base"]),
            "assetfare_fee_bps": row["route_fee_bps"],
            "direct_protocol": not external,
            "external_intent_protocol": external,
            "aggregator_api_used": False,
            **({"minimum_guard_bps": row["minimum_guard_bps"]} if "minimum_guard_bps" in row else {}),
        })
    route_name = f"{fc}:{ft}->{tc}:{tt}"
    summary = {
        "version": "assetfare-direct-route-summary-v1",
        "route": route_name,
        "from": f"{fc}:{ft}",
        "to": f"{tc}:{tt}",
        "classification": "direct_protocol_only",
        "mode": mode,
        "product_classification": "primary_direct",
        "economic_eligibility": "not_asserted_by_capability",
        "public_execution_eligible": True,
        "primary_selection_eligible": True,
        "route_minimum_guard_bps": 50 if mode == "robinhood_paxos_ingress_composition" else None,
        "route_aggregator_used": False,
        "external_intent_protocol_used": False,
        "provider_internal_dex_aggregation_possible": False,
        "assetfare_fee_bps": 1,
        "fee_collection_step_index": next(index for index, row in enumerate(raw_steps) if row["route_fee_bps"] == 1),
        "server_signing": False,
        "server_submission": False,
        "step_count": len(summary_steps),
        "steps": summary_steps,
    }
    route = {
        "route": route_name,
        "mode": mode,
        "product_classification": "primary_direct",
        "economic_eligibility": "not_asserted_by_capability",
        "public_execution_eligible": True,
        "primary_selection_eligible": True,
        "route_minimum_guard_bps": 50 if mode == "robinhood_paxos_ingress_composition" else None,
        "input_base": 1,
        "expected_output_base": raw_steps[-1]["expected_output_base"],
        "minimum_output_base": raw_steps[-1]["minimum_output_base"],
        "steps": raw_steps,
        "quote_latency_ms": 120,
        "aggregator_api_used": False,
        "external_intent_protocol_used": summary["external_intent_protocol_used"],
        "server_signing": False,
        "server_submission": False,
    }
    return route, summary


def valid_quote(fc, ft, tc, tt, amount, *, source_only=False, fee=1):
    route, direct_route_summary = direct_route_fixture(fc, ft, tc, tt, source_only=source_only)
    offer_fee = fee
    collectible = fee == 1
    fee_steps = [0] if fee == 1 else []
    execution = {
        "supported": True,
        "first_unsigned_action_supported": True,
        "future_actions_require_verified_receipts": True,
        "blocker": None,
    }
    handoff = executable_handoff()
    route_guidance=copy.deepcopy(ROUTE_ECONOMIC_GUIDANCE)
    route_guidance.update(
        quoted_amount_usd=amount,
        price_recommended_for_amount=amount >= route_guidance["best_from_usd"],
    )
    if f"{fc}:{ft}->{tc}:{tt}" in _AVAILABILITY_ONLY_ROUTES:
        route_guidance.update(advisory_start_usd=None,best_from_usd=None,best_from_verified=False,availability_only=True,public_activation_status="active_availability_only",recommendation_status="active_availability_only",recommended_action="available_no_cheapest_price_claim_compare_if_price_matters",confidence="availability_only_no_price_claim",price_recommended_for_amount=False)
    elif f"{fc}:{ft}->{tc}:{tt}" in _COMPARE_REQUIRED_ROUTES:
        route_guidance.update(advisory_start_usd=None,best_from_usd=None,best_from_verified=False,availability_only=False,public_activation_status="active_compare_required",recommendation_status="active_compare_required",recommended_action="available_not_price_recommended_compare_if_price_matters",confidence="paired_all_in_snapshot_competitor_cheaper_through_tested_ceiling",price_recommended_for_amount=False)
    quote = {
        "quote_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
        "status": "capped_public_agent_release",
        "as_of": "2026-09-17T00:00:00Z",
        "ttl_seconds": 30,
        "economic_guidance": route_guidance,
        "intent": {"from": f"{fc}:{ft}", "to": f"{tc}:{tt}", "amount_usd": amount, "estimated_input_base": 1},
        "offer": {
            "expected_receive_amount": 0.061,
            "estimated_min_receive_amount": 0.0607,
            "output_symbol": tt,
            "expected_receive_usd": amount,
            "estimated_min_receive_usd": amount - 0.02,
            "assetfare_fee_bps": offer_fee,
            "fee_modeled_bps": offer_fee,
            "fee_collectible_now": collectible,
            "fee_blocker": None,
            "fee_collection_steps": fee_steps,
            "fee_collection": "only_on_eligible_successful_executor_step",
            "estimated_time_seconds": 23,
        },
        "route": route,
        "risk": {
            "non_atomic": True,
            "fresh_quote_required_each_step": True,
            "external_intent_protocol_used": direct_route_summary["external_intent_protocol_used"],
            "provider_internal_dex_aggregation_possible": direct_route_summary[
                "provider_internal_dex_aggregation_possible"
            ],
            "server_signing": False,
            "server_submission": False,
        },
        "direct_route_summary": direct_route_summary,
        "execution": execution,
        "caller_action_plan_handoff": handoff,
        "caller_action_plan_handoff_v2": executable_handoff_v2(),
        "handoff_schema_version": 2,
    }
    return add_continuation(quote)


def _sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _typed_canonical(value):
    if value is None:
        return b"n"
    if value is True:
        return b"t"
    if value is False:
        return b"f"
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("unsafe integer")
        return b"d" + struct.pack(">d", float(value)).hex().encode()
    if isinstance(value, float):
        if value.is_integer() and abs(value) > 9_007_199_254_740_991:
            raise ValueError("unsafe integer")
        return b"d" + struct.pack(">d", value).hex().encode()
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("invalid unicode")
        encoded = value.encode()
        return b"s" + str(len(encoded)).encode() + b":" + encoded
    if isinstance(value, list):
        return b"a" + str(len(value)).encode() + b":[" + b"".join(map(_typed_canonical, value)) + b"]"
    if any(any(0xD800 <= ord(character) <= 0xDFFF for character in key) for key in value):
        raise ValueError("invalid unicode")
    items = sorted(value.items(), key=lambda item: item[0].encode())
    return b"o" + str(len(items)).encode() + b":{" + b"".join(
        _typed_canonical(key) + _typed_canonical(item) for key, item in items
    ) + b"}"


def _quote_payload_projection(quote):
    value = copy.deepcopy({key: item for key, item in quote.items() if key != "continuation_v3"})
    summary_steps = value["direct_route_summary"]["steps"]
    raw_steps = value["route"]["steps"]
    value["intent"]["estimated_input_base"] = summary_steps[0]["expected_input_base"]
    value["route"]["input_base"] = summary_steps[0]["expected_input_base"]
    value["route"]["expected_output_base"] = summary_steps[-1]["expected_output_base"]
    value["route"]["minimum_output_base"] = summary_steps[-1]["minimum_output_base"]
    for raw, exact in zip(raw_steps, summary_steps, strict=True):
        raw["expected_input_base"] = exact["expected_input_base"]
        raw["floor_input_base"] = exact["minimum_input_base"]
        raw["expected_output_base"] = exact["expected_output_base"]
        raw["minimum_output_base"] = exact["minimum_output_base"]
    return value


def add_continuation(quote):
    quote.pop("continuation_v3", None)
    summary = quote["direct_route_summary"]
    ttl = quote["ttl_seconds"]
    intent = quote["intent"]
    chains = sorted(
        {
            endpoint.split(":", 1)[0]
            for step in summary["steps"]
            for endpoint in (step["from"], step["to"])
        }
    )
    signer = any(
        step["provider"] == "circle_cctp" and step["from"].startswith("solana:")
        for step in summary["steps"]
    )
    modes = ["session"] if summary["step_count"] > 1 else ["one_shot", "session"]
    issued_at = "2026-09-17T00:00:00.000Z"
    expires_at = "2026-09-17T00:00:30.000Z"
    amount_decimal = format(intent["amount_usd"], "f").rstrip("0").rstrip(".") or "0"
    summary_hash = _sha256(summary)
    payload_hash = hashlib.sha256(_typed_canonical(_quote_payload_projection(quote))).hexdigest()
    bounds = {
        "minimum": str(intent["estimated_input_base"]),
        "maximum": str(intent["estimated_input_base"]),
    }
    claim = {
        "version": "assetfare-quote-bound-continuation-v3",
        "quote_id": quote["quote_id"],
        "issued_at": issued_at,
        "expires_at": expires_at,
        "ttl_seconds": str(ttl),
        "intent": {
            "from": intent["from"],
            "to": intent["to"],
            "amount_usd_decimal": amount_decimal,
            "estimated_input_base": str(intent["estimated_input_base"]),
        },
        "direct_route_summary_sha256": summary_hash,
        "quote_payload_sha256": payload_hash,
        "quote_payload_sha256_spec": QUOTE_PAYLOAD_SHA256_SPEC,
        "input_base_bounds": bounds,
        "minimum_output_base": str(quote["route"]["minimum_output_base"]),
        "required_wallet_chains": chains,
        "event_signer_public_required": signer,
        "step_count": str(summary["step_count"]),
        "allowed_modes": modes,
        "server_signing": False,
        "server_submission": False,
    }
    quote["continuation_v3"] = {
        "version": "assetfare-quote-bound-continuation-v3",
        "enforcement": "server_enforced_quote_binding",
        "selection_status": "unranked_candidate",
        "automatic_selection_forbidden": True,
        "caller_approved_boolean_is_not_human_proof": True,
        "quote_id": quote["quote_id"],
        "quote_fingerprint": _sha256(claim),
        "quote_fingerprint_spec": "sha256(UTF-8 sorted-key compact JSON of quote_fingerprint_claim; every numeric claim is a non-exponent decimal string)",
        "quote_fingerprint_claim": claim,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "ttl_seconds": ttl,
        "intent": dict(intent),
        "direct_route_summary_sha256": summary_hash,
        "quote_payload_sha256": payload_hash,
        "quote_payload_sha256_spec": QUOTE_PAYLOAD_SHA256_SPEC,
        "input_base_bounds": bounds,
        "minimum_output_base": str(quote["route"]["minimum_output_base"]),
        "required_wallet_chains": chains,
        "event_signer_public_required": signer,
        "step_count": summary["step_count"],
        "recommended_mode": "session" if summary["step_count"] > 1 else "one_shot_or_session",
        "allowed_modes": modes,
        "session_header": {
            "name": "X-AssetFare-Session-Token",
            "required_for": "session",
            "caller_generated": True,
            "minimum_entropy_bits": 256,
            "server_returns_raw_value": False,
        },
        "idempotency": {
            "required": True,
            "field": "idempotency_key",
            "pattern": "^[A-Za-z0-9._:-]{8,128}$",
            "scope": "quote_and_selected_mode",
        },
        "approval_v3_required_fields": [
            "direct_route_summary_sha256", "idempotency_key", "maximum_input_base",
            "minimum_output_base", "quote_fingerprint", "quote_id", "selected_mode",
            "selection_status", "version",
        ],
        "legacy_handoff_enforcement": "legacy_advisory",
        "server_signing": False,
        "server_submission": False,
    }
    return quote


def with_cost_summary(quote):
    amount=float(quote["intent"]["amount_usd"]);expected=float(quote["offer"]["expected_receive_usd"]);minimum=float(quote["offer"]["estimated_min_receive_usd"])
    ec=max(0.0,amount-expected);mc=max(0.0,amount-minimum)
    quote["cost_summary"]={"scope":"token_path_only_network_gas_excluded","input_value_usd":amount,"expected_receive_value_usd":expected,"minimum_receive_value_usd":minimum,"expected_total_cost_usd":ec,"maximum_total_cost_usd":mc,"expected_total_cost_percent":ec/amount*100,"maximum_total_cost_percent":mc/amount*100,"assetfare_service_fee":{"bps":1,"estimated_usd":amount/10_000,"included_in_receive_amount":True,"note":"service fee only"},"provider_fee_components":[],"unpriced_costs":["source_chain_network_fee"],"rankable_all_in":False,"small_amount_warning":mc/amount>=.01,"warning":None}
    quote["eta"]={"estimated_time_seconds":quote["offer"]["estimated_time_seconds"],"estimated_time_range_seconds":[8,23],"complete_route_estimate":True,"sources":[],"note":"estimate"}
    return add_continuation(quote)


class _Resp:
    def __init__(self, status_code=200, body=None, content_type="application/json", raw=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.is_redirect = 300 <= status_code < 400
        self._raw = raw if raw is not None else json.dumps({} if body is None else body).encode()

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._raw), chunk_size):
            yield self._raw[i : i + chunk_size]

    def close(self):
        pass


class _Session:
    """Static per-path mock: routes maps path -> dict body (or a _Resp)."""

    def __init__(self, routes):
        self.routes = routes
        self.trust_env = True
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        path = url.replace("https://api.assetfare.dev", "")
        entry = self.routes.get(path)
        if entry is None:
            raise AssertionError(f"no mock for {path}")
        if isinstance(entry, dict):
            return _Resp(body=entry)
        return entry


def client(routes):
    return af(session=_Session(routes))


# ---- stateful upstream mock (prepare + full session lifecycle, no network) ----
class _Upstream:
    def __init__(self):
        self.sessions = {}
        self.idem = {}
        self.calls = []
        self._n = 0

    def _uuid(self):
        self._n += 1
        return f"00000000-0000-4000-8000-{self._n:012d}"

    def _bundle(self):
        return {
            "status": "pass",
            "version": "assetfare-direct-multichain-action-v2",
            "step_index": 0,
            "expires_in_seconds": 60,
            "unsigned_action": {"transaction": "0xUNSIGNED", "chainId": 1},
            "minimum_output_base": 1,
            "server_signing": False,
            "server_submission": False,
            "signed": False,
            "submitted": False,
        }

    def _public(self, rec, replay=False):
        expired = rec["expired"]
        status = "action_expired" if expired else rec["status"]
        ready = (rec["status"] == "action_ready") and not expired
        return {
            "session_id": rec["id"],
            "status": status,
            "route": rec["route"],
            "current_step": 0,
            "action_available": ready,
            "current_action": (self._bundle() if ready else None),
            "next_operation": (
                "refresh_action"
                if expired
                else "submit_current_action"
                if rec["status"] == "action_ready"
                else "observe_output"
                if rec["status"] == "awaiting_output_receipt"
                else "none"
            ),
            "idempotent_replay": replay,
            "server_signing": False,
            "server_submission": False,
            "signed": False,
            "submitted": False,
        }

    def expire(self, session_id):
        self.sessions[session_id]["expired"] = True

    def handle(self, method, path, payload, headers):
        self.calls.append((method, path))
        payload = payload or {}
        token = headers.get("X-AssetFare-Session-Token") or headers.get("x-assetfare-session-token")

        if path == "/v2/prepare" and method == "POST":
            if payload.get("caller_approved") is not True:
                return _Resp(400, {"error": "caller_approval_required"})
            return _Resp(200, self._bundle())

        if path == "/v2/session" and method == "POST":
            if payload.get("caller_approved") is not True:
                return _Resp(400, {"error": "caller_approval_required"})
            if not token:
                return _Resp(401, {"error": "session_token_required"})
            key = (token, payload.get("idempotency_key"))
            if key in self.idem:
                rec = self.sessions[self.idem[key]]
                return _Resp(200, self._public(rec, replay=True))
            sid = self._uuid()
            rec = {
                "id": sid,
                "owner": token,
                "route": f"{payload['from_chain']}:{payload['from_token']}->{payload['to_chain']}:{payload['to_token']}",
                "status": "action_ready",
                "expired": False,
            }
            self.sessions[sid] = rec
            self.idem[key] = sid
            return _Resp(200, self._public(rec))

        if path.startswith("/v2/session/"):
            rest = path[len("/v2/session/") :]
            parts = rest.split("/")
            sid = parts[0]
            rec = self.sessions.get(sid)
            if rec is None:
                return _Resp(404, {"error": "session_not_found"})
            if token != rec["owner"]:
                return _Resp(403, {"error": "session_token_mismatch"})
            if len(parts) == 1 and method == "GET":
                return _Resp(200, self._public(rec))
            op = parts[1] if len(parts) > 1 else ""
            if op == "observe-source" and method == "POST":
                if rec["expired"]:
                    return _Resp(409, {"error": "action_expired"})
                rec["status"] = "awaiting_output_receipt"
                return _Resp(200, self._public(rec))
            if op == "observe-output" and method == "POST":
                if rec["expired"]:
                    return _Resp(409, {"error": "action_expired"})
                rec["status"] = "complete"
                return _Resp(200, self._public(rec))
            if op == "refresh-action" and method == "POST":
                rec["expired"] = False
                rec["status"] = "action_ready"
                return _Resp(200, self._public(rec))
        return _Resp(404, {"error": "not_found"})


class _StatefulSession:
    def __init__(self, upstream):
        self.upstream = upstream
        self.trust_env = True

    def request(self, method, url, **kwargs):
        path = url.replace("https://api.assetfare.dev", "")
        payload = kwargs.get("json")
        headers = kwargs.get("headers") or {}
        return self.upstream.handle(method, path, payload, headers)


def stateful():
    up = _Upstream()
    return af(session=_StatefulSession(up)), up


def wallets_for(*chains):
    return {c: (SOL if c == "solana" else EVM) for c in chains}


# ---- origin ----
@pytest.mark.parametrize(
    "bad",
    [
        "http://api.assetfare.dev",
        "https://api.assetfare.dev:8443",
        "https://u@api.assetfare.dev",
        "https://api.assetfare.dev/v2",
        "https://evil.example.com",
    ],
)
def test_origin_rejected(bad):
    with pytest.raises(AssetFareError):
        AssetFareClient(base_url=bad)


def test_trust_env_disabled():
    s = _Session({})
    af(session=s)
    assert s.trust_env is False


# ---- capabilities (19-chain surface: 24 endpoints, exactly 100 available) ----
def test_capabilities_ok():
    caps = client({"/v2/capabilities": valid_caps(), "/v2/status": valid_status()}).get_capabilities()
    assert caps["directed_conversion_routes"] == 100
    assert caps["unsigned_route_plans_ready"] == 100
    assert caps["execution_ready_routes"] == 100
    assert caps["currently_prepare_ready_routes"] == 100
    assert caps["phase_b_blocked_routes"] == 0
    assert len(caps["asset_endpoints"]) == 24
    assert caps["server_signs_or_submits"] is False
    assert sorted(caps["source_only_asset_endpoints"]) == sorted(f"{chain}:{token}" for chain,token in _SOURCE_ONLY_ENDPOINTS)
    assert set(caps["source_only_routes"]) == _SOURCE_ONLY_ROUTES
    assert caps["blocked_source_only_routes"] == []
    assert caps["amount_usd"] == {
        "minimum": 1.0,
        "maximum": None,
        "policy": "no_business_maximum",
    }
    assert caps["evaluation_guidance"] == PUBLIC_EVALUATION_GUIDANCE
    assert caps["economic_guidance"] == ECONOMIC_GUIDANCE
    assert caps["economic_guidance_url"] == "https://assetfare.dev/route-economics.json"


@pytest.mark.parametrize(
    "mut",
    [
        lambda c: c.update(directed_conversion_routes=41),
        lambda c: c.update(execution_ready_routes=80),
        lambda c: c.update(phase_b_blocked_routes=4),
        lambda c: c.update(blocked_source_only_routes=["xlayer:USDC->base:USDC"]),
        lambda c: c.update(server_signing=True),
        lambda c: c.pop("blocked_source_only_routes"),
    ],
)
def test_capabilities_boundary_fail(mut):
    caps = valid_caps()
    mut(caps)
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


def test_capabilities_partial_live_availability_fails_closed():
    caps=valid_caps();caps.pop("execution_availability")
    with pytest.raises(AssetFareError):client({"/v2/capabilities":caps,"/v2/status":valid_status()}).get_capabilities()


@pytest.mark.parametrize("mut",[
        lambda c:c.update(currently_prepare_ready_routes=41,temporarily_unavailable_routes=["evil:USDC->base:USDC"],temporarily_unavailable_route_count=1,execution_availability={"status":"degraded","provider":"circle_iris","guarantees_future_availability":False}),
    lambda c:c.update(execution_availability={"status":"degraded","provider":"circle_iris","guarantees_future_availability":False}),
])
def test_capabilities_live_availability_semantics_fail_closed(mut):
    caps=valid_caps();mut(caps)
    with pytest.raises(AssetFareError):client({"/v2/capabilities":caps,"/v2/status":valid_status()}).get_capabilities()


@pytest.mark.parametrize(
    "mut",
    [
        lambda c: c.pop("evaluation_guidance"),
        lambda c: c["evaluation_guidance"].update(route_minimum_usd=True),
        lambda c: c["evaluation_guidance"].update(documentation_example_usd=999),
        lambda c: c["evaluation_guidance"].update(extra=True),
    ],
)
def test_capabilities_evaluation_guidance_must_match_core_exactly(mut):
    caps = valid_caps()
    mut(caps)
    with pytest.raises(AssetFareError, match="assetfare_evaluation_guidance_invalid"):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


@pytest.mark.parametrize(
    "mut",
    [
        lambda c: c.pop("economic_guidance"),
        lambda c: c["route_product_policy"].pop("economic_guidance"),
        lambda c: c["route_product_policy"]["economic_guidance"].update(as_of="2099-01-01"),
        lambda c: c["route_product_policy"].update(amount_conditioned_routes={"ethereum:USDC->base:USDC": 500}),
        lambda c: c["economic_guidance"]["recommendation_status_counts"].update(active_price_verified=41),
        lambda c: c["evaluation_guidance"].update(native_usdc_economic_evaluation_start_usd=50),
    ],
)
def test_capabilities_route_specific_economic_guidance_fail_closed(mut):
    caps = valid_caps(); mut(caps)
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


# ---- quote happy path + fee surfaced ----
def test_quote_exact_body_and_bounded_return():
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 250))
    c = client({"/v2/quote": q})
    out = c.get_quote("solana", "USDG", "arbitrum", "ETH", 250)
    assert out["from"] == "solana:USDG" and out["to"] == "arbitrum:ETH"
    assert out["assetfare_fee_bps"] == 1
    assert out["fee_modeled_bps"] == 1
    assert out["fee_collectible_now"] is True
    assert out["cost_summary"]["maximum_total_cost_usd"] == pytest.approx(.02)
    assert out["cost_summary"]["assetfare_service_fee"]["bps"] == 1
    assert out["eta"]["estimated_time_seconds"] == 23
    assert out["fee_collection"] == "only_on_eligible_successful_executor_step"
    assert out["fee_collection_steps"] == [0]
    assert out["execution_supported"] is True
    assert out["source_only"] is False
    assert out["evaluation_guidance"] == PUBLIC_EVALUATION_GUIDANCE
    expected_guidance=copy.deepcopy(ROUTE_ECONOMIC_GUIDANCE)
    expected_guidance.update(quoted_amount_usd=250,price_recommended_for_amount=False)
    assert out["economic_guidance"] == expected_guidance
    assert out["economic_guidance_url"] == "https://assetfare.dev/route-economics.json"
    assert out["agent_guidance"] == {
        "best_from_usd": 1000,
        "best_from_verified": True,
        "availability_only": False,
        "recommendation_status": "active_price_verified",
        "recommended_action": "price_recommended_at_or_above_best_from_otherwise_no_price_recommendation",
        "use_assetfare_first_at_or_above_best_from": False,
        "best_from_not_100_percent_guarantee": True,
        "confirm_fresh_assetfare_quote": True,
        "compare_with_other_routes": True,
        "selection_status": "unranked_candidate",
        "automatic_selection_forbidden": True,
    }
    assert out["server_signs_or_submits"] is False
    summary = out["direct_route_summary"]
    assert summary["version"] == "assetfare-direct-route-summary-v1"
    assert summary["classification"] == "direct_protocol_only"
    assert summary["route_aggregator_used"] is False
    assert summary["fee_collection_step_index"] == 0
    assert [step["provider"] for step in summary["steps"]] == [
        "orca_whirlpool", "circle_cctp", "uniswap_v3"
    ]
    descriptor = out["continuation_descriptor"]
    assert descriptor == {
        "version": "assetfare-quote-bound-continuation-v3",
        "quote_id": q["quote_id"],
        "quote_fingerprint": q["continuation_v3"]["quote_fingerprint"],
        "expires_at": "2026-09-17T00:00:30.000Z",
        "ttl_seconds": 30,
        "selection_status": "unranked_candidate",
        "required_wallet_chains": ["arbitrum", "solana"],
        "event_signer_public_required": True,
        "allowed_modes": ["session"],
        "recommended_mode": "session",
        "openapi_url": "https://api.assetfare.dev/v2/openapi",
        "legacy_handoff_enforcement": "legacy_advisory",
        "automatic_selection_forbidden": True,
        "caller_approved_boolean_is_not_human_proof": True,
        "wallet_collection_performed": False,
        "approval_v3_generated": False,
        "prepare_calls": 0,
        "session_calls": 0,
        "server_signing": False,
        "server_submission": False,
    }
    assert "quote_fingerprint_claim" not in descriptor
    assert "input_base_bounds" not in descriptor


def test_quote_marks_assetfare_first_at_route_best_from_without_selecting_execution():
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 1000))
    out = client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 1000)
    assert out["agent_guidance"] == {
        "best_from_usd": 1000,
        "best_from_verified": True,
        "availability_only": False,
        "recommendation_status": "active_price_verified",
        "recommended_action": "price_recommended_at_or_above_best_from_otherwise_no_price_recommendation",
        "use_assetfare_first_at_or_above_best_from": True,
        "best_from_not_100_percent_guarantee": True,
        "confirm_fresh_assetfare_quote": True,
        "compare_with_other_routes": False,
        "selection_status": "unranked_candidate",
        "automatic_selection_forbidden": True,
    }
    assert out["continuation_descriptor"]["selection_status"] == "unranked_candidate"
    assert out["server_signs_or_submits"] is False


def test_availability_only_sei_quote_has_no_cheapest_price_claim():
    q=with_cost_summary(valid_quote("sei","USDC","base","USDC",100,source_only=True))
    out=client({"/v2/quote":q}).get_quote("sei","USDC","base","USDC",100)
    assert out["agent_guidance"]["best_from_usd"] is None
    assert out["agent_guidance"]["best_from_verified"] is False
    assert out["agent_guidance"]["availability_only"] is True
    assert out["agent_guidance"]["recommendation_status"]=="active_availability_only"
    assert out["agent_guidance"]["use_assetfare_first_at_or_above_best_from"] is False


def test_compare_required_route_remains_executable_without_price_recommendation():
    q=with_cost_summary(valid_quote("base","ETH","arbitrum","ETH",100))
    out=client({"/v2/quote":q}).get_quote("base","ETH","arbitrum","ETH",100)
    assert out["agent_guidance"]["recommendation_status"]=="active_compare_required"
    assert out["agent_guidance"]["best_from_usd"] is None
    assert out["agent_guidance"]["compare_with_other_routes"] is True
    assert out["execution_supported"] is True


@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q.pop("economic_guidance"),
        lambda q: q["economic_guidance"].update(native_usdc_economic_evaluation_start_usd=50),
        lambda q: q["economic_guidance"].update(advisory_start_usd=75),
        lambda q: q["economic_guidance"].update(not_an_execution_minimum=False),
    ],
)
def test_quote_route_specific_economic_guidance_fail_closed(mut):
    q = with_cost_summary(valid_quote("solana", "USDC", "base", "USDC", 1000)); mut(q)
    with pytest.raises(AssetFareError, match="assetfare_route_economic_guidance_invalid"):
        client({"/v2/quote": q}).get_quote("solana", "USDC", "base", "USDC", 1000)


def test_quote_guidance_amount_must_match_requested_amount():
    q=with_cost_summary(valid_quote("solana","USDC","base","USDC",1000))
    q["economic_guidance"]["quoted_amount_usd"]=999
    with pytest.raises(AssetFareError,match="assetfare_route_economic_guidance_invalid"):
        client({"/v2/quote":q}).get_quote("solana","USDC","base","USDC",1000)


def test_robinhood_ingress_exposes_direct_paxos_path():
    q = with_cost_summary(valid_quote("base", "USDC", "robinhood", "USDG", 250))
    out = client({"/v2/quote": q}).get_quote("base", "USDC", "robinhood", "USDG", 250)
    summary = out["direct_route_summary"]
    assert summary["classification"] == "direct_protocol_only"
    assert summary["product_classification"] == "primary_direct"
    assert summary["route_minimum_guard_bps"] == 50
    assert summary["route_aggregator_used"] is False
    assert summary["external_intent_protocol_used"] is False
    assert [step["provider"] for step in summary["steps"]] == ["circle_cctp", "orca_whirlpool", "paxos_usdg_layerzero_oft"]
    assert sum(step["minimum_guard_bps"] for step in summary["steps"]) == 50


@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q.pop("direct_route_summary"),
        lambda q: q["direct_route_summary"].update(extra="forbidden"),
        lambda q: q["direct_route_summary"]["steps"][0].update(expected_input_base=1),
        lambda q: q["direct_route_summary"]["steps"][0].update(expected_input_base="01"),
        lambda q: q["direct_route_summary"]["steps"][1].update(expected_input_base="3"),
        lambda q: q["direct_route_summary"]["steps"][1].update(minimum_input_base="2"),
        lambda q: q["direct_route_summary"]["steps"][0].update(private_key="forbidden"),
        lambda q: q["direct_route_summary"].update(fee_collection_step_index=1),
        lambda q: q["direct_route_summary"]["steps"][0].update(assetfare_fee_bps=0),
        lambda q: q["direct_route_summary"].update(route_aggregator_used=True),
        lambda q: q["direct_route_summary"].update(server_signing=True),
        lambda q: q["route"].update(mode="same_chain_direct"),
        lambda q: q["risk"].update(provider_internal_dex_aggregation_possible=True),
    ],
)
def test_direct_route_summary_hostiles_fail_closed(mut):
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 250))
    mut(q)
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 250)


@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q.pop("continuation_v3"),
        lambda q: q["continuation_v3"].update(extra="forbidden"),
        lambda q: q["continuation_v3"].update(selection_status="selected"),
        lambda q: q["continuation_v3"].update(automatic_selection_forbidden=False),
        lambda q: q["continuation_v3"].update(caller_approved_boolean_is_not_human_proof=False),
        lambda q: q["continuation_v3"].update(quote_fingerprint="0" * 64),
        lambda q: q["continuation_v3"].update(quote_payload_sha256_spec="forbidden"),
        lambda q: q["continuation_v3"].update(required_wallet_chains=["solana"]),
        lambda q: q["continuation_v3"].update(event_signer_public_required=False),
        lambda q: q["continuation_v3"].update(allowed_modes=["one_shot", "session"]),
        lambda q: q["continuation_v3"].update(recommended_mode="one_shot_or_session"),
        lambda q: q["continuation_v3"].update(legacy_handoff_enforcement="server_enforced"),
        lambda q: q["continuation_v3"]["input_base_bounds"].update(maximum="2"),
        lambda q: q["continuation_v3"]["session_header"].update(server_returns_raw_value=True),
        lambda q: q["continuation_v3"]["quote_fingerprint_claim"].update(step_count="2"),
        lambda q: q["continuation_v3"]["quote_fingerprint_claim"].update(quote_payload_sha256_spec="forbidden"),
        lambda q: q["continuation_v3"]["quote_fingerprint_claim"]["intent"].update(amount_usd_decimal="251"),
        lambda q: q["continuation_v3"].update(private_key="forbidden"),
    ],
)
def test_continuation_v3_hostiles_fail_closed_without_wallet_or_action_calls(mut):
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 250))
    mut(q)
    mock = _Session({"/v2/quote": q})
    with pytest.raises(AssetFareError, match="assetfare_(continuation_v3_invalid|safety_boundary_failed)"):
        af(session=mock).get_quote("solana", "USDG", "arbitrum", "ETH", 250)
    assert [call["url"] for call in mock.calls] == ["https://api.assetfare.dev/v2/quote"]


def test_continuation_payload_hash_accepts_integral_float_and_base_units_above_js_safe_integer():
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 1000.0))
    exact_output = 9_007_199_254_740_993
    q["direct_route_summary"]["steps"][-1]["expected_output_base"] = str(exact_output)
    q["route"]["steps"][-1]["expected_output_base"] = exact_output
    q["route"]["expected_output_base"] = exact_output
    add_continuation(q)
    out = client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 1000.0)
    assert out["continuation_descriptor"]["quote_fingerprint"] == q["continuation_v3"]["quote_fingerprint"]


def test_typed_payload_hash_preserves_number_string_and_negative_zero_and_rejects_unsafe_evidence():
    base = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 1000.0))
    numeric = copy.deepcopy(base)
    string = copy.deepcopy(base)
    negative_zero = copy.deepcopy(base)
    positive_zero = copy.deepcopy(base)
    numeric["route"]["steps"][0]["expected_evidence"] = {"semantic": 1}
    string["route"]["steps"][0]["expected_evidence"] = {"semantic": "1"}
    negative_zero["route"]["steps"][0]["expected_evidence"] = {"semantic": -0.0}
    positive_zero["route"]["steps"][0]["expected_evidence"] = {"semantic": 0.0}
    assert AssetFareClient._quote_payload_sha256(numeric, numeric["direct_route_summary"]) != (
        AssetFareClient._quote_payload_sha256(string, string["direct_route_summary"])
    )
    assert AssetFareClient._quote_payload_sha256(negative_zero, negative_zero["direct_route_summary"]) != (
        AssetFareClient._quote_payload_sha256(positive_zero, positive_zero["direct_route_summary"])
    )
    unsafe = copy.deepcopy(base)
    unsafe["route"]["steps"][0]["expected_evidence"] = {"semantic": 500_000_000_000_000_001}
    with pytest.raises(AssetFareError, match="assetfare_continuation_v3_invalid"):
        AssetFareClient._quote_payload_sha256(unsafe, unsafe["direct_route_summary"])
    unsafe_float = copy.deepcopy(base)
    unsafe_float["route"]["steps"][0]["expected_evidence"] = {"semantic": 500_000_000_000_000_000.0}
    with pytest.raises(AssetFareError, match="assetfare_continuation_v3_invalid"):
        AssetFareClient._quote_payload_sha256(unsafe_float, unsafe_float["direct_route_summary"])
    invalid_unicode = copy.deepcopy(base)
    invalid_unicode["route"]["steps"][0]["expected_evidence"] = {"semantic": "\ud800"}
    with pytest.raises(AssetFareError, match="assetfare_continuation_v3_invalid"):
        AssetFareClient._quote_payload_sha256(invalid_unicode, invalid_unicode["direct_route_summary"])


def test_robinhood_ingress_cannot_be_relabelled_external():
    q = with_cost_summary(valid_quote("base", "USDC", "robinhood", "USDG", 250))
    q["route"]["steps"][2].update(provider="across_intent_bridge", external_intent_protocol=True)
    q["route"]["external_intent_protocol_used"] = True
    q["risk"]["external_intent_protocol_used"] = True
    q["risk"]["provider_internal_dex_aggregation_possible"] = True
    summary = q["direct_route_summary"]
    summary.update(
        classification="external_intent",
        external_intent_protocol_used=True,
        provider_internal_dex_aggregation_possible=True,
    )
    summary["steps"][2].update(
        provider="across_intent_bridge",
        direct_protocol=False,
        external_intent_protocol=True,
    )
    with pytest.raises(AssetFareError, match="assetfare_direct_route_summary_invalid"):
        client({"/v2/quote": q}).get_quote("base", "USDC", "robinhood", "USDG", 250)


@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q["route"]["steps"][0].update(private_key="forbidden"),
        lambda q: q["route"]["steps"][0].update(signed=True),
        lambda q: q["route"]["steps"][0].update(submitted=True),
    ],
)
def test_raw_route_secret_or_signed_claim_fails_closed(mut):
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 250))
    mut(q)
    with pytest.raises(AssetFareError, match="assetfare_safety_boundary_failed"):
        client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 250)


def test_raw_provider_evidence_is_not_projected_into_normalized_summary():
    q = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", 250))
    q["route"]["steps"][0]["expected_evidence"] = {"provider_raw": "opaque", "signed": False}
    add_continuation(q)
    out = client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 250)
    encoded = json.dumps(out["direct_route_summary"], sort_keys=True)
    assert "expected_evidence" not in encoded
    assert "provider_raw" not in encoded


@pytest.mark.parametrize("mut",[
    lambda q:q["cost_summary"].__setitem__("maximum_total_cost_usd",.5),
    lambda q:q["cost_summary"]["assetfare_service_fee"].__setitem__("estimated_usd",1),
    lambda q:q["cost_summary"].__setitem__("rankable_all_in",True),
    lambda q:q["eta"].__setitem__("estimated_time_seconds",99),
    lambda q:q["cost_summary"].__setitem__("provider_fee_components",[{"expected_usd":1,"maximum_usd":1}]),
    lambda q:q["cost_summary"].__setitem__("provider_fee_components",[{"expected_usd":.02,"maximum_usd":.01}]),
    lambda q:q["cost_summary"].__setitem__("unpriced_costs",[]),
    lambda q:(q["cost_summary"].__setitem__("small_amount_warning",True),q["cost_summary"].__setitem__("warning","wrong")),
    lambda q:q["eta"].__setitem__("estimated_time_range_seconds",[30,23]),
    lambda q:q["eta"].__setitem__("complete_route_estimate",False),
    lambda q:q.__setitem__("ttl_seconds",61),
])
def test_quote_cost_and_eta_binding_hostiles(mut):
    q=with_cost_summary(valid_quote("solana","USDG","arbitrum","ETH",250));mut(q)
    with pytest.raises(AssetFareError):client({"/v2/quote":q}).get_quote("solana","USDG","arbitrum","ETH",250)


def test_rollback_core_without_cost_derives_honest_total():
    q=valid_quote("solana","USDG","arbitrum","ETH",250)
    out=client({"/v2/quote":q}).get_quote("solana","USDG","arbitrum","ETH",250)
    assert out["cost_summary"]["scope"]=="token_path_only_network_gas_excluded"
    assert "provider_fee_breakdown_unavailable_legacy_core" in out["cost_summary"]["unpriced_costs"]


def test_rollback_core_large_amount_fee_is_exact_one_bp_without_maximum():
    amount = 100_000
    q = valid_quote("solana", "USDG", "arbitrum", "ETH", amount)
    out = client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", amount)
    assert out["cost_summary"]["assetfare_service_fee"]["estimated_usd"] == 10


def test_layerzero_native_fee_and_gas_are_bound_into_all_in_estimate():
    amount=50
    q=with_cost_summary(valid_quote("solana","USDG","arbitrum","ETH",amount))
    cost=q["cost_summary"];expected=cost["expected_total_cost_usd"];maximum=cost["maximum_total_cost_usd"]
    native={"provider":"layerzero","kind":"layerzero_native_fee","paid_in":"robinhood:ETH","expected_amount_base":"452721895243444","maximum_amount_base":"452721895243444","decimals":18,"expected_usd":1.18,"maximum_usd":1.18,"included_in_receive_amount":False,"basis":"live_quorum_oft_quote"}
    gas={"provider":"source_network","kind":"source_chain_network_fee_estimate","paid_in":"robinhood:ETH","expected_amount_base":"9000000000000","maximum_amount_base":"14000000000000","decimals":18,"expected_usd":.02,"maximum_usd":.04,"included_in_receive_amount":False,"basis":"live_quorum_gas_price_x_receipt_anchored_approval_and_bridge_units"}
    required={"paid_in":"robinhood:ETH","decimals":18,"expected_amount_base":"461721895243444","maximum_amount_base":"466721895243444","expected_amount":"0.000461721895243444","maximum_amount":"0.000466721895243444","expected_usd":1.2,"maximum_usd":1.22,"includes":["layerzero_native_fee","source_chain_network_fee_estimate"],"included_in_receive_amount":False,"basis":"live_layerzero_native_fee_plus_bounded_source_network_fee"}
    cost.update(token_path_expected_cost_usd=expected,token_path_maximum_cost_usd=maximum,separately_paid_costs=[native,gas],expected_all_in_cost_usd_estimate=expected+1.2,maximum_all_in_cost_usd_estimate=maximum+1.22,expected_all_in_cost_percent_estimate=(expected+1.2)/amount*100,maximum_all_in_cost_percent_estimate=(maximum+1.22)/amount*100,all_in_estimate_complete=False,native_balance_requirements=[required],source_native_balance_required=required,small_amount_warning=True,warning="known native cost")
    add_continuation(q)
    out=client({"/v2/quote":q}).get_quote("solana","USDG","arbitrum","ETH",amount)
    assert out["cost_summary"]["expected_all_in_cost_usd_estimate"]>=native["expected_usd"]
    assert out["cost_summary"]["source_native_balance_required"]["paid_in"]=="robinhood:ETH"
    broken=copy.deepcopy(q);broken["cost_summary"]["expected_all_in_cost_usd_estimate"]+=1;add_continuation(broken)
    with pytest.raises(AssetFareError,match="assetfare_cost_summary_invalid"):
        client({"/v2/quote":broken}).get_quote("solana","USDG","arbitrum","ETH",amount)


def test_quote_accepts_sub_micro_usd_rounding_alignment():
    q=with_cost_summary(valid_quote("solana","USDG","arbitrum","ETH",250));q["offer"]["expected_receive_usd"]=249.1234567;q["offer"]["estimated_min_receive_usd"]=248.123456;q["cost_summary"].update(expected_receive_value_usd=249.123457,minimum_receive_value_usd=248.123456,expected_total_cost_usd=.876543,maximum_total_cost_usd=1.876544,expected_total_cost_percent=.3506172,maximum_total_cost_percent=.7506176,small_amount_warning=False,warning=None)
    add_continuation(q)
    out=client({"/v2/quote":q}).get_quote("solana","USDG","arbitrum","ETH",250)
    assert out["cost_summary"]["expected_receive_value_usd"]==249.123457










# ---- FAIL-CLOSED handoff (no local fallback) ----




# ---- source-only directional quotes use the same executable handoff ----
def test_source_only_quote_execution_ready():
    q = valid_quote("xlayer", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    out = client({"/v2/quote": q}).get_quote("xlayer", "USDC", "base", "USDC", 100)
    assert out["source_only"] is True
    assert out["execution_supported"] is True
    assert out["execution_blocker"] is None
    assert out["fee_collectible_now"] is True
    assert "caller_action_plan_handoff" not in out
    assert "caller_action_plan_handoff_v2" not in out
    assert "handoff_schema_version" not in out


@pytest.mark.parametrize(
    ("source", "destination"),
    [("ethereum", "solana"), ("hyperevm", "solana"), ("xlayer", "base"), ("xlayer", "solana"), ("sei", "base"), ("sei", "solana"), ("sonic", "base"), ("sonic", "solana"), ("unichain", "solana"), ("ink", "solana")],
)
def test_expansion_source_only_quote_execution_ready(source, destination):
    q = valid_quote(source, "USDC", destination, "USDC", 500, source_only=True, fee=1)
    out = client({"/v2/quote": q}).get_quote(source, "USDC", destination, "USDC", 500)
    assert out["source_only"] is True
    assert out["direct_route_summary"]["mode"] == "cctp_direct_composition"
    assert out["economic_guidance"]["not_an_execution_minimum"] is True


def test_quote_only_client_has_no_action_methods():
    forbidden = {
        "prepare",
        "session_create",
        "session_get",
        "observe_source",
        "observe_output",
        "refresh_action",
    }
    assert forbidden.isdisjoint(dir(AssetFareClient))


def test_optimism_source_only_one_fee_quote_ok():
    q = valid_quote("optimism", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    out = client({"/v2/quote": q}).get_quote("optimism", "USDC", "base", "USDC", 100)
    assert out["assetfare_fee_bps"] == 1
    assert out["fee_collection_steps"] == [0]
    assert out["fee_collectible_now"] is True






# ---- fee EXACTLY 1bp ----
@pytest.mark.parametrize(
    "mut",
    [
        lambda o: o.update(assetfare_fee_bps=8, fee_modeled_bps=8),  # 8bp out of range
        lambda o: o.update(assetfare_fee_bps=2, fee_modeled_bps=2),  # >1
        lambda o: o.update(assetfare_fee_bps=0, fee_modeled_bps=0, fee_collectible_now=False, fee_collection_steps=[]),
        lambda o: o.update(assetfare_fee_bps=1, fee_collection_steps=[]),  # fee1 needs one step
        lambda o: o.update(assetfare_fee_bps=0, fee_collection_steps=[0]),  # fee0 needs none
        lambda o: o.update(assetfare_fee_bps=1, fee_collection_steps=[0, 0]),  # duplicate/2-step
        lambda o: o.update(assetfare_fee_bps=1, fee_collection_steps=[5]),  # out of range
        lambda o: o.update(fee_collection="something_else"),  # wrong literal
        lambda o: o.pop("fee_modeled_bps"),  # missing modeled
        lambda o: o.update(fee_collectible_now="yes"),  # non-bool
    ],
)
def test_quote_fee_exact_zero_or_one(mut):
    q = valid_quote("solana", "USDG", "arbitrum", "ETH", 250)
    mut(q["offer"])
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 250)


def test_source_only_fee_collectible_now_false_rejected():
    q = valid_quote("xlayer", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    q["offer"]["fee_collectible_now"] = False
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("xlayer", "USDC", "base", "USDC", 100)


# ---- route enumeration ----
def test_all_100_routes_generated_and_executable():
    routes = all_routes()
    assert len(routes) == 100
    executable = [r for r in routes if r[0] not in SOURCE_ONLY_CHAINS]
    source_only = [r for r in routes if r[0] in SOURCE_ONLY_CHAINS]
    assert len(executable) == 72
    assert len(source_only) == 28


@pytest.mark.parametrize("dest", sorted(SOURCE_ONLY_CHAINS))
def test_source_only_destination_rejected(dest):
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "USDC", dest, "USDC", 100)


# ---- amount / endpoint hostiles ----
@pytest.mark.parametrize("amt", [0.99, "5", True, float("nan"), float("inf"), None])
def test_amount_rejected(amt):
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "USDG", "arbitrum", "ETH", amt)


@pytest.mark.parametrize("amt", [1000.01, 5000])
def test_amount_above_former_business_maximum_accepted(amt):
    quote = with_cost_summary(valid_quote("solana", "USDG", "arbitrum", "ETH", amt))
    result = client({"/v2/quote": quote}).get_quote("solana", "USDG", "arbitrum", "ETH", amt)
    assert result["amount_usd"] == amt


@pytest.mark.parametrize(
    "amount_policy",
    [
        None,
        {"minimum": 0, "maximum": None, "policy": "no_business_maximum"},
        {"minimum": 1, "maximum": 1000, "policy": "no_business_maximum"},
        {"minimum": 1, "maximum": None, "policy": "capped"},
    ],
)
def test_capabilities_rejects_invalid_amount_policy(amount_policy):
    caps = valid_caps()
    caps["amount_usd"] = amount_policy
    with pytest.raises(AssetFareError, match="assetfare_amount_policy_invalid"):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


def test_unsupported_endpoint_rejected():
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "DOGE", "arbitrum", "ETH", 100)


# ---- transport / safety ----
def test_non_json_rejected():
    c = af(session=_Session({"/v2/quote": _Resp(body={"x": 1}, content_type="text/html")}))
    with pytest.raises(AssetFareError):
        c.get_quote("solana", "USDG", "arbitrum", "ETH", 100)


def test_non_2xx_rejected():
    c = af(session=_Session({"/v2/quote": _Resp(status_code=400, body={"error": "x"})}))
    with pytest.raises(AssetFareError):
        c.get_quote("solana", "USDG", "arbitrum", "ETH", 100)


def test_quote_signing_claim_rejected():
    q = valid_quote("solana", "USDG", "arbitrum", "ETH", 250)
    q["route"]["server_signing"] = True
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDG", "arbitrum", "ETH", 250)


# ---- new_session_capability (local-only token, no network) ----
