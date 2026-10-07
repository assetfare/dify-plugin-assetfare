"""AssetFare non-custodial v2 client (pure; no Dify runtime dependency).

Strictly read-only quote and capabilities discovery. This Marketplace client
contains no wallet, authentication, prepare, session, signing, submission,
funding, swap, or bridge-execution method.

This client never accepts a wallet or credential and never authenticates,
prepares, creates a session, constructs an action, signs, submits, funds, swaps,
or bridges. Every response is validated against the live AssetFare v2 contract
and fails closed on anything that claims the server will sign or submit.

Kept free of the Dify SDK so it can be unit-tested offline.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import math
import re
import struct
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import requests

_ALLOWED_ORIGIN = "https://api.assetfare.dev"
# `_SOCKET_TIMEOUT_S` is the requests connect/read *inactivity* cap: a single
# connect or read cannot block longer than this. On top of it we track a
# monotonic *stale budget* per call and reject once the elapsed time has already
# overrun it -- checked before the request and after each received chunk (i.e. at
# chunk boundaries, not mid-read). This is NOT a hard absolute cancel: between
# stale checks a read can still extend up to the socket-inactivity window past
# the budget, and no thread/gevent forced cancellation is used (intentionally).
# The real hard wall is the Dify serverless runtime's outer execution limit.
_SOCKET_TIMEOUT_S = 45.0
_STALE_BUDGET_S = 45.0
_MAX_BYTES = 1_048_576
_MAX_TTL_SECONDS = 60
_MAX_FUTURE_SKEW_S = 300  # as_of may not be more than 5 minutes in the future

_CHAINS = ("aptos", "arbitrum", "avalanche", "base", "cronos", "ethereum", "hyperevm", "injective", "linea", "monad", "optimism", "robinhood", "sei", "solana", "sonic", "xlayer")
# Source chains that MAY be used in a quote. Destinations exclude
# the source-only chains.
_DESTINATION_CHAINS = frozenset({"solana", "base", "arbitrum", "robinhood"})
# Source-only chains: native USDC may leave them (to Base/Arbitrum USDC) but they
# are never a destination. Their four directional corridors are execution-ready
# through the same live quote surface as every other route.
_SOURCE_ONLY_CHAINS = frozenset({"aptos", "avalanche", "cronos", "ethereum", "hyperevm", "injective", "linea", "monad", "optimism", "sei", "sonic", "xlayer"})
_ENDPOINTS = frozenset(
    {
        ("solana", "SOL"),
        ("solana", "USDC"),
        ("solana", "USDG"),
        ("base", "USDC"),
        ("arbitrum", "ETH"),
        ("arbitrum", "USDC"),
        ("robinhood", "ETH"),
        ("robinhood", "USDG"),
        ("optimism", "USDC"),
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
    }
)
_SOURCE_ONLY_ENDPOINTS = frozenset({("aptos", "USDC"), ("avalanche", "USDC"), ("cronos", "USDC"), ("ethereum", "USDC"), ("hyperevm", "USDC"), ("injective", "USDC"), ("linea", "USDC"), ("monad", "USDC"), ("optimism", "USDC"), ("sei", "USDC"), ("sonic", "USDC"), ("xlayer", "USDC")})
_SOURCE_ONLY_ROUTES = frozenset(
    {
        "optimism:USDC->base:USDC",
        "ethereum:USDC->solana:USDC",
        "hyperevm:USDC->solana:USDC",
        "xlayer:USDC->base:USDC",
        "xlayer:USDC->solana:USDC",
        "sei:USDC->base:USDC",
        "sei:USDC->solana:USDC",
        "sonic:USDC->base:USDC",
        "sonic:USDC->solana:USDC",
        "monad:USDC->base:USDC",
        "monad:USDC->solana:USDC",
        "avalanche:USDC->base:USDC",
        "avalanche:USDC->solana:USDC",
        "cronos:USDC->base:USDC",
        "cronos:USDC->solana:USDC",
        "injective:USDC->base:USDC",
        "injective:USDC->solana:USDC",
        "linea:USDC->base:USDC",
        "linea:USDC->solana:USDC",
        "aptos:USDC->base:USDC",
        "aptos:USDC->solana:USDC",
    }
)
_PRICE_VERIFIED_ROUTES=frozenset({
    "arbitrum:ETH->arbitrum:USDC", "arbitrum:USDC->arbitrum:ETH",
    "arbitrum:USDC->robinhood:USDG", "arbitrum:USDC->solana:USDC",
    "arbitrum:USDC->solana:USDG", "base:USDC->robinhood:USDG",
    "base:USDC->solana:USDC", "base:USDC->solana:USDG",
    "ethereum:USDC->solana:USDC", "hyperevm:USDC->solana:USDC",
    "optimism:USDC->base:USDC", "robinhood:ETH->arbitrum:USDC",
    "robinhood:ETH->base:USDC", "robinhood:ETH->robinhood:USDG",
    "robinhood:ETH->solana:SOL", "robinhood:ETH->solana:USDC",
    "robinhood:ETH->solana:USDG", "robinhood:USDG->arbitrum:USDC",
    "robinhood:USDG->base:USDC", "robinhood:USDG->robinhood:ETH",
    "robinhood:USDG->solana:SOL", "robinhood:USDG->solana:USDC",
    "solana:SOL->base:USDC", "solana:SOL->robinhood:ETH",
    "solana:SOL->robinhood:USDG", "solana:SOL->solana:USDC",
    "solana:SOL->solana:USDG", "solana:USDC->arbitrum:USDC",
    "solana:USDC->base:USDC", "solana:USDC->robinhood:ETH",
    "solana:USDC->robinhood:USDG", "solana:USDG->arbitrum:ETH",
    "solana:USDG->arbitrum:USDC", "solana:USDG->base:USDC",
    "solana:USDG->robinhood:ETH", "solana:USDG->robinhood:USDG",
    "sonic:USDC->base:USDC", "sonic:USDC->solana:USDC",
    "xlayer:USDC->base:USDC", "xlayer:USDC->solana:USDC",
    "injective:USDC->base:USDC", "injective:USDC->solana:USDC",
    "linea:USDC->base:USDC", "linea:USDC->solana:USDC",
})
_AVAILABILITY_ONLY_ROUTES=frozenset({
    "aptos:USDC->base:USDC","aptos:USDC->solana:USDC",
    "avalanche:USDC->base:USDC","avalanche:USDC->solana:USDC",
    "cronos:USDC->base:USDC","cronos:USDC->solana:USDC",
    "monad:USDC->base:USDC","monad:USDC->solana:USDC",
    "sei:USDC->base:USDC","sei:USDC->solana:USDC",
})
_ROUTES=_PRICE_VERIFIED_ROUTES|_AVAILABILITY_ONLY_ROUTES
_EXPECTED_ROUTES = 54
_EXECUTION_READY_ROUTES = 54
_PHASE_B_BLOCKED_ROUTES = 0
_MIN_USD = 1.0

_EVALUATION_GUIDANCE = {
    "schema_version": 4,
    "route_minimum_usd": 1,
    "reachability_smoke_usd": 1,
    "reachability_smoke_scope": "connectivity_only_not_economic_evaluation",
    "route_specific_guidance": {
        "version": "assetfare-route-economic-guidance-v3",
        "url": "https://assetfare.dev/route-economics.json",
        "required_on_every_quote": True,
        "verified_best_from_only": True,
        "nullable_when_unverified": True,
        "controls_recommendation_only_when_verified": True,
        "values_change_with_market": True,
        "catalog_routes": 98,
        "public_active_routes": 54,
        "public_inactive_routes": 44,
        "availability_only_routes": 10,
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

_PUBLIC_EVALUATION_GUIDANCE = _EVALUATION_GUIDANCE

_ROUTE_GUIDANCE_KEYS = frozenset(
    {
        "advisory_start_usd", "best_from_usd", "best_from_verified", "availability_only",
        "public_activation_status", "public_active", "recommendation_status", "recommended_action",
        "confidence", "basis", "tested_amounts_usd", "tested_ceiling_usd", "not_an_execution_minimum",
        "not_a_best_price_guarantee", "fresh_quote_required",
    }
)
_GUIDANCE_STARTS = frozenset({50, 100, 250, 500, 1000, 2500, 5000, 10000})
_CAPABILITY_GUIDANCE_KEYS = frozenset(
    {
        "version", "as_of", "route_count", "public_active_route_count", "public_inactive_route_count",
        "verified_best_from_route_count", "availability_only_route_count", "currency", "technical_quote_minimum_usd",
        "economic_guidance_is_non_enforcing", "amount_is_never_rejected_by_economic_guidance",
        "values_change_with_market", "fresh_quote_and_caller_decision_control",
        "update_policy", "first_use_zero_allowance_scenario", "expected_output_ranking",
        "incomplete_cost_never_promoted", "tested_ceiling_usd", "advisory_start_distribution",
        "recommendation_status_counts",
    }
)

_FEE_COLLECTION_CONST = "only_on_eligible_successful_executor_step"
_DIRECT_SUMMARY_VERSION = "assetfare-direct-route-summary-v1"
_DIRECT_SUMMARY_MODES = frozenset(
    {
        "same_chain_direct",
        "same_chain_direct_composition",
        "cctp_direct_composition",
        "aptos_move_cctp_direct",
        "robinhood_paxos_egress_composition",
        "robinhood_paxos_ingress_composition",
        "robinhood_across_ingress_composition",
        "optimism_source_cctp",
    }
)
_DIRECT_SUMMARY_PROVIDERS = frozenset(
    {
        "raydium_clmm",
        "orca_whirlpool",
        "uniswap_v3",
        "circle_cctp",
        "circle_cctp_receive",
        "paxos_usdg_layerzero_oft",
        "across_intent_bridge",
    }
)
_SWAP_PROVIDERS = frozenset({"raydium_clmm", "orca_whirlpool", "uniswap_v3"})
_BRIDGE_PROVIDERS = _DIRECT_SUMMARY_PROVIDERS - _SWAP_PROVIDERS
_DIRECT_SUMMARY_KEYS = frozenset(
    {
        "version",
        "route",
        "from",
        "to",
        "classification",
        "mode",
        "product_classification",
        "economic_eligibility",
        "public_execution_eligible",
        "primary_selection_eligible",
        "route_minimum_guard_bps",
        "route_aggregator_used",
        "external_intent_protocol_used",
        "provider_internal_dex_aggregation_possible",
        "assetfare_fee_bps",
        "fee_collection_step_index",
        "server_signing",
        "server_submission",
        "step_count",
        "steps",
    }
)
_DIRECT_SUMMARY_STEP_KEYS = frozenset(
    {
        "index",
        "action",
        "provider",
        "from",
        "to",
        "expected_input_base",
        "minimum_input_base",
        "expected_output_base",
        "minimum_output_base",
        "assetfare_fee_bps",
        "direct_protocol",
        "external_intent_protocol",
        "aggregator_api_used",
    }
)
_POSITIVE_BASE_AMOUNT_STRING = re.compile(r"[1-9][0-9]*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CONTINUATION_VERSION = "assetfare-quote-bound-continuation-v3"
_OPENAPI_URL = "https://api.assetfare.dev/v2/openapi"
_QUOTE_PAYLOAD_SHA256_SPEC = (
    "sha256(AssetFare typed-canonical-v1 bytes of the quote without continuation_v3 after exact base-unit "
    "substitution: n=null; t/f=boolean; d=<IEEE-754 binary64 big-endian 16 lowercase hex> for each finite JSON "
    "number; s=<UTF-8 byte length>:<Unicode scalar text with lone surrogates forbidden>; a=<count>:[items]; "
    "o=<count>:{UTF-8-byte-sorted string-key/value pairs}; every non-substituted integral JSON number must be "
    "within +/-9007199254740991; substituted paths are "
    "intent.estimated_input_base, route.input_base, route.expected_output_base, route.minimum_output_base, and "
    "every route.steps[i].expected_input_base/floor_input_base/expected_output_base/minimum_output_base from "
    "direct_route_summary exact decimal strings)"
)
_CONTINUATION_KEYS = frozenset(
    {
        "version",
        "enforcement",
        "selection_status",
        "automatic_selection_forbidden",
        "caller_approved_boolean_is_not_human_proof",
        "quote_id",
        "quote_fingerprint",
        "quote_fingerprint_spec",
        "quote_fingerprint_claim",
        "issued_at",
        "expires_at",
        "ttl_seconds",
        "intent",
        "direct_route_summary_sha256",
        "quote_payload_sha256",
        "quote_payload_sha256_spec",
        "input_base_bounds",
        "minimum_output_base",
        "required_wallet_chains",
        "event_signer_public_required",
        "step_count",
        "recommended_mode",
        "allowed_modes",
        "session_header",
        "idempotency",
        "approval_v3_required_fields",
        "legacy_handoff_enforcement",
        "server_signing",
        "server_submission",
    }
)
_FINGERPRINT_CLAIM_KEYS = frozenset(
    {
        "version",
        "quote_id",
        "issued_at",
        "expires_at",
        "ttl_seconds",
        "intent",
        "direct_route_summary_sha256",
        "quote_payload_sha256",
        "quote_payload_sha256_spec",
        "input_base_bounds",
        "minimum_output_base",
        "required_wallet_chains",
        "event_signer_public_required",
        "step_count",
        "allowed_modes",
        "server_signing",
        "server_submission",
    }
)
_APPROVAL_FIELDS = [
    "direct_route_summary_sha256",
    "idempotency_key",
    "maximum_input_base",
    "minimum_output_base",
    "quote_fingerprint",
    "quote_id",
    "selected_mode",
    "selection_status",
    "version",
]

class AssetFareError(RuntimeError):
    """Fixed, bounded error. Never carries an upstream message or a cause."""


def _fail(code: str) -> None:
    raise AssetFareError(code)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_origin(base_url: str) -> str:
    parts = urlsplit(base_url or "")
    if (
        parts.scheme != "https"
        or parts.hostname != "api.assetfare.dev"
        or parts.username
        or parts.password
        or parts.port is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        _fail("assetfare_base_url_rejected")
    return _ALLOWED_ORIGIN


def _reject_signing_claims(value: Any) -> None:
    """Fail closed if any nested object claims server_signing/server_submission != False."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen = 0
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > 512 or depth > 12:
            _fail("assetfare_safety_boundary_failed")
        if isinstance(node, dict):
            for key in ("server_signing", "server_submission"):
                if key in node and node[key] is not False:
                    _fail("assetfare_safety_boundary_failed")
            for key in ("signed", "submitted"):
                if key in node and node[key] is not False:
                    _fail("assetfare_safety_boundary_failed")
            if {"private_key", "mnemonic", "seed_phrase"} & set(node):
                _fail("assetfare_safety_boundary_failed")
            for child in node.values():
                stack.append((child, depth + 1))
        elif isinstance(node, (list, tuple)):
            for child in node:
                stack.append((child, depth + 1))


