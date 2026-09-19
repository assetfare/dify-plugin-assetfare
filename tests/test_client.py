import json
import os
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.client import AssetFareClient, AssetFareError, new_session_capability

# Fixed "now" so the fixed-timestamp quote fixture (as_of 2026-09-17T00:00:00Z,
# ttl 30) is fresh (10s in). Tests inject this so they do not depend on wall time.
FIXED_NOW = datetime(2026, 9, 17, 0, 0, 10, tzinfo=UTC)

PREPARE_URL = "https://api.assetfare.dev/v2/prepare"
SESSION_URL = "https://api.assetfare.dev/v2/session"
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
EXECUTION_NOT_READY = "execution_not_ready_phase_b"

EVM = "0x" + "a" * 40
SOL = "So11111111111111111111111111111111111111112"


def af(**kwargs):
    kwargs.setdefault("utcnow", lambda: FIXED_NOW)
    return AssetFareClient(**kwargs)


ENDPOINTS = [
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
]

# Chains that may only be a source (native USDC -> base/arbitrum USDC), never a
# destination: polygon (1bp) and optimism (0bp).
SOURCE_ONLY_CHAINS = {"polygon", "optimism"}
DESTINATION_ENDPOINTS = [(c, t) for (c, t) in ENDPOINTS if c not in SOURCE_ONLY_CHAINS]


def all_routes():
    """Generate the 76 directed quote-discovery routes (72 executable + 4 source-only)."""
    routes = []
    for fc, ft in ENDPOINTS:
        for tc, tt in DESTINATION_ENDPOINTS:
            if (fc, ft) == (tc, tt):
                continue
            if fc in SOURCE_ONLY_CHAINS and not (ft == "USDC" and tc in {"base", "arbitrum"} and tt == "USDC"):
                continue
            routes.append((fc, ft, tc, tt))
    return routes


def valid_caps():
    return {
        "status": "capped_public_agent_release",
        "public_api_enabled": True,
        "directed_conversion_routes": 76,
        "unsigned_route_plans_ready": 76,
        "execution_ready_routes": 72,
        "phase_b_blocked_routes": 4,
        "server_signing": False,
        "server_submission": False,
        "chains": ["arbitrum", "base", "optimism", "polygon", "robinhood", "solana"],
        "asset_endpoints": [{"chain": c, "token": t} for c, t in ENDPOINTS],
        "source_only_asset_endpoints": [
            {"chain": "polygon", "token": "USDC"},
            {"chain": "optimism", "token": "USDC"},
        ],
        "source_only_routes": [
            "polygon:USDC->base:USDC",
            "polygon:USDC->arbitrum:USDC",
            "optimism:USDC->base:USDC",
            "optimism:USDC->arbitrum:USDC",
        ],
        "blocked_source_only_routes": [
            "polygon:USDC->base:USDC",
            "polygon:USDC->arbitrum:USDC",
            "optimism:USDC->base:USDC",
            "optimism:USDC->arbitrum:USDC",
        ],
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


def blocked_handoff():
    return {
        "kind": "caller_operated_rest_prepare",
        "method": "POST",
        "requires_explicit_caller_approval": True,
        "requires_public_wallet_addresses": True,
        "request_fields": list(REQUEST_FIELDS),
        "assetfare_server_signing": False,
        "assetfare_server_submission": False,
        "caller_must_verify_sign_and_submit": True,
        "requires_fresh_requote": True,
        "automatic_prepare_call_forbidden": True,
        "note": "Quote/action-plan discovery only: source-only, no REST prepare endpoint offered.",
        "available": False,
        "blocker": EXECUTION_NOT_READY,
    }


def valid_quote(fc, ft, tc, tt, amount, *, source_only=False, fee=1):
    steps = [{"kind": "burn"}]
    if source_only:
        offer_fee = fee
        collectible = False
        fee_steps = [0] if fee == 1 else []
        execution = {
            "supported": False,
            "first_unsigned_action_supported": False,
            "future_actions_require_verified_receipts": True,
            "blocker": EXECUTION_NOT_READY,
        }
        handoff = blocked_handoff()
    else:
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
    return {
        "quote_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
        "status": "capped_public_agent_release",
        "as_of": "2026-09-17T00:00:00Z",
        "ttl_seconds": 30,
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
            "fee_blocker": EXECUTION_NOT_READY if source_only else None,
            "fee_collection_steps": fee_steps,
            "fee_collection": "only_on_eligible_successful_executor_step",
            "estimated_time_seconds": 23,
        },
        "route": {
            "route": f"{fc}:{ft}->{tc}:{tt}",
            "steps": steps,
            "quote_latency_ms": 120,
            "server_signing": False,
            "server_submission": False,
        },
        "risk": {
            "non_atomic": True,
            "fresh_quote_required_each_step": True,
            "server_signing": False,
            "server_submission": False,
        },
        "execution": execution,
        "caller_action_plan_handoff": handoff,
    }


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
        return "00000000-0000-4000-8000-{:012d}".format(self._n)

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
            if payload.get("from_chain") in SOURCE_ONLY_CHAINS:
                return _Resp(409, {"error": EXECUTION_NOT_READY})
            return _Resp(200, self._bundle())

        if path == "/v2/session" and method == "POST":
            if payload.get("caller_approved") is not True:
                return _Resp(400, {"error": "caller_approval_required"})
            if payload.get("from_chain") in SOURCE_ONLY_CHAINS:
                return _Resp(409, {"error": EXECUTION_NOT_READY})
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


