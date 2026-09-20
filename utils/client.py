"""AssetFare non-custodial v2 client (pure; no Dify runtime dependency).

Read-only quote/capabilities discovery PLUS the caller-approved, non-custodial
v2 action surface: a one-shot ``POST /v2/prepare`` and the full ``POST /v2/session``
receipt-driven lifecycle (get / observe-source / observe-output / refresh-action).

This client NEVER signs, NEVER submits to a chain, NEVER receives a private key or
seed, and NEVER auto-calls prepare/session from a quote. Every response is validated
against the live AssetFare v2 contract and fails closed on anything that claims the
server will sign or submit, or that omits/deviates from the caller_action_plan_handoff
contract. The caller-generated session capability token is a SENSITIVE bearer value
(sent only in the ``X-AssetFare-Session-Token`` header, never logged), not a private key.

Kept free of the Dify SDK so it can be unit-tested offline.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import secrets
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
_MAX_TTL_SECONDS = 86_400
_MAX_FUTURE_SKEW_S = 300  # as_of may not be more than 5 minutes in the future

_CHAINS = ("arbitrum", "base", "optimism", "polygon", "robinhood", "solana")
# Source chains that MAY be used in a quote/prepare intent. Destinations exclude
# the source-only chains.
_DESTINATION_CHAINS = frozenset({"solana", "base", "arbitrum", "robinhood"})
# Source-only chains: native USDC may leave them (to Base/Arbitrum USDC) but they
# are never a destination. Their four directional corridors are execution-ready
# through the same caller-approved, non-custodial handoff as every other route.
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
_EXPECTED_ROUTES = 76  # directed quote-discovery routes (6-chain surface)
_EXECUTION_READY_ROUTES = 76
_PHASE_B_BLOCKED_ROUTES = 0
_MIN_USD = 1.0
_MAX_USD = 1000.0

# ---- Caller-operated non-custodial v2 action contract (fixed origin) --------
_PREPARE_URL = "https://api.assetfare.dev/v2/prepare"
_SESSION_URL = "https://api.assetfare.dev/v2/session"
_FEE_COLLECTION_CONST = "only_on_eligible_successful_executor_step"
_SESSION_TOKEN_HEADER = "X-AssetFare-Session-Token"
# EXACT 8-field request contract (caller_approved first). Any deviation fails closed.
_HANDOFF_REQUEST_FIELDS = [
    "caller_approved",
    "from_chain",
    "from_token",
    "to_chain",
    "to_token",
    "amount_usd",
    "wallets",
    "event_signer_public",
]
_HANDOFF_ALLOWED_KEYS = frozenset(
    {
        "kind",
        "url",
        "method",
        "requires_explicit_caller_approval",
        "requires_public_wallet_addresses",
        "request_fields",
        "assetfare_server_signing",
        "assetfare_server_submission",
        "caller_must_verify_sign_and_submit",
        "requires_fresh_requote",
        "automatic_prepare_call_forbidden",
        "options",
        "note",
        "available",
        "blocker",
    }
)

# Public wallet addresses only. Never a private key, seed, or signed transaction.
_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Caller-generated session capability token: >=256-bit CSPRNG, url-safe, 43-128 chars.
_SESSION_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TX_HASH = re.compile(r"^[0-9A-Za-z:_-]{16,128}$")
# Key names that would carry signing/secret material. Any of these anywhere in a
# caller-supplied intent is rejected before a network call.
_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "private_key",
        "privatekey",
        "privkey",
        "secret_key",
        "secretkey",
        "seed",
        "seed_phrase",
        "mnemonic",
        "keypair",
        "secret",
        "signature",
        "signed_transaction",
        "signed_tx",
        "raw_transaction",
        "signed",
        "password",
        "passphrase",
    }
)


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


def _reject_secret_material(value: Any) -> None:
    """Deep-scan a caller intent and reject any private-key / seed / signed material.

    Bounded (node count and depth) so a hostile/huge input cannot hang the scan.
    """
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen = 0
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > 512 or depth > 12:
            _fail("assetfare_secret_material_rejected")
        if isinstance(node, dict):
            for key in node:
                if str(key).lower() in _FORBIDDEN_SECRET_KEYS:
                    _fail("assetfare_secret_material_rejected")
            for child in node.values():
                stack.append((child, depth + 1))
        elif isinstance(node, (list, tuple)):
            for child in node:
                stack.append((child, depth + 1))


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
            for child in node.values():
                stack.append((child, depth + 1))
        elif isinstance(node, (list, tuple)):
            for child in node:
                stack.append((child, depth + 1))


def _is_public_address(value: Any) -> bool:
    return isinstance(value, str) and bool(_EVM_ADDRESS.match(value) or _SOL_ADDRESS.match(value))


def new_session_capability() -> dict[str, Any]:
    """Generate ONE caller-owned session capability token, purely locally.

    Makes NO network call. Returns a >=256-bit CSPRNG url-safe token (43-128 chars)
    marked SENSITIVE. It is a bearer capability, NOT a private key, and cannot move
    funds. The caller stores it and passes it to session_create and every
    session read/observe/refresh. Because the CALLER (not the server) owns the token,
    a lost session_create response can be retried with the same token + idempotency
    key to recover the same session. Never log, telemetry, or persist it in plaintext.
    """
    token = secrets.token_urlsafe(32)  # 32 bytes = 256 bits -> 43 url-safe chars
    if not _SESSION_TOKEN.match(token):  # pragma: no cover - token_urlsafe(32) always 43 chars
        _fail("assetfare_session_token_generation_failed")
    return {
        "session_token": token,
        "token_bits": 256,
        "token_length": len(token),
        "sensitivity": "sensitive_capability",
        "is_private_key": False,
        "network_calls": 0,
        "usage": (
            "Pass this token as session_token to assetfare_session_create and to every "
            "session get/observe/refresh call. It is sent to AssetFare only in the "
            "X-AssetFare-Session-Token header; the server stores only its hash and never "
            "returns it. Treat it like a bearer credential: never log, share, or persist it "
            "in plaintext. It is NOT a private key and cannot move funds. Retrying a lost "
            "session_create with the same token + idempotency_key recovers the same session."
        ),
        "server_signing": False,
        "server_submission": False,
    }


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
    def _validate_caller_handoff(node: Any) -> dict[str, Any]:
        """FAIL-CLOSED passthrough of the upstream caller_action_plan_handoff.

        There is NO local fallback: a missing / null / array / extra-field /
        wrong-field / private-key handoff is a real contract regression and is
        REJECTED. Executable routes must carry the dual-option handoff (POST
        /v2/prepare + full /v2/session lifecycle). Source-only routes must carry
        the blocked form: available=false, blocker, and NO prepare url / options.
        """
        if not isinstance(node, dict):
            _fail("assetfare_handoff_missing")
        if node.get("kind") != "caller_operated_rest_prepare":
            _fail("assetfare_handoff_invalid")
        if node.get("method") != "POST":
            _fail("assetfare_handoff_invalid")
        fields = node.get("request_fields")
        if not isinstance(fields, list) or list(fields) != _HANDOFF_REQUEST_FIELDS:
            _fail("assetfare_handoff_request_fields_invalid")
        if node.get("requires_explicit_caller_approval") is not True:
            _fail("assetfare_handoff_invalid")
        if node.get("requires_public_wallet_addresses") is not True:
            _fail("assetfare_handoff_invalid")
        if node.get("assetfare_server_signing") is not False or node.get("assetfare_server_submission") is not False:
            _fail("assetfare_safety_boundary_failed")
        if node.get("caller_must_verify_sign_and_submit") is not True:
            _fail("assetfare_handoff_invalid")
        if node.get("requires_fresh_requote") is not True:
            _fail("assetfare_handoff_invalid")
        if node.get("automatic_prepare_call_forbidden") is not True:
            _fail("assetfare_handoff_invalid")
        note = node.get("note")
        if not isinstance(note, str) or not note:
            _fail("assetfare_handoff_invalid")
        for key in node:
            if key not in _HANDOFF_ALLOWED_KEYS:
                _fail("assetfare_handoff_extra_field")

        if node.get("available") is not True:
            _fail("assetfare_handoff_invalid")
        if node.get("url") != _PREPARE_URL:
            _fail("assetfare_handoff_invalid")
        options = node.get("options")
        if not isinstance(options, list) or len(options) != 2:
            _fail("assetfare_handoff_options_invalid")
        prepare_option, session_option = options
        if (
            not isinstance(prepare_option, dict)
            or prepare_option.get("kind") != "one_shot_first_unsigned_bundle"
            or prepare_option.get("method") != "POST"
            or prepare_option.get("url") != _PREPARE_URL
            or prepare_option.get("requires_explicit_caller_approval") is not True
            or prepare_option.get("requires_public_wallet_addresses") is not True
            or prepare_option.get("assetfare_never_signs_submits_or_auto_calls") is not True
        ):
            _fail("assetfare_handoff_prepare_option_invalid")
        if (
            not isinstance(session_option, dict)
            or session_option.get("kind") != "caller_approved_full_workflow_session"
            or session_option.get("method") != "POST"
            or session_option.get("url") != _SESSION_URL
            or session_option.get("requires_explicit_caller_approval") is not True
            or session_option.get("requires_public_wallet_addresses") is not True
            or session_option.get("assetfare_never_signs_submits_or_auto_calls") is not True
        ):
            _fail("assetfare_handoff_session_option_invalid")
        lifecycle = session_option.get("lifecycle_urls")
        if not isinstance(lifecycle, dict):
            _fail("assetfare_handoff_session_lifecycle_invalid")
        expected_lifecycle = {
            "create": _SESSION_URL,
            "read": f"{_SESSION_URL}/{{session_id}}",
            "observe_source": f"{_SESSION_URL}/{{session_id}}/observe-source",
            "observe_output": f"{_SESSION_URL}/{{session_id}}/observe-output",
            "refresh_action": f"{_SESSION_URL}/{{session_id}}/refresh-action",
        }
        for name, url in expected_lifecycle.items():
            entry = lifecycle.get(name)
            if not isinstance(entry, dict) or entry.get("url") != url:
                _fail("assetfare_handoff_session_lifecycle_invalid")
        return dict(node)

    @staticmethod
    def _validate_caller_handoff_v2(node: Any) -> dict[str, Any]:
        """FAIL-CLOSED EXACT validation of the optional caller_action_plan_handoff_v2 sibling. Advisory machine
        contract: per-kind EXACT option key sets (missing AND extra rejected), nonempty option notes, and exact
        session lifecycle. v1 stays the unchanged old shape; this is validated only when the sibling is present."""
        if not isinstance(node, dict):
            _fail("assetfare_handoff_v2_invalid")
        if node.get("request_fields") != _HANDOFF_REQUEST_FIELDS:
            _fail("assetfare_handoff_v2_request_fields_invalid")
        scalars = {"schema_version": 2, "kind": "caller_operated_rest_prepare", "method": "POST", "requires_explicit_caller_approval": True, "requires_public_wallet_addresses": True, "assetfare_server_signing": False, "assetfare_server_submission": False, "caller_must_verify_sign_and_submit": True, "requires_fresh_requote": True, "automatic_prepare_call_forbidden": True, "selection": "choose_exactly_one", "mutually_exclusive": True, "do_not_call_both": True, "selection_before_signing": True, "once_any_action_submitted_do_not_start_other_mode": True, "enforcement": "advisory_caller_side", "available": True, "url": _PREPARE_URL}
        for key, val in scalars.items():
            if node.get(key) != val:
                _fail("assetfare_handoff_v2_invalid")
        if not isinstance(node.get("note"), str) or not node["note"]:
            _fail("assetfare_handoff_v2_invalid")
        top_required = {"kind", "url", "method", "request_fields", "assetfare_server_signing", "assetfare_server_submission", "caller_must_verify_sign_and_submit", "requires_fresh_requote", "automatic_prepare_call_forbidden", "requires_explicit_caller_approval", "requires_public_wallet_addresses", "schema_version", "selection", "mutually_exclusive", "do_not_call_both", "selection_before_signing", "once_any_action_submitted_do_not_start_other_mode", "enforcement", "options", "note", "available"}
        # v2 is ALWAYS the available=true machine contract: EXACT key set (no blocker) — reject missing AND extra.
        if set(node) != top_required:
            _fail("assetfare_handoff_v2_extra_field")
        options = node.get("options")
        if not isinstance(options, list) or len(options) != 2:
            _fail("assetfare_handoff_v2_options_invalid")
        prepare_option, session_option = options
        prep_keys = {"kind", "method", "url", "requires_explicit_caller_approval", "requires_public_wallet_addresses", "assetfare_never_signs_submits_or_auto_calls", "preview_or_manual_first_action_only", "not_a_session", "do_not_start_session_after_submission", "note"}
        if not isinstance(prepare_option, dict) or set(prepare_option) != prep_keys:
            _fail("assetfare_handoff_v2_prepare_option_invalid")
        if (prepare_option.get("kind") != "one_shot_first_unsigned_bundle" or prepare_option.get("method") != "POST" or prepare_option.get("url") != _PREPARE_URL or prepare_option.get("requires_explicit_caller_approval") is not True or prepare_option.get("requires_public_wallet_addresses") is not True or prepare_option.get("assetfare_never_signs_submits_or_auto_calls") is not True or prepare_option.get("preview_or_manual_first_action_only") is not True or prepare_option.get("not_a_session") is not True or prepare_option.get("do_not_start_session_after_submission") is not True or not isinstance(prepare_option.get("note"), str) or not prepare_option["note"]):
            _fail("assetfare_handoff_v2_prepare_option_invalid")
        sess_keys = {"kind", "method", "url", "lifecycle_urls", "requires_explicit_caller_approval", "requires_public_wallet_addresses", "assetfare_never_signs_submits_or_auto_calls", "recommended_for_multistep", "note"}
        if not isinstance(session_option, dict) or set(session_option) != sess_keys:
            _fail("assetfare_handoff_v2_session_option_invalid")
        if (session_option.get("kind") != "caller_approved_full_workflow_session" or session_option.get("method") != "POST" or session_option.get("url") != _SESSION_URL or session_option.get("requires_explicit_caller_approval") is not True or session_option.get("requires_public_wallet_addresses") is not True or session_option.get("assetfare_never_signs_submits_or_auto_calls") is not True or session_option.get("recommended_for_multistep") is not True or not isinstance(session_option.get("note"), str) or not session_option["note"]):
            _fail("assetfare_handoff_v2_session_option_invalid")
        lifecycle = session_option.get("lifecycle_urls")
        expected_lifecycle = {"create": ("POST", _SESSION_URL), "read": ("GET", f"{_SESSION_URL}/{{session_id}}"), "observe_source": ("POST", f"{_SESSION_URL}/{{session_id}}/observe-source"), "observe_output": ("POST", f"{_SESSION_URL}/{{session_id}}/observe-output"), "refresh_action": ("POST", f"{_SESSION_URL}/{{session_id}}/refresh-action")}
        if not isinstance(lifecycle, dict) or set(lifecycle) != set(expected_lifecycle):
            _fail("assetfare_handoff_v2_session_lifecycle_invalid")
        for name, (method, url) in expected_lifecycle.items():
            entry = lifecycle.get(name)
            if not isinstance(entry, dict) or set(entry) != {"method", "url"} or entry.get("method") != method or entry.get("url") != url:
                _fail("assetfare_handoff_v2_session_lifecycle_invalid")
        return dict(node)

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
        if not (_MIN_USD <= amount <= _MAX_USD):
            raise AssetFareError("assetfare_amount_out_of_range")
        return amount

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
        return {
            "status": caps["status"],
            "chains": sorted(_CHAINS),
            "asset_endpoints": [f"{c}:{t}" for (c, t) in sorted(_ENDPOINTS)],
            "directed_conversion_routes": _EXPECTED_ROUTES,
            "unsigned_route_plans_ready": _EXPECTED_ROUTES,
            "execution_ready_routes": _EXECUTION_READY_ROUTES,
            "phase_b_blocked_routes": _PHASE_B_BLOCKED_ROUTES,
            "source_only_asset_endpoints": sorted(f"{c}:{t}" for (c, t) in _SOURCE_ONLY_ENDPOINTS),
            "source_only_routes": sorted(_SOURCE_ONLY_ROUTES),
            "blocked_source_only_routes": [],
            "amount_usd_min": _MIN_USD,
            "amount_usd_max": _MAX_USD,
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

        # risk: non-atomic bool, fresh-quote flag true, sign flags false
        if not isinstance(risk.get("non_atomic"), bool):
            _fail("assetfare_response_invalid")
        if risk.get("fresh_quote_required_each_step") is not True:
            _fail("assetfare_response_invalid")
        self._no_sign(risk)

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

        # FAIL-CLOSED passthrough of the upstream caller_action_plan_handoff. No
        # local fallback: a missing/malformed handoff is a real contract regression.
        caller_action_plan_handoff = self._validate_caller_handoff(data.get("caller_action_plan_handoff"))
        # Transition-safe v2 sibling: v1 is always validated; the sibling and its schema_version are strictly coupled
        # (both present or both absent). A rollback Core (v1-only) still quotes.
        # Coupling by KEY PRESENCE (not value): both keys present or both absent. A present-but-null/array sibling is
        # rejected by the validator (isinstance dict check), not treated as absent.
        has_version = "handoff_schema_version" in data
        has_sibling = "caller_action_plan_handoff_v2" in data
        if has_version != has_sibling:
            _fail("assetfare_handoff_schema_version_invalid")
        caller_action_plan_handoff_v2 = None
        if has_sibling:
            if data.get("handoff_schema_version") != 2:
                _fail("assetfare_handoff_schema_version_invalid")
            caller_action_plan_handoff_v2 = self._validate_caller_handoff_v2(data.get("caller_action_plan_handoff_v2"))

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
            "non_atomic": risk["non_atomic"],
            "quote_id": data["quote_id"],
            "as_of": as_of,
            "ttl_seconds": ttl,
            "source_only": source_only,
            "execution_supported": True,
            "execution_blocker": None,
            "server_signs_or_submits": False,
            "caller_action_plan_handoff": caller_action_plan_handoff,
            "caller_action_plan_handoff_v2": caller_action_plan_handoff_v2,
            "handoff_schema_version": 2 if caller_action_plan_handoff_v2 is not None else None,
        }

    # ---- caller-approved action intent helpers ----
    def _action_intent(
        self,
        caller_approved: Any,
        from_chain: str,
        from_token: str,
        to_chain: str,
        to_token: str,
        amount_usd: Any,
        wallets: Any,
        event_signer_public: Any,
    ) -> dict[str, Any]:
        """Validate a caller-approved prepare/session intent BEFORE any network call.

        Enforces the literal caller_approved gate, the route endpoints, the public
        wallet map (no private keys/seeds/signed material), the conditional
        event_signer_public, and directional source-only corridor constraints. Fails closed.
        """
        # Machine approval gate: literal True only (reject false/missing/string/number).
        if caller_approved is not True:
            _fail("assetfare_caller_approval_required")
        amount = self._amount(amount_usd)
        from_u = self._endpoint(from_chain, from_token, "source")
        to_u = self._endpoint(to_chain, to_token, "destination")
        if (from_chain, from_u) == (to_chain, to_u):
            _fail("assetfare_identity_route_rejected")
        if to_chain in _SOURCE_ONLY_CHAINS:
            _fail("assetfare_destination_endpoint_invalid")
        if from_chain in _SOURCE_ONLY_CHAINS and not (
            from_u == "USDC" and to_chain in {"base", "arbitrum"} and to_u == "USDC"
        ):
            _fail("assetfare_source_endpoint_invalid")
        # Reject any private key / seed / signed material anywhere in the intent.
        _reject_secret_material(
            {
                "wallets": wallets,
                "event_signer_public": event_signer_public,
                "from_chain": from_chain,
                "to_chain": to_chain,
            }
        )
        # Exact public wallet map: 1-6 entries, keys are source chains, values are
        # public addresses only. Never a private key or seed.
        if not isinstance(wallets, dict) or not (1 <= len(wallets) <= 6):
            _fail("assetfare_wallets_invalid")
        for chain, address in wallets.items():
            if chain not in _CHAINS:
                _fail("assetfare_wallets_invalid")
            if not _is_public_address(address):
                _fail("assetfare_wallet_not_public_address")
        # The route's source and destination chains must each have a wallet.
        if from_chain not in wallets or to_chain not in wallets:
            _fail("assetfare_wallets_route_mismatch")
        body: dict[str, Any] = {
            "caller_approved": True,
            "from_chain": from_chain,
            "from_token": from_u,
            "to_chain": to_chain,
            "to_token": to_u,
            "amount_usd": amount,
            "wallets": dict(wallets),
        }
        # event_signer_public is conditional (Solana CCTP routes). If supplied it must
        # be a public address (never a private key); pass it through when present.
        if event_signer_public is not None:
            if not _is_public_address(event_signer_public):
                _fail("assetfare_event_signer_not_public_address")
            body["event_signer_public"] = event_signer_public
        return body

    @staticmethod
    def _validate_bundle(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate an upstream bounded FIRST unsigned action bundle (prepare / create).

        Fail-closed on any server signing/submission claim. The bundle must carry an
        unsigned_action and explicitly assert it is neither signed nor submitted.
        """
        _reject_signing_claims(payload)
        if (
            payload.get("server_signing") is not False
            or payload.get("server_submission") is not False
            or payload.get("signed") is not False
            or payload.get("submitted") is not False
        ):
            _fail("assetfare_bundle_unsafe")
        if not isinstance(payload.get("unsigned_action"), dict):
            _fail("assetfare_bundle_missing_action")
        return payload

    @staticmethod
    def _validate_session(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a v2 session workflow-state response. It must never assert signing."""
        _reject_signing_claims(payload)
        if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
            _fail("assetfare_session_invalid")
        if (
            payload.get("server_signing") is not False
            or payload.get("server_submission") is not False
            or payload.get("signed") is not False
            or payload.get("submitted") is not False
        ):
            _fail("assetfare_session_unsafe")
        return payload

    @staticmethod
    def _session_token(session_token: Any) -> str:
        if not isinstance(session_token, str) or not _SESSION_TOKEN.match(session_token):
            _fail("assetfare_session_token_invalid")
        return session_token

    @staticmethod
    def _idempotency_key(idempotency_key: Any) -> str:
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.match(idempotency_key):
            _fail("assetfare_idempotency_key_invalid")
        return idempotency_key

    @staticmethod
    def _session_id(session_id: Any) -> str:
        if not isinstance(session_id, str) or not _UUID_RE.match(session_id):
            _fail("assetfare_session_id_invalid")
        return session_id

    # ---- caller-approved one-shot prepare (POST /v2/prepare) ----
    def prepare(
        self,
        caller_approved: Any,
        from_chain: str,
        from_token: str,
        to_chain: str,
        to_token: str,
        amount_usd: Any,
        wallets: Any,
        event_signer_public: Any = None,
    ) -> dict[str, Any]:
        """Explicit caller-approved one-shot POST /v2/prepare.

        Returns the fresh re-quoted bounded FIRST unsigned action bundle. NEVER
        auto-called from a quote; rejects private key / seed / signed transaction
        material. AssetFare never signs or submits.
        """
        body = self._action_intent(
            caller_approved, from_chain, from_token, to_chain, to_token, amount_usd, wallets, event_signer_public
        )
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request("POST", "/v2/prepare", body, budget_deadline)
        return self._validate_bundle(data)

    # ---- caller-approved full session lifecycle (POST /v2/session ...) ----
    def session_create(
        self,
        caller_approved: Any,
        from_chain: str,
        from_token: str,
        to_chain: str,
        to_token: str,
        amount_usd: Any,
        wallets: Any,
        session_token: Any,
        idempotency_key: Any,
        event_signer_public: Any = None,
    ) -> dict[str, Any]:
        """Explicit caller-approved idempotent POST /v2/session create.

        The CALLER-GENERATED session capability token (from new_session_capability)
        is REQUIRED input and is sent only in the X-AssetFare-Session-Token header;
        this method does NOT generate it. Retrying with the same token + idempotency
        key recovers the same session (lost-response crash recovery). Never auto-chains,
        signs, or submits.
        """
        body = self._action_intent(
            caller_approved, from_chain, from_token, to_chain, to_token, amount_usd, wallets, event_signer_public
        )
        token = self._session_token(session_token)
        body["idempotency_key"] = self._idempotency_key(idempotency_key)
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request(
            "POST", "/v2/session", body, budget_deadline, extra_headers={_SESSION_TOKEN_HEADER: token}
        )
        return self._validate_session(data)

    def session_get(self, session_token: Any, session_id: Any) -> dict[str, Any]:
        """Read a v2 session's current workflow state and current unsigned action."""
        token = self._session_token(session_token)
        sid = self._session_id(session_id)
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request(
            "GET", f"/v2/session/{sid}", None, budget_deadline, extra_headers={_SESSION_TOKEN_HEADER: token}
        )
        return self._validate_session(data)

    def observe_source(
        self, session_token: Any, session_id: Any, idempotency_key: Any, transaction_hashes: Any
    ) -> dict[str, Any]:
        """Observe the caller's ALREADY-submitted source tx hashes and advance the workflow.

        Only caller-submitted transaction hashes are observed; this never submits a
        transaction and never auto-chains.
        """
        token = self._session_token(session_token)
        sid = self._session_id(session_id)
        key = self._idempotency_key(idempotency_key)
        if (
            not isinstance(transaction_hashes, list)
            or not (1 <= len(transaction_hashes) <= 8)
            or not all(isinstance(h, str) and _TX_HASH.match(h) for h in transaction_hashes)
        ):
            _fail("assetfare_transaction_hashes_invalid")
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request(
            "POST",
            f"/v2/session/{sid}/observe-source",
            {"idempotency_key": key, "transaction_hashes": list(transaction_hashes)},
            budget_deadline,
            extra_headers={_SESSION_TOKEN_HEADER: token},
        )
        return self._validate_session(data)

    def observe_output(
        self, session_token: Any, session_id: Any, idempotency_key: Any, transaction_hash: Any = None
    ) -> dict[str, Any]:
        """Observe the caller's ALREADY-produced destination/bridge output and advance."""
        token = self._session_token(session_token)
        sid = self._session_id(session_id)
        key = self._idempotency_key(idempotency_key)
        body: dict[str, Any] = {"idempotency_key": key}
        if transaction_hash is not None:
            if not isinstance(transaction_hash, str) or not _TX_HASH.match(transaction_hash):
                _fail("assetfare_transaction_hash_invalid")
            body["transaction_hash"] = transaction_hash
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request(
            "POST",
            f"/v2/session/{sid}/observe-output",
            body,
            budget_deadline,
            extra_headers={_SESSION_TOKEN_HEADER: token},
        )
        return self._validate_session(data)

    def refresh_action(self, session_token: Any, session_id: Any, idempotency_key: Any) -> dict[str, Any]:
        """Replace an expired, unsubmitted session action with a fresh quote-bound one."""
        token = self._session_token(session_token)
        sid = self._session_id(session_id)
        key = self._idempotency_key(idempotency_key)
        budget_deadline = self._monotonic() + _STALE_BUDGET_S
        data = self._request(
            "POST",
            f"/v2/session/{sid}/refresh-action",
            {"idempotency_key": key},
            budget_deadline,
            extra_headers={_SESSION_TOKEN_HEADER: token},
        )
        return self._validate_session(data)