class AssetFareClient:
    def __init__(
        self,
        base_url: str = _ALLOWED_ORIGIN,
        session: requests.Session | None = None,
        monotonic: Callable[[], float] | None = None,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_url = _validate_origin(base_url)
        self._session = session or requests.Session()
        self._session.trust_env = False
        # Injectable clocks (tests supply fakes). `monotonic` drives the stale
        # budget; `utcnow` (timezone-aware UTC) drives the quote freshness check.
        self._monotonic = monotonic or time.monotonic
        self._utcnow = utcnow or (lambda: datetime.now(UTC))

    # ---- transport ----
    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        budget_deadline: float,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # Reject if the stale budget is already exhausted before we start; cap the
        # per-socket inactivity timeout at whatever budget remains.
        remaining = budget_deadline - self._monotonic()
        if remaining <= 0:
            _fail("assetfare_upstream_unavailable")
        timeout = min(_SOCKET_TIMEOUT_S, remaining)
        headers = {"accept": "application/json", "x-assetfare-channel": "dify"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                timeout=timeout,
                allow_redirects=False,
                headers=headers,
                stream=True,
            )
        except requests.RequestException:
            _fail("assetfare_upstream_unavailable")
        try:
            if resp.is_redirect or 300 <= resp.status_code < 400:
                _fail("assetfare_upstream_unavailable")
            if resp.status_code == 429:
                _fail("assetfare_rate_limited")
            if resp.status_code >= 500:
                _fail("assetfare_upstream_unavailable")
            if resp.status_code >= 400:
                _fail("assetfare_request_rejected")
            media = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != "application/json" and not media.endswith("+json"):
                _fail("assetfare_response_invalid")
            declared = resp.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > _MAX_BYTES:
                _fail("assetfare_response_invalid")
            # Any streaming/decode error is sanitized to a fixed code (no cause).
            try:
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    # Stale-budget check at each chunk boundary: reject a stream
                    # that has already overrun its budget once a (possibly delayed)
                    # chunk is received. Not a mid-read absolute cancel.
                    if self._monotonic() >= budget_deadline:
                        _fail("assetfare_upstream_unavailable")
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        _fail("assetfare_response_invalid")
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks).decode("utf-8"))
            except AssetFareError:
                raise
            except Exception:  # noqa: BLE001 - deliberately bounded, no upstream text leaks
                _fail("assetfare_response_invalid")
            if not isinstance(data, dict):
                _fail("assetfare_response_invalid")
            return data
        finally:
            with contextlib.suppress(Exception):
                resp.close()

    # ---- validation helpers ----
    @staticmethod
    def _obj(data: dict[str, Any], key: str) -> dict[str, Any]:
        node = data.get(key)
        if not isinstance(node, dict):
            _fail("assetfare_response_invalid")
        return node

    @staticmethod
    def _no_sign(node: dict[str, Any]) -> None:
        if node.get("server_signing") is not False or node.get("server_submission") is not False:
            _fail("assetfare_safety_boundary_failed")

    @staticmethod
    def _validate_offer_fee(offer: dict[str, Any], step_count: int) -> None:
        """Fee EXACTLY 1bp, collected once on an eligible successful atomic action.

        fee_collection_steps holds EXACTLY one valid, in-range index. Rejects 0bp,
        fee>1 / negative / 8bp, 2-step or 0-step mismatches,
        out-of-range or duplicate indices. Also validates fee_modeled_bps,
        fee_collectible_now, and the fee_collection literal.
        """
        if offer.get("fee_collection") != _FEE_COLLECTION_CONST:
            _fail("assetfare_fee_invalid")
        fee = offer.get("assetfare_fee_bps")
        if not _int(fee) or fee != 1:
            _fail("assetfare_fee_invalid")
        modeled = offer.get("fee_modeled_bps")
        if not _int(modeled) or modeled != 1:
            _fail("assetfare_fee_invalid")
        if not isinstance(offer.get("fee_collectible_now"), bool):
            _fail("assetfare_fee_invalid")
        if modeled != fee or offer.get("fee_collectible_now") is not (fee == 1):
            _fail("assetfare_fee_collectibility_mismatch")
        steps = offer.get("fee_collection_steps")
        if not isinstance(steps, list) or not all(_int(s) for s in steps):
            _fail("assetfare_fee_invalid")
        if any(s < 0 or s >= step_count for s in steps):
            _fail("assetfare_fee_step_out_of_range")
        if len(set(steps)) != len(steps):
            _fail("assetfare_fee_step_duplicate")
        if len(steps) != 1:
            _fail("assetfare_fee_step_count_mismatch")

    @staticmethod
    def _endpoint(chain: Any, token: Any, field: str) -> str:
        if not isinstance(chain, str) or not isinstance(token, str):
            raise AssetFareError(f"assetfare_{field}_endpoint_invalid")
        token_u = token.upper()
        if (chain, token_u) not in _ENDPOINTS:
            raise AssetFareError(f"assetfare_{field}_endpoint_invalid")
        return token_u

    @staticmethod
    def _amount(amount_usd: Any) -> float:
        if (
            isinstance(amount_usd, bool)
            or not isinstance(amount_usd, (int, float))
            or not math.isfinite(float(amount_usd))
        ):
            raise AssetFareError("assetfare_amount_invalid")
        amount = float(amount_usd)
        if amount < _MIN_USD:
            raise AssetFareError("assetfare_amount_out_of_range")
        return amount

    @staticmethod
    def _evaluation_guidance(value: Any) -> dict[str, Any]:
        if type(value) is not dict or set(value) != set(_EVALUATION_GUIDANCE):
            _fail("assetfare_evaluation_guidance_invalid")
        for key, expected in _EVALUATION_GUIDANCE.items():
            actual = value[key]
            if type(actual) is not type(expected) or actual != expected:
                _fail("assetfare_evaluation_guidance_invalid")
        return dict(_EVALUATION_GUIDANCE)

    @staticmethod
    def _route_economic_guidance(value: Any) -> dict[str, Any]:
        if type(value) is not dict or set(value) != _ROUTE_GUIDANCE_KEYS:
            _fail("assetfare_route_economic_guidance_invalid")
        tested = value.get("tested_amounts_usd")
        status=value.get("recommendation_status")
        start=value.get("advisory_start_usd")
        price_verified=status=="active_price_verified"
        availability_only=status=="active_availability_only"
        if (
            status not in {"active_price_verified","active_availability_only"}
            or value.get("public_activation_status")!=status
            or value.get("public_active") is not True
            or value.get("confidence")!=("paired_all_in_snapshot" if price_verified else "availability_only_no_price_claim")
            or value.get("best_from_usd")!=start
            or value.get("best_from_verified") is not price_verified
            or value.get("availability_only") is not availability_only
            or (price_verified and start not in _GUIDANCE_STARTS)
            or (availability_only and start is not None)
            or value.get("recommended_action")!=("use_assetfare_first_at_or_above_best_from" if price_verified else "use_assetfare_when_route_availability_is_required_without_price_claim")
            or not isinstance(value.get("basis"), str)
            or not value["basis"]
            or not isinstance(tested, list)
            or tested!=[50,100,250,500,1000,2500,5000,10000]
            or value.get("tested_ceiling_usd")!=10000
            or value.get("not_an_execution_minimum") is not True
            or value.get("not_a_best_price_guarantee") is not True
            or value.get("fresh_quote_required") is not True
        ):
            _fail("assetfare_route_economic_guidance_invalid")
        return dict(value)

    @staticmethod
    def _capability_economic_guidance(caps: dict[str, Any]) -> dict[str, Any]:
        top = caps.get("economic_guidance")
        policy = caps.get("route_product_policy")
        if type(top) is not dict or set(top) != _CAPABILITY_GUIDANCE_KEYS or type(policy) is not dict:
            _fail("assetfare_economic_guidance_invalid")
        nested = policy.get("economic_guidance")
        counts = top.get("recommendation_status_counts")
        distribution = top.get("advisory_start_distribution")
        conditioned = policy.get("amount_conditioned_routes")
        inactive=policy.get("inactive_routes")
        if (
            nested != top
            or top.get("version") != "assetfare-route-economic-guidance-v3"
            or not isinstance(top.get("as_of"), str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", top["as_of"]) is None
            or top.get("route_count") != 98
            or top.get("public_active_route_count")!=54
            or top.get("public_inactive_route_count")!=44
            or top.get("verified_best_from_route_count")!=44
            or top.get("availability_only_route_count")!=10
            or top.get("currency") != "USD"
            or top.get("technical_quote_minimum_usd") != 1
            or top.get("economic_guidance_is_non_enforcing") is not True
            or top.get("amount_is_never_rejected_by_economic_guidance") is not True
            or top.get("values_change_with_market") is not True
            or top.get("fresh_quote_and_caller_decision_control") is not True
            or top.get("update_policy") != "daily_measurement_with_three_day_reactivation_hysteresis"
            or top.get("first_use_zero_allowance_scenario") is not True
            or top.get("expected_output_ranking") is not True
            or top.get("incomplete_cost_never_promoted") is not True
            or top.get("tested_ceiling_usd")!=10000
            or type(counts) is not dict
            or counts!={"active_price_verified":44,"active_availability_only":10,"inactive_economics":44}
            or type(distribution) is not dict
            or set(distribution) != {"50", "100", "250", "500", "1000", "2500", "5000", "10000"}
            or any(not _int(value) or value < 0 for value in distribution.values())
            or sum(distribution.values()) != 44
            or type(conditioned) is not dict
            or set(conditioned)!=_PRICE_VERIFIED_ROUTES
            or any(value not in _GUIDANCE_STARTS for value in conditioned.values())
            or policy.get("primary_direct_route_count")!=54
            or policy.get("external_coverage_only_route_count")!=0
            or policy.get("active_route_count")!=54
            or policy.get("inactive_route_count")!=44
            or not isinstance(inactive,list) or len(inactive)!=44 or len(set(inactive))!=44
            or not _ROUTES.isdisjoint(inactive)
            or policy.get("automatic_external_fallback_forbidden") is not True
            or policy.get("economic_guidance_url") != "https://assetfare.dev/route-economics.json"
        ):
            _fail("assetfare_economic_guidance_invalid")
        return dict(top)

    @staticmethod
    def _summary_amount(value: Any) -> str:
        if not isinstance(value, str) or _POSITIVE_BASE_AMOUNT_STRING.fullmatch(value) is None:
            _fail("assetfare_direct_route_summary_invalid")
        return value

    @staticmethod
    def _expected_direct_route(expected_from: str, expected_to: str) -> tuple[str, bool, list[tuple[str, str, str]]]:
        from_chain, from_token = expected_from.split(":", 1)
        to_chain, to_token = expected_to.split(":", 1)
        path: list[tuple[str, str, str]] = []

        def add_swap(chain: str, source: str, destination: str) -> None:
            provider = (
                "raydium_clmm"
                if chain == "solana" and {source, destination} == {"SOL", "USDC"}
                else "orca_whirlpool"
                if chain == "solana"
                else "uniswap_v3"
            )
            path.append((provider, f"{chain}:{source}", f"{chain}:{destination}"))

        def add_bridge(provider: str, source: str, destination: str, source_asset: str, destination_asset: str) -> None:
            path.append((provider, f"{source}:{source_asset}", f"{destination}:{destination_asset}"))

        if from_chain in _SOURCE_ONLY_CHAINS:
            add_bridge("circle_cctp", from_chain, to_chain, "USDC", "USDC")
            if from_chain == "optimism":
                path.append(("circle_cctp_receive", f"{to_chain}:USDC", f"{to_chain}:USDC"))
            mode = "optimism_source_cctp" if from_chain == "optimism" else "aptos_move_cctp_direct" if from_chain == "aptos" else "cctp_direct_composition"
            return mode, False, path
        if from_chain == to_chain:
            composed = from_chain == "solana" and {from_token, to_token} == {"SOL", "USDG"}
            if composed:
                add_swap("solana", from_token, "USDC")
                add_swap("solana", "USDC", to_token)
            else:
                add_swap(from_chain, from_token, to_token)
            return ("same_chain_direct_composition" if composed else "same_chain_direct"), False, path
        if from_chain == "robinhood":
            if from_token == "ETH":
                add_swap("robinhood", "ETH", "USDG")
            add_bridge("paxos_usdg_layerzero_oft", "robinhood", "solana", "USDG", "USDG")
            if to_chain == "solana":
                if to_token == "USDC":
                    add_swap("solana", "USDG", "USDC")
                elif to_token == "SOL":
                    add_swap("solana", "USDG", "USDC")
                    add_swap("solana", "USDC", "SOL")
            else:
                add_swap("solana", "USDG", "USDC")
                add_bridge("circle_cctp", "solana", to_chain, "USDC", "USDC")
                if to_token == "ETH":
                    add_swap(to_chain, "USDC", "ETH")
            return "robinhood_paxos_egress_composition", False, path
        if to_chain == "robinhood":
            if from_chain == "solana":
                if from_token == "SOL":
                    add_swap("solana", "SOL", "USDC")
                if from_token != "USDG":
                    add_swap("solana", "USDC", "USDG")
                add_bridge("paxos_usdg_layerzero_oft", "solana", "robinhood", "USDG", "USDG")
                if to_token == "ETH":
                    add_swap("robinhood", "USDG", "ETH")
            else:
                if from_token == "ETH":
                    add_swap(from_chain, "ETH", "USDC")
                add_bridge("circle_cctp", from_chain, "solana", "USDC", "USDC")
                add_swap("solana", "USDC", "USDG")
                add_bridge("paxos_usdg_layerzero_oft", "solana", "robinhood", "USDG", "USDG")
                if to_token == "ETH":
                    add_swap("robinhood", "USDG", "ETH")
            return "robinhood_paxos_ingress_composition", False, path
        if from_token != "USDC":
            add_swap(from_chain, from_token, "USDC")
        add_bridge("circle_cctp", from_chain, to_chain, "USDC", "USDC")
        if to_token != "USDC":
            add_swap(to_chain, "USDC", to_token)
        return "cctp_direct_composition", False, path

    @staticmethod
    def _raw_step_shape(step: dict[str, Any]) -> tuple[str, str, str]:
        provider = step.get("provider")
        if provider in _SWAP_PROVIDERS and step.get("kind") == "direct_swap":
            chain = step.get("chain")
            return "swap", f"{chain}:{step.get('from')}", f"{chain}:{step.get('to')}"
        if provider in _BRIDGE_PROVIDERS and step.get("kind") == "direct_bridge":
            source_asset = step.get("from_asset") if provider == "across_intent_bridge" else step.get("asset")
            destination_asset = step.get("to_asset") if provider == "across_intent_bridge" else step.get("asset")
            return "bridge", f"{step.get('from')}:{source_asset}", f"{step.get('to')}:{destination_asset}"
        if provider == "circle_cctp_receive" and step.get("kind") == "direct_receive":
            chain = step.get("chain")
            return "receive", f"{chain}:{step.get('from')}", f"{chain}:{step.get('to')}"
        _fail("assetfare_direct_route_summary_invalid")

    @classmethod
    def _validate_direct_route_summary(
        cls,
        value: Any,
        *,
        expected_from: str,
        expected_to: str,
        intent: dict[str, Any],
        offer: dict[str, Any],
        route: dict[str, Any],
        risk: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate the agent-readable route proof and bind it to the raw quote."""
        if not isinstance(value, dict) or set(value) != _DIRECT_SUMMARY_KEYS:
            _fail("assetfare_direct_route_summary_invalid")
        expected_route = f"{expected_from}->{expected_to}"
        expected_mode, expected_external, expected_path = cls._expected_direct_route(expected_from, expected_to)
        expected_guard = 50 if expected_mode == "robinhood_paxos_ingress_composition" else None
        if (
            value.get("version") != _DIRECT_SUMMARY_VERSION
            or value.get("route") != expected_route
            or value.get("from") != expected_from
            or value.get("to") != expected_to
            or value.get("mode") not in _DIRECT_SUMMARY_MODES
            or value.get("mode") != expected_mode
            or value.get("product_classification") != "primary_direct"
            or value.get("economic_eligibility") != "not_asserted_by_capability"
            or value.get("public_execution_eligible") is not True
            or value.get("primary_selection_eligible") is not True
            or value.get("route_minimum_guard_bps") != expected_guard
            or value.get("route_aggregator_used") is not False
            or value.get("assetfare_fee_bps") != 1
            or value.get("server_signing") is not False
            or value.get("server_submission") is not False
        ):
            _fail("assetfare_direct_route_summary_invalid")
        summary_steps = value.get("steps")
        step_count = value.get("step_count")
        fee_index = value.get("fee_collection_step_index")
        raw_steps = route.get("steps")
        if (
            not _int(step_count)
            or not 1 <= step_count <= 8
            or not isinstance(summary_steps, list)
            or len(summary_steps) != step_count
            or step_count != len(expected_path)
            or not isinstance(raw_steps, list)
            or len(raw_steps) != step_count
            or not _int(fee_index)
            or not 0 <= fee_index < step_count
            or offer.get("fee_collection_steps") != [fee_index]
        ):
            _fail("assetfare_direct_route_summary_invalid")

        previous_expected_output = None
        previous_minimum_output = None
        any_external = False
        fee_total = 0
        guard_total = 0
        for index, (step, raw_step) in enumerate(zip(summary_steps, raw_steps, strict=True)):
            expected_step_keys = _DIRECT_SUMMARY_STEP_KEYS | ({"minimum_guard_bps"} if expected_guard is not None else set())
            if not isinstance(step, dict) or set(step) != expected_step_keys:
                _fail("assetfare_direct_route_summary_invalid")
            if step.get("index") != index or step.get("provider") not in _DIRECT_SUMMARY_PROVIDERS:
                _fail("assetfare_direct_route_summary_invalid")
            provider = step["provider"]
            external = provider == "across_intent_bridge"
            action = "swap" if provider in _SWAP_PROVIDERS else "receive" if provider == "circle_cctp_receive" else "bridge"
            expected_provider, expected_step_from, expected_step_to = expected_path[index]
            if (
                provider != expected_provider
                or step.get("action") != action
                or step.get("from") != expected_step_from
                or step.get("to") != expected_step_to
                or step.get("direct_protocol") is not (not external)
                or step.get("external_intent_protocol") is not external
                or step.get("aggregator_api_used") is not False
                or step.get("from") not in {f"{chain}:{token}" for chain, token in _ENDPOINTS}
                or step.get("to") not in {
                    f"{chain}:{token}" for chain, token in _ENDPOINTS if chain not in _SOURCE_ONLY_CHAINS
                }
            ):
                _fail("assetfare_direct_route_summary_invalid")
            if expected_guard is not None:
                guard = step.get("minimum_guard_bps")
                if not _int(guard) or not 1 <= guard <= 500 or raw_step.get("minimum_guard_bps") != guard:
                    _fail("assetfare_direct_route_summary_invalid")
                guard_total += guard
            expected_input = cls._summary_amount(step.get("expected_input_base"))
            minimum_input = cls._summary_amount(step.get("minimum_input_base"))
            expected_output = cls._summary_amount(step.get("expected_output_base"))
            minimum_output = cls._summary_amount(step.get("minimum_output_base"))
            if int(minimum_input) > int(expected_input) or int(minimum_output) > int(expected_output):
                _fail("assetfare_direct_route_summary_invalid")
            if index == 0:
                if (
                    step.get("from") != expected_from
                    or expected_input != str(intent.get("estimated_input_base"))
                    or minimum_input != expected_input
                ):
                    _fail("assetfare_direct_route_summary_invalid")
            elif (
                step.get("from") != summary_steps[index - 1].get("to")
                or expected_input != previous_expected_output
                or minimum_input != previous_minimum_output
            ):
                _fail("assetfare_direct_route_summary_invalid")
            if index == step_count - 1 and step.get("to") != expected_to:
                _fail("assetfare_direct_route_summary_invalid")

            raw_action, raw_from, raw_to = cls._raw_step_shape(raw_step)
            raw_expected_input = raw_step.get("expected_input_base")
            raw_minimum_input = raw_step.get("floor_input_base")
            raw_expected_output = raw_step.get("expected_output_base")
            raw_minimum_output = raw_step.get("minimum_output_base")
            if (
                raw_step.get("index") != index
                or raw_step.get("provider") != provider
                or raw_action != action
                or raw_from != step.get("from")
                or raw_to != step.get("to")
                or raw_step.get("route_fee_bps") != step.get("assetfare_fee_bps")
                or any(not _int(raw) or raw < 1 for raw in (raw_expected_input, raw_minimum_input, raw_expected_output, raw_minimum_output))
                or expected_input != str(raw_expected_input)
                or minimum_input != str(raw_minimum_input)
                or expected_output != str(raw_expected_output)
                or minimum_output != str(raw_minimum_output)
            ):
                _fail("assetfare_direct_route_summary_invalid")
            step_fee = step.get("assetfare_fee_bps")
            if not _int(step_fee) or step_fee not in (0, 1) or (step_fee == 1) is not (index == fee_index):
                _fail("assetfare_direct_route_summary_invalid")
            fee_total += step_fee
            any_external = any_external or external
            previous_expected_output = expected_output
            previous_minimum_output = minimum_output

        if (
            fee_total != 1
            or guard_total != (expected_guard or 0)
            or any_external is not expected_external
            or value.get("classification") != ("external_intent" if any_external else "direct_protocol_only")
            or value.get("external_intent_protocol_used") is not any_external
            or value.get("provider_internal_dex_aggregation_possible") is not any_external
            or route.get("mode") != value.get("mode")
            or route.get("input_base") != intent.get("estimated_input_base")
            or str(route.get("expected_output_base")) != previous_expected_output
            or str(route.get("minimum_output_base")) != previous_minimum_output
            or route.get("aggregator_api_used") is not False
            or route.get("external_intent_protocol_used") is not any_external
            or risk.get("external_intent_protocol_used") is not any_external
            or risk.get("provider_internal_dex_aggregation_possible") is not any_external
            or any(route.get(key) != value.get(key) for key in ("product_classification", "economic_eligibility", "public_execution_eligible", "primary_selection_eligible", "route_minimum_guard_bps"))
        ):
            _fail("assetfare_direct_route_summary_invalid")
        return dict(value)

    @staticmethod
    def _canonical_sha256(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _typed_canonical(value: Any) -> bytes:
        if value is None:
            return b"n"
        if value is True:
            return b"t"
        if value is False:
            return b"f"
        if isinstance(value, int):
            if abs(value) > 9_007_199_254_740_991:
                _fail("assetfare_continuation_v3_invalid")
            return b"d" + struct.pack(">d", float(value)).hex().encode("ascii")
        if isinstance(value, float):
            if not math.isfinite(value) or (value.is_integer() and abs(value) > 9_007_199_254_740_991):
                _fail("assetfare_continuation_v3_invalid")
            return b"d" + struct.pack(">d", value).hex().encode("ascii")
        if isinstance(value, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
                _fail("assetfare_continuation_v3_invalid")
            encoded = value.encode("utf-8")
            return b"s" + str(len(encoded)).encode("ascii") + b":" + encoded
        if isinstance(value, list):
            return (
                b"a"
                + str(len(value)).encode("ascii")
                + b":["
                + b"".join(AssetFareClient._typed_canonical(item) for item in value)
                + b"]"
            )
        if isinstance(value, dict):
            if any(
                not isinstance(key, str)
                or any(0xD800 <= ord(character) <= 0xDFFF for character in key)
                for key in value
            ):
                _fail("assetfare_continuation_v3_invalid")
            items = sorted(value.items(), key=lambda item: item[0].encode("utf-8"))
            return (
                b"o"
                + str(len(items)).encode("ascii")
                + b":" + b"{"
                + b"".join(
                    AssetFareClient._typed_canonical(key) + AssetFareClient._typed_canonical(item)
                    for key, item in items
                )
                + b"}"
            )
        _fail("assetfare_continuation_v3_invalid")

    @classmethod
    def _quote_payload_projection(
        cls, quote: dict[str, Any], direct_route_summary: dict[str, Any]
    ) -> dict[str, Any]:
        payload = copy.deepcopy({key: item for key, item in quote.items() if key != "continuation_v3"})
        try:
            summary_steps = direct_route_summary["steps"]
            route = payload["route"]
            raw_steps = route["steps"]
            intent = payload["intent"]
            if not isinstance(summary_steps, list) or not summary_steps or len(raw_steps) != len(summary_steps):
                raise ValueError
            intent["estimated_input_base"] = summary_steps[0]["expected_input_base"]
            route["input_base"] = summary_steps[0]["expected_input_base"]
            route["expected_output_base"] = summary_steps[-1]["expected_output_base"]
            route["minimum_output_base"] = summary_steps[-1]["minimum_output_base"]
            for raw, exact in zip(raw_steps, summary_steps, strict=True):
                raw["expected_input_base"] = exact["expected_input_base"]
                raw["floor_input_base"] = exact["minimum_input_base"]
                raw["expected_output_base"] = exact["expected_output_base"]
                raw["minimum_output_base"] = exact["minimum_output_base"]
        except (KeyError, TypeError, ValueError):
            _fail("assetfare_continuation_v3_invalid")
        return payload

    @classmethod
    def _quote_payload_sha256(cls, quote: dict[str, Any], direct_route_summary: dict[str, Any]) -> str:
        return hashlib.sha256(cls._typed_canonical(cls._quote_payload_projection(quote, direct_route_summary))).hexdigest()

    @classmethod
    def _validate_continuation_v3(
        cls,
        value: Any,
        *,
        quote: dict[str, Any],
        intent: dict[str, Any],
        route: dict[str, Any],
        direct_route_summary: dict[str, Any],
        current_time: datetime,
    ) -> dict[str, Any]:
        """Validate the complete v3 quote binding, then return only safe discovery metadata.

        This quote-only plugin deliberately does not return an approval object, collect
        wallets, select a mode, or call prepare/session.  The descriptor is enough for
        an agent to understand the separate caller-operated continuation contract.
        """
        if type(value) is not dict or set(value) != _CONTINUATION_KEYS:
            _fail("assetfare_continuation_v3_invalid")
        claim = value.get("quote_fingerprint_claim")
        bounds = value.get("input_base_bounds")
        session_header = value.get("session_header")
        idempotency = value.get("idempotency")
        required_wallet_chains = value.get("required_wallet_chains")
        allowed_modes = value.get("allowed_modes")
        step_count = direct_route_summary["step_count"]
        expected_modes = ["session"] if step_count > 1 else ["one_shot", "session"]
        expected_recommended = "session" if step_count > 1 else "one_shot_or_session"
        expected_wallet_chains = sorted(
            {
                endpoint.split(":", 1)[0]
                for step in direct_route_summary["steps"]
                for endpoint in (step["from"], step["to"])
            }
        )
        expected_event_signer = any(
            step["provider"] == "circle_cctp" and step["from"].startswith("solana:")
            for step in direct_route_summary["steps"]
        )
        if (
            value.get("version") != _CONTINUATION_VERSION
            or value.get("enforcement") != "server_enforced_quote_binding"
            or value.get("selection_status") != "unranked_candidate"
            or value.get("automatic_selection_forbidden") is not True
            or value.get("caller_approved_boolean_is_not_human_proof") is not True
            or value.get("quote_id") != quote.get("quote_id")
            or not isinstance(value.get("quote_fingerprint"), str)
            or _HASH.fullmatch(value["quote_fingerprint"]) is None
            or value.get("quote_fingerprint_spec")
            != "sha256(UTF-8 sorted-key compact JSON of quote_fingerprint_claim; every numeric claim is a non-exponent decimal string)"
            or not _int(value.get("ttl_seconds"))
            or value["ttl_seconds"] != quote.get("ttl_seconds")
            or value.get("intent") != intent
            or not isinstance(value.get("direct_route_summary_sha256"), str)
            or _HASH.fullmatch(value["direct_route_summary_sha256"]) is None
            or not isinstance(value.get("quote_payload_sha256"), str)
            or _HASH.fullmatch(value["quote_payload_sha256"]) is None
            or value.get("quote_payload_sha256_spec") != _QUOTE_PAYLOAD_SHA256_SPEC
            or not isinstance(bounds, dict)
            or set(bounds) != {"minimum", "maximum"}
            or bounds.get("minimum") != str(intent.get("estimated_input_base"))
            or bounds.get("maximum") != str(intent.get("estimated_input_base"))
            or value.get("minimum_output_base") != str(route.get("minimum_output_base"))
            or value.get("step_count") != step_count
            or allowed_modes != expected_modes
            or value.get("recommended_mode") != expected_recommended
            or value.get("approval_v3_required_fields") != _APPROVAL_FIELDS
            or value.get("legacy_handoff_enforcement") != "legacy_advisory"
            or value.get("server_signing") is not False
            or value.get("server_submission") is not False
        ):
            _fail("assetfare_continuation_v3_invalid")
        if (
            not isinstance(required_wallet_chains, list)
            or not required_wallet_chains
            or required_wallet_chains != expected_wallet_chains
            or value.get("event_signer_public_required") is not expected_event_signer
            or type(session_header) is not dict
            or set(session_header)
            != {"name", "required_for", "caller_generated", "minimum_entropy_bits", "server_returns_raw_value"}
            or session_header
            != {
                "name": "X-AssetFare-Session-Token",
                "required_for": "session",
                "caller_generated": True,
                "minimum_entropy_bits": 256,
                "server_returns_raw_value": False,
            }
            or type(idempotency) is not dict
            or set(idempotency) != {"required", "field", "pattern", "scope"}
            or idempotency
            != {
                "required": True,
                "field": "idempotency_key",
                "pattern": "^[A-Za-z0-9._:-]{8,128}$",
                "scope": "quote_and_selected_mode",
            }
        ):
            _fail("assetfare_continuation_v3_invalid")
        if type(claim) is not dict or set(claim) != _FINGERPRINT_CLAIM_KEYS:
            _fail("assetfare_continuation_v3_invalid")
        try:
            amount_decimal = format(Decimal(str(intent.get("amount_usd"))), "f")
        except InvalidOperation:
            _fail("assetfare_continuation_v3_invalid")
        if "." in amount_decimal:
            amount_decimal = amount_decimal.rstrip("0").rstrip(".")
        if amount_decimal in ("", "-0"):
            amount_decimal = "0"
        expected_claim = {
            "version": _CONTINUATION_VERSION,
            "quote_id": value["quote_id"],
            "issued_at": value.get("issued_at"),
            "expires_at": value.get("expires_at"),
            "ttl_seconds": str(value["ttl_seconds"]),
            "intent": {
                "from": intent.get("from"),
                "to": intent.get("to"),
                "amount_usd_decimal": amount_decimal,
                "estimated_input_base": str(intent.get("estimated_input_base")),
            },
            "direct_route_summary_sha256": value["direct_route_summary_sha256"],
            "quote_payload_sha256": value["quote_payload_sha256"],
            "quote_payload_sha256_spec": _QUOTE_PAYLOAD_SHA256_SPEC,
            "input_base_bounds": dict(bounds),
            "minimum_output_base": value["minimum_output_base"],
            "required_wallet_chains": list(required_wallet_chains),
            "event_signer_public_required": value["event_signer_public_required"],
            "step_count": str(step_count),
            "allowed_modes": list(expected_modes),
            "server_signing": False,
            "server_submission": False,
        }
        if (
            claim != expected_claim
            or cls._canonical_sha256(claim) != value["quote_fingerprint"]
            or cls._canonical_sha256(direct_route_summary) != value["direct_route_summary_sha256"]
            or cls._quote_payload_sha256(quote, direct_route_summary) != value["quote_payload_sha256"]
        ):
            _fail("assetfare_continuation_v3_invalid")
        try:
            issued = datetime.fromisoformat(str(value.get("issued_at")))
            expires = datetime.fromisoformat(str(value.get("expires_at")))
        except ValueError:
            _fail("assetfare_continuation_v3_invalid")
        if (
            issued.tzinfo is None
            or expires.tzinfo is None
            or expires <= issued
            or abs((expires - issued).total_seconds() - value["ttl_seconds"]) > 0.001
            or expires <= current_time
            or issued > current_time + timedelta(seconds=_MAX_FUTURE_SKEW_S)
        ):
            _fail("assetfare_continuation_v3_invalid")
        return {
            "version": _CONTINUATION_VERSION,
            "quote_id": value["quote_id"],
            "quote_fingerprint": value["quote_fingerprint"],
            "expires_at": value["expires_at"],
            "ttl_seconds": value["ttl_seconds"],
            "selection_status": "unranked_candidate",
            "required_wallet_chains": list(required_wallet_chains),
            "event_signer_public_required": value["event_signer_public_required"],
            "allowed_modes": list(expected_modes),
            "recommended_mode": expected_recommended,
            "openapi_url": _OPENAPI_URL,
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

    # ---- read-only capabilities ----
    def get_capabilities(self) -> dict[str, Any]:
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        caps = self._request("GET", "/v2/capabilities", None, budget_deadline)
        status = self._request("GET", "/v2/status", None, budget_deadline)
        if caps.get("status") != "capped_public_agent_release" or caps.get("public_api_enabled") is not True:
            _fail("assetfare_safety_boundary_failed")
        # Exactly the 54 economically active directed routes are execution-ready.
        if (
            caps.get("directed_conversion_routes") != _EXPECTED_ROUTES
            or caps.get("unsigned_route_plans_ready") != _EXPECTED_ROUTES
            or caps.get("execution_ready_routes") != _EXECUTION_READY_ROUTES
            or caps.get("phase_b_blocked_routes") != _PHASE_B_BLOCKED_ROUTES
        ):
            _fail("assetfare_safety_boundary_failed")
        self._no_sign(caps)
        if status.get("status") != "capped_public_agent_release":
            _fail("assetfare_safety_boundary_failed")
        self._no_sign(status)
        chains = caps.get("chains")
        if not isinstance(chains, list) or len(chains) != len(_CHAINS) or set(chains) != set(_CHAINS):
            _fail("assetfare_safety_boundary_failed")
        endpoints = caps.get("asset_endpoints")
        if not isinstance(endpoints, list) or len(endpoints) != len(_ENDPOINTS):
            _fail("assetfare_safety_boundary_failed")
        got = set()
        for ep in endpoints:
            if not isinstance(ep, dict) or "chain" not in ep or "token" not in ep:
                _fail("assetfare_safety_boundary_failed")
            got.add((ep["chain"], str(ep["token"]).upper()))
        if len(got) != len(_ENDPOINTS) or got != _ENDPOINTS:
            _fail("assetfare_safety_boundary_failed")
        so_eps = caps.get("source_only_asset_endpoints")
        if not isinstance(so_eps, list) or len(so_eps) != len(_SOURCE_ONLY_ENDPOINTS):
            _fail("assetfare_safety_boundary_failed")
        so_got = set()
        for ep in so_eps:
            if not isinstance(ep, dict) or "chain" not in ep or "token" not in ep:
                _fail("assetfare_safety_boundary_failed")
            so_got.add((ep["chain"], str(ep["token"]).upper()))
        if so_got != _SOURCE_ONLY_ENDPOINTS:
            _fail("assetfare_safety_boundary_failed")
        source_only = caps.get("source_only_routes")
        if (
            not isinstance(source_only, list)
            or len(source_only) != len(_SOURCE_ONLY_ROUTES)
            or set(source_only) != _SOURCE_ONLY_ROUTES
        ):
            _fail("assetfare_safety_boundary_failed")
        blocked = caps.get("blocked_source_only_routes")
        if not isinstance(blocked, list) or blocked:
            _fail("assetfare_safety_boundary_failed")
        amount_policy = caps.get("amount_usd")
        if (
            not isinstance(amount_policy, dict)
            or isinstance(amount_policy.get("minimum"), bool)
            or not isinstance(amount_policy.get("minimum"), (int, float))
            or not math.isfinite(float(amount_policy["minimum"]))
            or float(amount_policy["minimum"]) != _MIN_USD
            or amount_policy.get("maximum") is not None
            or amount_policy.get("policy") != "no_business_maximum"
        ):
            _fail("assetfare_amount_policy_invalid")
        evaluation_guidance = self._evaluation_guidance(caps.get("evaluation_guidance"))
        economic_guidance = self._capability_economic_guidance(caps)
        availability_keys={"execution_implemented_routes","currently_prepare_ready_routes","temporarily_unavailable_routes","temporarily_unavailable_route_count","execution_availability"}
        present=availability_keys & set(caps)
        if present and present!=availability_keys:
            _fail("assetfare_current_availability_invalid")
        current_ready=None;temporary=[];availability=None
        if present:
            current_ready=caps.get("currently_prepare_ready_routes");temporary=caps.get("temporarily_unavailable_routes");availability=caps.get("execution_availability")
            if (caps.get("execution_implemented_routes")!=_EXPECTED_ROUTES or not _int(current_ready) or not 0<=current_ready<=_EXPECTED_ROUTES or not isinstance(temporary,list) or len(set(temporary))!=len(temporary) or any(route not in _ROUTES for route in temporary) or caps.get("temporarily_unavailable_route_count")!=len(temporary) or current_ready!=_EXPECTED_ROUTES-len(temporary) or not isinstance(availability,dict) or availability.get("provider")!="circle_iris" or availability.get("status") not in {"available","degraded","unknown"} or (len(temporary)==0)!=(availability.get("status")=="available") or availability.get("guarantees_future_availability") is not False):
                _fail("assetfare_current_availability_invalid")
        return {
            "status": caps["status"],
            "chains": sorted(_CHAINS),
            "asset_endpoints": [f"{c}:{t}" for (c, t) in sorted(_ENDPOINTS)],
            "directed_conversion_routes": _EXPECTED_ROUTES,
            "unsigned_route_plans_ready": _EXPECTED_ROUTES,
            "execution_ready_routes": _EXECUTION_READY_ROUTES,
            "execution_implemented_routes": _EXECUTION_READY_ROUTES,
            "currently_prepare_ready_routes": current_ready,
            "temporarily_unavailable_routes": temporary,
            "execution_availability": availability,
            "phase_b_blocked_routes": _PHASE_B_BLOCKED_ROUTES,
            "source_only_asset_endpoints": sorted(f"{c}:{t}" for (c, t) in _SOURCE_ONLY_ENDPOINTS),
            "source_only_routes": sorted(_SOURCE_ONLY_ROUTES),
            "blocked_source_only_routes": [],
            "amount_usd": {
                "minimum": _MIN_USD,
                "maximum": None,
                "policy": "no_business_maximum",
            },
            "evaluation_guidance": copy.deepcopy(_PUBLIC_EVALUATION_GUIDANCE),
            "economic_guidance": economic_guidance,
            "economic_guidance_url": "https://assetfare.dev/route-economics.json",
            "quote_only_discovery": True,
            "server_signs_or_submits": False,
        }

    # ---- read-only quote ----
    def get_quote(
        self, from_chain: str, from_token: str, to_chain: str, to_token: str, amount_usd: Any
    ) -> dict[str, Any]:
        amount = self._amount(amount_usd)
        from_u = self._endpoint(from_chain, from_token, "source")
        to_u = self._endpoint(to_chain, to_token, "destination")
        if (from_chain, from_u) == (to_chain, to_u):
            raise AssetFareError("assetfare_identity_route_rejected")
        if to_chain in _SOURCE_ONLY_CHAINS:
            raise AssetFareError("assetfare_destination_endpoint_invalid")
        source_only = from_chain in _SOURCE_ONLY_CHAINS
        route_label=f"{from_chain}:{from_u}->{to_chain}:{to_u}"
        if route_label not in _ROUTES:
            raise AssetFareError("assetfare_route_inactive_or_unsupported")
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request(
            "POST",
            "/v2/quote",
            {
                "from_chain": from_chain,
                "from_token": from_u,
                "to_chain": to_chain,
                "to_token": to_u,
                "amount_usd": amount,
            },
            budget_deadline,
        )
        _reject_signing_claims(data)
        intent = self._obj(data, "intent")
        offer = self._obj(data, "offer")
        route = self._obj(data, "route")
        risk = self._obj(data, "risk")
        execution = self._obj(data, "execution")
        route_economic_guidance = self._route_economic_guidance(data.get("economic_guidance"))
        if (
            route_label in _PRICE_VERIFIED_ROUTES and route_economic_guidance["recommendation_status"]!="active_price_verified"
        ) or (
            route_label in _AVAILABILITY_ONLY_ROUTES and route_economic_guidance["recommendation_status"]!="active_availability_only"
        ):
            _fail("assetfare_route_economic_guidance_invalid")

        # top-level strict fields
        if data.get("status") != "capped_public_agent_release":
            _fail("assetfare_response_invalid")
        try:
            uuid.UUID(str(data.get("quote_id")))
        except (ValueError, AttributeError, TypeError):
            _fail("assetfare_response_invalid")
        ttl = data.get("ttl_seconds")
        if not _int(ttl) or not (0 < ttl <= _MAX_TTL_SECONDS):
            _fail("assetfare_response_invalid")
        # as_of must be an RFC3339 timezone-aware datetime (Z or numeric offset);
        # a naive (no timezone) timestamp is rejected.
        as_of = data.get("as_of")
        if not isinstance(as_of, str) or "T" not in as_of:
            _fail("assetfare_response_invalid")
        try:
            as_of_dt = datetime.fromisoformat(as_of)  # Python >= 3.11 parses the trailing 'Z'
        except ValueError:
            _fail("assetfare_response_invalid")
        if as_of_dt.tzinfo is None or as_of_dt.utcoffset() is None:
            _fail("assetfare_response_invalid")
        now = self._utcnow()
        # Reject an as_of stamped excessively in the future (clock-skew / forgery),
        # and a quote whose expiry (as_of + ttl) has already passed (stale).
        if as_of_dt > now + timedelta(seconds=_MAX_FUTURE_SKEW_S):
            _fail("assetfare_response_invalid")
        if as_of_dt + timedelta(seconds=ttl) < now:
            _fail("assetfare_response_invalid")

        expected_from = f"{from_chain}:{from_u}"
        expected_to = f"{to_chain}:{to_u}"

        # intent
        if intent.get("from") != expected_from or intent.get("to") != expected_to:
            _fail("assetfare_response_invalid")
        if not _finite(intent.get("amount_usd")) or float(intent["amount_usd"]) != amount:
            _fail("assetfare_response_invalid")
        if not _int(intent.get("estimated_input_base")) or intent["estimated_input_base"] < 1:
            _fail("assetfare_response_invalid")

        # route: label bound to the corridor, >=1 object steps, latency, sign flags false
        if route.get("route") != f"{expected_from}->{expected_to}":
            _fail("assetfare_response_invalid")
        steps = route.get("steps")
        if not isinstance(steps, list) or len(steps) < 1 or not all(isinstance(s, dict) for s in steps):
            _fail("assetfare_response_invalid")
        if not _int(route.get("quote_latency_ms")) or route["quote_latency_ms"] < 0:
            _fail("assetfare_response_invalid")
        self._no_sign(route)

        # offer: native and USD ordering expected >= minimum > 0, output symbol
        exp, mn = offer.get("expected_receive_amount"), offer.get("estimated_min_receive_amount")
        exp_usd, mn_usd = offer.get("expected_receive_usd"), offer.get("estimated_min_receive_usd")
        for v in (exp, mn, exp_usd, mn_usd):
            if not _finite(v):
                _fail("assetfare_response_invalid")
        if not (float(mn) > 0 and float(exp) >= float(mn)):
            _fail("assetfare_response_invalid")
        if not (float(mn_usd) > 0 and float(exp_usd) >= float(mn_usd)):
            _fail("assetfare_response_invalid")
        if offer.get("output_symbol") != to_u:
            _fail("assetfare_response_invalid")
        # Fee EXACTLY 1bp + modeled/collectible + fee_collection literal.
        self._validate_offer_fee(offer, len(steps))
        fee = offer["assetfare_fee_bps"]
        fee_steps = offer["fee_collection_steps"]
        eta = offer.get("estimated_time_seconds")
        if eta is not None and (not _int(eta) or eta < 0):
            _fail("assetfare_response_invalid")

        # Root-level total token-path cost. New Core supplies exact provider components; rollback Core is supported by
        # deriving the receive-value delta and explicitly labeling provider detail unavailable.
        cost=data.get("cost_summary")
        expected_cost=max(0.0,amount-float(exp_usd));maximum_cost=max(0.0,amount-float(mn_usd))
        if cost is None:
            small=maximum_cost/amount>=.01
            cost={"scope":"token_path_only_network_gas_excluded","input_value_usd":amount,"expected_receive_value_usd":float(exp_usd),"minimum_receive_value_usd":float(mn_usd),"expected_total_cost_usd":expected_cost,"maximum_total_cost_usd":maximum_cost,"expected_total_cost_percent":expected_cost/amount*100,"maximum_total_cost_percent":maximum_cost/amount*100,"assetfare_service_fee":{"bps":1,"estimated_usd":amount/10_000,"included_in_receive_amount":True,"note":"Exact 1bp AssetFare service fee with no service-fee maximum; not the total route cost"},"provider_fee_components":[],"unpriced_costs":["provider_fee_breakdown_unavailable_legacy_core","source_chain_network_fee"],"rankable_all_in":False,"small_amount_warning":small,"warning":"Legacy-core fallback: total derived from receive value; provider detail unavailable." if small else None}
        close=lambda a,b,t=0.000001:abs(float(a)-float(b))<=t
        if not isinstance(cost,dict) or cost.get("scope")!="token_path_only_network_gas_excluded" or cost.get("rankable_all_in") is not False or not all(_finite(cost.get(key)) for key in ("input_value_usd","expected_receive_value_usd","minimum_receive_value_usd")) or not close(cost.get("input_value_usd"),amount) or not close(cost.get("expected_receive_value_usd"),float(exp_usd)) or not close(cost.get("minimum_receive_value_usd"),float(mn_usd)):
            _fail("assetfare_cost_summary_invalid")
        for key in ("expected_total_cost_usd","maximum_total_cost_usd","expected_total_cost_percent","maximum_total_cost_percent"):
            if not _finite(cost.get(key)) or float(cost[key])<0:_fail("assetfare_cost_summary_invalid")
        if not close(cost["expected_total_cost_usd"],expected_cost) or not close(cost["maximum_total_cost_usd"],maximum_cost) or not close(cost["expected_total_cost_percent"],expected_cost/amount*100,0.0001) or not close(cost["maximum_total_cost_percent"],maximum_cost/amount*100,0.0001) or float(cost["maximum_total_cost_usd"])<float(cost["expected_total_cost_usd"]):
            _fail("assetfare_cost_summary_invalid")
        service=cost.get("assetfare_service_fee")
        if not isinstance(service,dict) or service.get("bps")!=1 or service.get("included_in_receive_amount") is not True or not _finite(service.get("estimated_usd")) or not close(service["estimated_usd"],amount/10_000):
            _fail("assetfare_cost_summary_invalid")
        components=cost.get("provider_fee_components");unpriced=cost.get("unpriced_costs");small=cost.get("small_amount_warning")
        if not isinstance(components,list) or not isinstance(unpriced,list) or not unpriced or not isinstance(small,bool):
            _fail("assetfare_cost_summary_invalid")
        component_expected=component_maximum=0.0
        for row in components:
            if not isinstance(row,dict) or not _finite(row.get("expected_usd")) or not _finite(row.get("maximum_usd")) or float(row["expected_usd"])<0 or float(row["maximum_usd"])<float(row["expected_usd"]):_fail("assetfare_cost_summary_invalid")
            component_expected+=float(row["expected_usd"]);component_maximum+=float(row["maximum_usd"])
        separately=cost.get("separately_paid_costs");current=isinstance(separately,list)
        separate_expected=separate_maximum=0.0
        if current:
            required=("token_path_expected_cost_usd","token_path_maximum_cost_usd","expected_all_in_cost_usd_estimate","maximum_all_in_cost_usd_estimate","expected_all_in_cost_percent_estimate","maximum_all_in_cost_percent_estimate","all_in_estimate_complete","native_balance_requirements","source_native_balance_required")
            if not all(key in cost for key in required) or not isinstance(cost["all_in_estimate_complete"],bool) or not isinstance(cost["native_balance_requirements"],list):_fail("assetfare_cost_summary_invalid")
            for row in separately:
                if not isinstance(row,dict) or row.get("kind") not in {"layerzero_native_fee","source_chain_network_fee_estimate"} or not isinstance(row.get("paid_in"),str) or row.get("included_in_receive_amount") is not False or not _finite(row.get("expected_usd")) or not _finite(row.get("maximum_usd")) or float(row["expected_usd"])<0 or float(row["maximum_usd"])<float(row["expected_usd"]):_fail("assetfare_cost_summary_invalid")
                separate_expected+=float(row["expected_usd"]);separate_maximum+=float(row["maximum_usd"])
            if not close(cost["token_path_expected_cost_usd"],expected_cost) or not close(cost["token_path_maximum_cost_usd"],maximum_cost) or not close(cost["expected_all_in_cost_usd_estimate"],expected_cost+separate_expected) or not close(cost["maximum_all_in_cost_usd_estimate"],maximum_cost+separate_maximum) or not close(cost["expected_all_in_cost_percent_estimate"],(expected_cost+separate_expected)/amount*100,0.0001) or not close(cost["maximum_all_in_cost_percent_estimate"],(maximum_cost+separate_maximum)/amount*100,0.0001):_fail("assetfare_cost_summary_invalid")
            source_required=cost["source_native_balance_required"]
            if source_required is not None and (not isinstance(source_required,dict) or source_required.get("included_in_receive_amount") is not False or not isinstance(source_required.get("paid_in"),str) or not str(source_required.get("expected_amount_base") or "").isdigit() or not str(source_required.get("maximum_amount_base") or "").isdigit() or int(source_required["maximum_amount_base"])<int(source_required["expected_amount_base"])):_fail("assetfare_cost_summary_invalid")
        warning_percent=float(cost["maximum_all_in_cost_percent_estimate"]) if current else float(cost["maximum_total_cost_percent"])
        if component_expected>expected_cost+0.000001 or component_maximum>maximum_cost+0.000001 or small!=(warning_percent>=1) or (small and not isinstance(cost.get("warning"),str)) or (not small and cost.get("warning") is not None):_fail("assetfare_cost_summary_invalid")
        eta_summary=data.get("eta")
        if eta_summary is not None:
            if not isinstance(eta_summary,dict) or eta_summary.get("estimated_time_seconds")!=eta or not isinstance(eta_summary.get("complete_route_estimate"),bool):_fail("assetfare_eta_invalid")
            complete=eta_summary["complete_route_estimate"];eta_range=eta_summary.get("estimated_time_range_seconds")
            if complete:
                if eta is None or not isinstance(eta_range,list) or len(eta_range)!=2 or not all(_int(v) and v>=0 for v in eta_range) or eta_range[0]>eta_range[1] or eta_range[1]!=eta:_fail("assetfare_eta_invalid")
            elif eta is not None or eta_range is not None:_fail("assetfare_eta_invalid")

        # risk: non-atomic bool, fresh-quote flag true, sign flags false
        if not isinstance(risk.get("non_atomic"), bool):
            _fail("assetfare_response_invalid")
        if risk.get("fresh_quote_required_each_step") is not True:
            _fail("assetfare_response_invalid")
        self._no_sign(risk)
        direct_route_summary = self._validate_direct_route_summary(
            data.get("direct_route_summary"),
            expected_from=expected_from,
            expected_to=expected_to,
            intent=intent,
            offer=offer,
            route=route,
            risk=risk,
        )
        continuation_descriptor = self._validate_continuation_v3(
            data.get("continuation_v3"),
            quote=data,
            intent=intent,
            route=route,
            direct_route_summary=direct_route_summary,
            current_time=self._utcnow(),
        )

        # Every supported route is execution-ready through caller-operated wallets.
        if not isinstance(execution.get("first_unsigned_action_supported"), bool):
            _fail("assetfare_response_invalid")
        if execution.get("future_actions_require_verified_receipts") is not True:
            _fail("assetfare_response_invalid")
        if (
            execution.get("supported") is not True
            or execution.get("first_unsigned_action_supported") is not True
            or ("blocker" in execution and execution.get("blocker") is not None)
        ):
            _fail("assetfare_execution_boundary_failed")

        # The AssetFare fee is conditional on the eligible successful executor step.
        fee_steps_out = [int(s) for s in fee_steps]
        fee_note = "AssetFare 1bp is collected only on the eligible successful atomic action."

        return {
            "from": expected_from,
            "to": expected_to,
            "amount_usd": amount,
            "output_symbol": to_u,
            "expected_receive_amount": float(exp),
            "estimated_min_receive_amount": float(mn),
            "expected_receive_usd": float(exp_usd),
            "estimated_min_receive_usd": float(mn_usd),
            "assetfare_fee_bps": fee,
            "fee_modeled_bps": int(offer["fee_modeled_bps"]),
            "fee_collectible_now": bool(offer["fee_collectible_now"]),
            "assetfare_fee_conditional": fee > 0,
            "fee_collection_steps": fee_steps_out,
            "fee_collection": _FEE_COLLECTION_CONST,
            "assetfare_fee_note": fee_note,
            "estimated_time_seconds": eta if _int(eta) else None,
            "cost_summary": cost,
            "eta": eta_summary or {"estimated_time_seconds":eta if _int(eta) else None,"estimated_time_range_seconds":None,"complete_route_estimate":False,"sources":[],"note":"Legacy-core fallback; full ETA provenance unavailable"},
            "direct_route_summary": direct_route_summary,
            "continuation_descriptor": continuation_descriptor,
            "non_atomic": risk["non_atomic"],
            "quote_id": data["quote_id"],
            "as_of": as_of,
            "ttl_seconds": ttl,
            "source_only": source_only,
            "execution_supported": True,
            "execution_blocker": None,
            "economic_guidance": route_economic_guidance,
            "economic_guidance_url": "https://assetfare.dev/route-economics.json",
            "evaluation_guidance": copy.deepcopy(_PUBLIC_EVALUATION_GUIDANCE),
            "agent_guidance": {
                "best_from_usd": route_economic_guidance["best_from_usd"],
                "best_from_verified": route_economic_guidance["best_from_verified"],
                "availability_only": route_economic_guidance["availability_only"],
                "recommendation_status": route_economic_guidance["recommendation_status"],
                "recommended_action": route_economic_guidance["recommended_action"],
                "use_assetfare_first_at_or_above_best_from": route_economic_guidance["best_from_verified"] is True and amount >= route_economic_guidance["best_from_usd"],
                "best_from_not_100_percent_guarantee": True,
                "confirm_fresh_assetfare_quote": True,
                "compare_with_other_routes": False,
                "selection_status": "unranked_candidate",
                "automatic_selection_forbidden": True,
            },
            "server_signs_or_submits": False,
        }