# ---- capabilities (6-chain surface: 11 endpoints, 76 routes, 72 executable, 4 blocked) ----
def test_capabilities_ok():
    caps = client({"/v2/capabilities": valid_caps(), "/v2/status": valid_status()}).get_capabilities()
    assert caps["directed_conversion_routes"] == 76
    assert caps["unsigned_route_plans_ready"] == 76
    assert caps["execution_ready_routes"] == 72
    assert caps["phase_b_blocked_routes"] == 4
    assert len(caps["asset_endpoints"]) == 11
    assert caps["server_signs_or_submits"] is False
    assert sorted(caps["source_only_asset_endpoints"]) == ["optimism:USDC", "polygon:USDC"]
    assert set(caps["source_only_routes"]) == {
        "polygon:USDC->base:USDC",
        "polygon:USDC->arbitrum:USDC",
        "optimism:USDC->base:USDC",
        "optimism:USDC->arbitrum:USDC",
    }
    assert set(caps["blocked_source_only_routes"]) == set(caps["source_only_routes"])


@pytest.mark.parametrize(
    "mut",
    [
        lambda c: c.update(directed_conversion_routes=75),
        lambda c: c.update(execution_ready_routes=76),  # must be 72
        lambda c: c.update(phase_b_blocked_routes=0),  # must be 4
        lambda c: c.update(server_signing=True),
        lambda c: c.pop("blocked_source_only_routes"),
    ],
)
def test_capabilities_boundary_fail(mut):
    caps = valid_caps()
    mut(caps)
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


# ---- quote happy path + fee surfaced ----
def test_quote_exact_body_and_bounded_return():
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    c = client({"/v2/quote": q})
    out = c.get_quote("solana", "SOL", "base", "ETH", 250)
    assert out["from"] == "solana:SOL" and out["to"] == "base:ETH"
    assert out["assetfare_fee_bps"] == 1
    assert out["fee_modeled_bps"] == 1
    assert out["fee_collectible_now"] is True
    assert out["fee_collection"] == "only_on_eligible_successful_executor_step"
    assert out["fee_collection_steps"] == [0]
    assert out["execution_supported"] is True
    assert out["source_only"] is False
    assert out["server_signs_or_submits"] is False


def test_quote_surfaces_executable_dual_option_handoff():
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    out = client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)
    handoff = out["caller_action_plan_handoff"]
    assert handoff["available"] is True
    assert handoff["url"] == PREPARE_URL
    assert handoff["request_fields"] == REQUEST_FIELDS
    assert handoff["request_fields"][0] == "caller_approved"
    assert len(handoff["options"]) == 2
    assert handoff["options"][0]["kind"] == "one_shot_first_unsigned_bundle"
    assert handoff["options"][1]["kind"] == "caller_approved_full_workflow_session"
    assert handoff["requires_fresh_requote"] is True
    assert handoff["automatic_prepare_call_forbidden"] is True


