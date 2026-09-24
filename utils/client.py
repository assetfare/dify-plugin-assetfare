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
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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

_CHAINS = ("arbitrum", "base", "optimism", "polygon", "robinhood", "solana")
# Source chains that MAY be used in a quote. Destinations exclude
# the source-only chains.
_DESTINATION_CHAINS = frozenset({"solana", "base", "arbitrum", "robinhood"})
# Source-only chains: native USDC may leave them (to Base/Arbitrum USDC) but they
# are never a destination. Their four directional corridors are execution-ready
# through the same live quote surface as every other route.
_SOURCE_ONLY_CHAINS = frozenset({"optimism", "polygon"})
_ENDPOINTS = frozenset(
    {
        ("solana", "SOL"),
        ("solana", "USDC"),
        ("solana", "USDG"),
        ("base", "ETH"),
        ("base", "USDC"),
        ("arbitrum", "ETH"),
        ("arbitrum", "USDC"),
        ("robinhood", "ETH"),
        ("robinhood", "USDG"),
        ("polygon", "USDC"),
        ("optimism", "USDC"),
    }
)
_SOURCE_ONLY_ENDPOINTS = frozenset({("optimism", "USDC"), ("polygon", "USDC")})
_SOURCE_ONLY_ROUTES = frozenset(
    {
        "polygon:USDC->base:USDC",
        "polygon:USDC->arbitrum:USDC",
        "optimism:USDC->base:USDC",
        "optimism:USDC->arbitrum:USDC",
    }
)
_ROUTES=frozenset(
    f"{fc}:{ft}->{tc}:{tt}"
    for fc,ft in _ENDPOINTS for tc,tt in _ENDPOINTS
    if tc in _DESTINATION_CHAINS and (fc,ft)!=(tc,tt)
    and (fc not in _SOURCE_ONLY_CHAINS or (ft=="USDC" and tc in {"base","arbitrum"} and tt=="USDC"))
)
_EXPECTED_ROUTES = 76  # directed quote-discovery routes (6-chain surface)
_EXECUTION_READY_ROUTES = 76
_PHASE_B_BLOCKED_ROUTES = 0
_MIN_USD = 1.0

_EVALUATION_GUIDANCE = {
    "schema_version": 1,
    "route_minimum_usd": 1,
    "reachability_smoke_usd": 1,
    "reachability_smoke_scope": "connectivity_only_not_economic_evaluation",
    "native_usdc_economic_evaluation_start_usd": 50,
    "representative_economic_evaluation_usd": 1000,
    "sol_input_representative_evaluation_usd": 1000,
    "sol_input_caveat": (
        "SOL-input routes add a source swap, so compare their full fee-inclusive route economics separately."
    ),
    "evidence_as_of": "2026-09-23",
    "evidence_scope": "Dated Solana native USDC to Base native USDC measurements at USD 50, 250, and 1000.",
    "not_a_minimum": True,
    "not_guaranteed_best": True,
    "always_compare_fresh_at_intended_amount": True,
}

_FEE_COLLECTION_CONST = "only_on_eligible_successful_executor_step"
_DIRECT_SUMMARY_VERSION = "assetfare-direct-route-summary-v1"
_DIRECT_SUMMARY_MODES = frozenset(
    {
        "same_chain_direct",
        "same_chain_direct_composition",
        "cctp_direct_composition",
        "robinhood_paxos_egress_composition",
        "robinhood_across_ingress_composition",
        "polygon_source_cctp",
        "optimism_source_cctp",
    }
)
_DIRECT_SUMMARY_PROVIDERS = frozenset(
    {
        "raydium_clmm",
        "orca_whirlpool",
        "uniswap_v3",
        "circle_cctp",
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
            return f"{from_chain}_source_cctp", False, path
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
                elif from_token == "USDG":
                    add_swap("solana", "USDG", "USDC")
                add_bridge("circle_cctp", "solana", "base", "USDC", "USDC")
                across_source = "base"
            else:
                if from_token == "ETH":
                    add_swap(from_chain, "ETH", "USDC")
                across_source = from_chain
            add_bridge("across_intent_bridge", across_source, "robinhood", "USDC", "USDG")
            if to_token == "ETH":
                add_swap("robinhood", "USDG", "ETH")
            return "robinhood_across_ingress_composition", True, path
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
        if (
            value.get("version") != _DIRECT_SUMMARY_VERSION
            or value.get("route") != expected_route
            or value.get("from") != expected_from
            or value.get("to") != expected_to
            or value.get("mode") not in _DIRECT_SUMMARY_MODES
            or value.get("mode") != expected_mode
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
        for index, (step, raw_step) in enumerate(zip(summary_steps, raw_steps, strict=True)):
            if not isinstance(step, dict) or set(step) != _DIRECT_SUMMARY_STEP_KEYS:
                _fail("assetfare_direct_route_summary_invalid")
            if step.get("index") != index or step.get("provider") not in _DIRECT_SUMMARY_PROVIDERS:
                _fail("assetfare_direct_route_summary_invalid")
            provider = step["provider"]
            external = provider == "across_intent_bridge"
            action = "swap" if provider in _SWAP_PROVIDERS else "bridge"
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
        ):
            _fail("assetfare_direct_route_summary_invalid")
        return dict(value)

    # ---- read-only capabilities ----
    def get_capabilities(self) -> dict[str, Any]:
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        caps = self._request("GET", "/v2/capabilities", None, budget_deadline)
        status = self._request("GET", "/v2/status", None, budget_deadline)
        if caps.get("status") != "capped_public_agent_release" or caps.get("public_api_enabled") is not True:
            _fail("assetfare_safety_boundary_failed")
        # All 76 directed routes are caller-approved and execution-ready.
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
            "evaluation_guidance": evaluation_guidance,
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
        if source_only and not (from_u == "USDC" and to_chain in {"base", "arbitrum"} and to_u == "USDC"):
            raise AssetFareError("assetfare_source_endpoint_invalid")
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
        if component_expected>expected_cost+0.000001 or component_maximum>maximum_cost+0.000001 or small!=(float(cost["maximum_total_cost_percent"])>=1) or (small and not isinstance(cost.get("warning"),str)) or (not small and cost.get("warning") is not None):_fail("assetfare_cost_summary_invalid")
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
            "non_atomic": risk["non_atomic"],
            "quote_id": data["quote_id"],
            "as_of": as_of,
            "ttl_seconds": ttl,
            "source_only": source_only,
            "execution_supported": True,
            "execution_blocker": None,
            "evaluation_guidance": dict(_EVALUATION_GUIDANCE),
            "server_signs_or_submits": False,
        }
