"""AssetFare read-only v2 client (pure; no Dify runtime dependency).

Quote-only. It never authenticates a wallet, opens a session, prepares an
unsigned action, signs, or submits, and it validates every response against the
live AssetFare v2 contract, failing closed on anything that claims the server
will sign or submit. Kept free of the Dify SDK so it can be unit-tested offline.
"""

from __future__ import annotations

import contextlib
import json
import math
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import requests

_ALLOWED_ORIGIN = "https://api.assetfare.dev"
# The requests socket timeout is per connect/read inactivity; on top of it we
# enforce a real total monotonic deadline across the whole call (below), so a
# slow drip stream cannot make an agent hang past the budget.
_SOCKET_TIMEOUT_S = 45.0
_TOTAL_DEADLINE_S = 45.0
_MAX_BYTES = 1_048_576
_MAX_TTL_SECONDS = 86_400
_MAX_FUTURE_SKEW_S = 300  # as_of may not be more than 5 minutes in the future

_CHAINS = ("arbitrum", "base", "robinhood", "solana")
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
    }
)
_EXPECTED_ROUTES = 72
_MIN_USD = 1.0
_MAX_USD = 1000.0


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
        # Injectable clocks (tests supply fakes). `monotonic` drives the total
        # deadline; `utcnow` (timezone-aware UTC) drives the quote freshness check.
        self._monotonic = monotonic or time.monotonic
        self._utcnow = utcnow or (lambda: datetime.now(UTC))

    # ---- transport ----
    def _request(self, method: str, path: str, payload: dict[str, Any] | None, deadline: float) -> dict[str, Any]:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            _fail("assetfare_upstream_unavailable")
        timeout = min(_SOCKET_TIMEOUT_S, remaining)
        try:
            resp = self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                timeout=timeout,
                allow_redirects=False,
                headers={"accept": "application/json", "x-assetfare-channel": "dify"},
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
                    # Enforce the total monotonic budget even under a slow drip
                    # stream whose individual chunks each beat the socket timeout.
                    if self._monotonic() >= deadline:
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
        if not (_MIN_USD <= amount <= _MAX_USD):
            raise AssetFareError("assetfare_amount_out_of_range")
        return amount

    # ---- read-only capabilities ----
    def get_capabilities(self) -> dict[str, Any]:
        deadline = self._monotonic() + _TOTAL_DEADLINE_S
        caps = self._request("GET", "/v2/capabilities", None, deadline)
        status = self._request("GET", "/v2/status", None, deadline)
        if caps.get("status") != "capped_public_agent_release" or caps.get("public_api_enabled") is not True:
            _fail("assetfare_safety_boundary_failed")
        if (
            caps.get("directed_conversion_routes") != _EXPECTED_ROUTES
            or caps.get("unsigned_route_plans_ready") != _EXPECTED_ROUTES
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
        return {
            "status": caps["status"],
            "chains": sorted(_CHAINS),
            "asset_endpoints": [f"{c}:{t}" for (c, t) in sorted(_ENDPOINTS)],
            "directed_conversion_routes": _EXPECTED_ROUTES,
            "unsigned_route_plans_ready": _EXPECTED_ROUTES,
            "amount_usd_min": _MIN_USD,
            "amount_usd_max": _MAX_USD,
            "quote_only": True,
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
        deadline = self._monotonic() + _TOTAL_DEADLINE_S
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
            deadline,
        )
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

        # offer: native and USD ordering expected >= minimum > 0, fee bps, fee steps
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
        fee = offer.get("assetfare_fee_bps")
        if not _int(fee) or fee < 0:
            _fail("assetfare_response_invalid")
        fee_steps = offer.get("fee_collection_steps")
        if not isinstance(fee_steps, list) or not all(_int(s) and s >= 0 for s in fee_steps):
            _fail("assetfare_response_invalid")
        eta = offer.get("estimated_time_seconds")
        if eta is not None and (not _int(eta) or eta < 0):
            _fail("assetfare_response_invalid")

        # risk: non-atomic bool, fresh-quote flag true, sign flags false
        if not isinstance(risk.get("non_atomic"), bool):
            _fail("assetfare_response_invalid")
        if risk.get("fresh_quote_required_each_step") is not True:
            _fail("assetfare_response_invalid")
        self._no_sign(risk)

        # execution: supported, first-unsigned-action flag (bool), verified-receipts flag true
        if execution.get("supported") is not True:
            _fail("assetfare_response_invalid")
        if not isinstance(execution.get("first_unsigned_action_supported"), bool):
            _fail("assetfare_response_invalid")
        if execution.get("future_actions_require_verified_receipts") is not True:
            _fail("assetfare_response_invalid")

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
            "estimated_time_seconds": eta if _int(eta) else None,
            "non_atomic": risk["non_atomic"],
            "quote_id": data["quote_id"],
            "as_of": as_of,
            "ttl_seconds": ttl,
            "execution_supported": True,
            "server_signs_or_submits": False,
        }