# ---- FAIL-CLOSED handoff (no local fallback) ----
@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q.pop("caller_action_plan_handoff"),  # missing
        lambda q: q.update(caller_action_plan_handoff=None),  # null
        lambda q: q.update(caller_action_plan_handoff=[]),  # array
        lambda q: q.update(caller_action_plan_handoff="x"),  # string
    ],
)
def test_quote_handoff_missing_or_wrong_type_rejected(mut):
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    mut(q)
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)


@pytest.mark.parametrize(
    "mut",
    [
        lambda h: h.update(private_key="0xdead"),  # private-key material -> extra field
        lambda h: h.update(evil="x"),  # extra field
        lambda h: h.update(request_fields=REQUEST_FIELDS[1:]),  # short
        lambda h: h.update(request_fields=list(reversed(REQUEST_FIELDS))),  # reordered
        lambda h: h.update(request_fields=["from_chain", "from_token", "to_chain", "to_token", "amount_usd", "wallets", "event_signer_public"]),  # old 7-field
        lambda h: h.update(assetfare_server_signing=True),  # signing claim
        lambda h: h.update(requires_fresh_requote=False),
        lambda h: h.update(automatic_prepare_call_forbidden=False),
        lambda h: h.update(options=[prepare_option()]),  # only one option
        lambda h: h.pop("url"),  # executable must carry url
    ],
)
def test_quote_handoff_malformed_rejected(mut):
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    mut(q["caller_action_plan_handoff"])
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)


# ---- source-only quote: available:false handled, no prepare offered ----
def test_source_only_quote_available_false():
    q = valid_quote("polygon", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    out = client({"/v2/quote": q}).get_quote("polygon", "USDC", "base", "USDC", 100)
    assert out["source_only"] is True
    assert out["execution_supported"] is False
    assert out["execution_blocker"] == EXECUTION_NOT_READY
    assert out["fee_collectible_now"] is False
    handoff = out["caller_action_plan_handoff"]
    assert handoff["available"] is False
    assert handoff["blocker"] == EXECUTION_NOT_READY
    assert "url" not in handoff and "options" not in handoff


def test_optimism_source_only_zero_fee_quote_ok():
    q = valid_quote("optimism", "USDC", "arbitrum", "USDC", 100, source_only=True, fee=0)
    out = client({"/v2/quote": q}).get_quote("optimism", "USDC", "arbitrum", "USDC", 100)
    assert out["assetfare_fee_bps"] == 0
    assert out["fee_collection_steps"] == []
    assert out["fee_collectible_now"] is False


def test_source_only_quote_with_prepare_url_rejected():
    # A source-only route that (wrongly) carries an executable handoff must be rejected.
    q = valid_quote("polygon", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    q["caller_action_plan_handoff"] = executable_handoff()
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("polygon", "USDC", "base", "USDC", 100)


def test_executable_quote_with_blocked_handoff_rejected():
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    q["caller_action_plan_handoff"] = blocked_handoff()  # available:false on an executable route
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)


# ---- fee EXACTLY {0,1} ----
@pytest.mark.parametrize(
    "mut",
    [
        lambda o: o.update(assetfare_fee_bps=8, fee_modeled_bps=8),  # 8bp out of range
        lambda o: o.update(assetfare_fee_bps=2, fee_modeled_bps=2),  # >1
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
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    mut(q["offer"])
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)


def test_source_only_fee_collectible_now_true_rejected():
    q = valid_quote("polygon", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    q["offer"]["fee_collectible_now"] = True  # cannot be collectible while blocked
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("polygon", "USDC", "base", "USDC", 100)


# ---- route enumeration ----
def test_all_76_routes_generated_72_executable():
    routes = all_routes()
    assert len(routes) == 76
    executable = [r for r in routes if r[0] not in SOURCE_ONLY_CHAINS]
    source_only = [r for r in routes if r[0] in SOURCE_ONLY_CHAINS]
    assert len(executable) == 72
    assert len(source_only) == 4


@pytest.mark.parametrize("dest", ["polygon", "optimism"])
def test_source_only_destination_rejected(dest):
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "USDC", dest, "USDC", 100)


# ---- amount / endpoint hostiles ----
@pytest.mark.parametrize("amt", [0.99, 1000.01, "5", True, float("nan"), None])
def test_amount_rejected(amt):
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "SOL", "base", "ETH", amt)


def test_unsupported_endpoint_rejected():
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "DOGE", "base", "ETH", 100)


# ---- transport / safety ----
def test_non_json_rejected():
    c = af(session=_Session({"/v2/quote": _Resp(body={"x": 1}, content_type="text/html")}))
    with pytest.raises(AssetFareError):
        c.get_quote("solana", "SOL", "base", "ETH", 100)


def test_non_2xx_rejected():
    c = af(session=_Session({"/v2/quote": _Resp(status_code=400, body={"error": "x"})}))
    with pytest.raises(AssetFareError):
        c.get_quote("solana", "SOL", "base", "ETH", 100)


def test_quote_signing_claim_rejected():
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    q["route"]["server_signing"] = True
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)


# ---- new_session_capability (local-only token, no network) ----
def test_new_session_capability_local_only():
    cap = new_session_capability()
    assert cap["token_bits"] == 256
    assert 43 <= cap["token_length"] <= 128
    assert cap["is_private_key"] is False
    assert cap["network_calls"] == 0
    assert cap["server_signing"] is False
    import re

    assert re.match(r"^[A-Za-z0-9_-]{43,128}$", cap["session_token"])
    # Two calls produce distinct tokens.
    assert cap["session_token"] != new_session_capability()["session_token"]


# ---- prepare: caller_approved gate (build BEFORE any network) ----
@pytest.mark.parametrize("approved", [False, None, "true", 1, 0, "True"])
def test_prepare_caller_approved_gate(approved):
    # client({}) raises AssertionError if a network call is attempted -> proves the
    # gate fails closed before any request.
    with pytest.raises(AssetFareError):
        client({}).prepare(approved, "solana", "SOL", "base", "ETH", 100, wallets_for("solana", "base"))


def test_prepare_source_only_rejected_before_network():
    with pytest.raises(AssetFareError):
        client({}).prepare(True, "polygon", "USDC", "base", "USDC", 100, wallets_for("polygon", "base"))


@pytest.mark.parametrize(
    "wallets",
    [
        {"solana": SOL, "base": EVM, "private_key": "0xdead"},  # secret material
        {"solana": SOL, "base": "not-an-address"},  # non-public value
        {"solana": SOL},  # missing destination chain wallet
        {"base": EVM},  # missing source chain wallet
        "0xdeadbeef",  # not a map
        {},  # empty
    ],
)
def test_prepare_wallet_hostiles_before_network(wallets):
    with pytest.raises(AssetFareError):
        client({}).prepare(True, "solana", "SOL", "base", "ETH", 100, wallets)


def test_prepare_private_key_event_signer_rejected():
    with pytest.raises(AssetFareError):
        client({}).prepare(
            True,
            "solana",
            "SOL",
            "base",
            "ETH",
            100,
            wallets_for("solana", "base"),
            event_signer_public="not-a-public-key",
        )


def test_prepare_happy_path_returns_unsigned_bundle():
    c, up = stateful()
    bundle = c.prepare(True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"))
    assert bundle["unsigned_action"]["transaction"] == "0xUNSIGNED"
    assert bundle["signed"] is False and bundle["submitted"] is False
    assert bundle["server_signing"] is False and bundle["server_submission"] is False
    assert ("POST", "/v2/prepare") in up.calls


def test_prepare_bundle_unsafe_rejected():
    class _Up(_Upstream):
        def _bundle(self):
            b = super()._bundle()
            b["signed"] = True
            return b

    up = _Up()
    c = af(session=_StatefulSession(up))
    with pytest.raises(AssetFareError):
        c.prepare(True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"))


# ---- session lifecycle happy path ----
def test_session_lifecycle_happy_path():
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    created = c.session_create(
        True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap, "create-0001"
    )
    sid = created["session_id"]
    assert created["status"] == "action_ready"
    got = c.session_get(cap, sid)
    assert got["session_id"] == sid
    src = c.observe_source(cap, sid, "src-00001", ["0x" + "c" * 40])
    assert src["session_id"] == sid
    out = c.observe_output(cap, sid, "out-00001")
    assert out["status"] == "complete"


# ---- session token hostiles ----
def test_session_get_missing_token_rejected_before_network():
    c, up = stateful()
    with pytest.raises(AssetFareError):
        c.session_get(None, "00000000-0000-4000-8000-000000000001")
    assert up.calls == []  # no network


def test_session_get_wrong_token_rejected():
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    created = c.session_create(
        True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap, "create-0001"
    )
    other = new_session_capability()["session_token"]
    with pytest.raises(AssetFareError):
        c.session_get(other, created["session_id"])


def test_session_replay_same_token_and_key_same_session():
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    a = c.session_create(True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap, "key-0001")
    b = c.session_create(True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap, "key-0001")
    assert a["session_id"] == b["session_id"]
    assert b["idempotent_replay"] is True


def test_session_different_token_same_key_independent_session():
    c, up = stateful()
    cap1 = new_session_capability()["session_token"]
    cap2 = new_session_capability()["session_token"]
    a = c.session_create(True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap1, "key-0001")
    b = c.session_create(True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap2, "key-0001")
    assert a["session_id"] != b["session_id"]


def test_session_lost_create_response_retry_same_session():
    # The caller owns the token; retrying a lost create with the same token + key
    # recovers the same session (no duplicate).
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    first = c.session_create(
        True, "arbitrum", "USDC", "base", "USDC", 30, wallets_for("arbitrum", "base"), cap, "lost-0001"
    )
    retry = c.session_create(
        True, "arbitrum", "USDC", "base", "USDC", 30, wallets_for("arbitrum", "base"), cap, "lost-0001"
    )
    assert retry["session_id"] == first["session_id"]
    assert retry["idempotent_replay"] is True


def test_session_create_caller_approved_gate():
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    with pytest.raises(AssetFareError):
        c.session_create(False, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap, "keyapprv1")
    assert up.calls == []  # gate before network


def test_session_create_source_only_rejected_before_network():
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    with pytest.raises(AssetFareError):
        c.session_create(
            True, "polygon", "USDC", "base", "USDC", 25, wallets_for("polygon", "base"), cap, "k"
        )
    assert up.calls == []


def test_session_expired_refresh_recovers():
    c, up = stateful()
    cap = new_session_capability()["session_token"]
    created = c.session_create(
        True, "base", "USDC", "arbitrum", "USDC", 25, wallets_for("base", "arbitrum"), cap, "exp-00001"
    )
    sid = created["session_id"]
    up.expire(sid)
    got = c.session_get(cap, sid)
    assert got["status"] == "action_expired"
    with pytest.raises(AssetFareError):
        c.observe_source(cap, sid, "src-exp01", ["0x" + "b" * 40])
    refreshed = c.refresh_action(cap, sid, "refresh-01")
    assert refreshed["status"] == "action_ready"
    assert refreshed["action_available"] is True


def test_invalid_session_token_format_rejected():
    c, up = stateful()
    with pytest.raises(AssetFareError):
        c.session_get("short", "00000000-0000-4000-8000-000000000001")
    with pytest.raises(AssetFareError):
        c.session_get(new_session_capability()["session_token"], "not-a-uuid")


# ---- 76-route multi-step MOCK e2e matrix: 72 complete, 4 blocked (phase B) ----
def test_e2e_76_route_matrix_72_complete_4_blocked():
    c, up = stateful()
    completed = 0
    blocked = 0
    for i, (fc, ft, tc, tt) in enumerate(all_routes()):
        tag = "{:03d}".format(i)
        wallets = wallets_for(fc, tc)
        if fc in SOURCE_ONLY_CHAINS:
            # Source-only routes must be fail-closed at prepare AND session create.
            with pytest.raises(AssetFareError):
                c.prepare(True, fc, ft, tc, tt, 10, wallets)
            with pytest.raises(AssetFareError):
                c.session_create(
                    True, fc, ft, tc, tt, 10, wallets, new_session_capability()["session_token"], f"blocked-{tag}"
                )
            blocked += 1
            continue
        cap = new_session_capability()["session_token"]
        created = c.session_create(True, fc, ft, tc, tt, 10, wallets, cap, f"matrix-{tag}")
        sid = created["session_id"]
        c.observe_source(cap, sid, f"source-{tag}", ["0x" + "c" * 40])
        out = c.observe_output(cap, sid, f"output-{tag}")
        assert out["status"] == "complete"
        completed += 1
    assert completed == 72
    assert blocked == 4
