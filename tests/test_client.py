import json
import os
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.client import AssetFareClient, AssetFareError

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

EVM = "0x" + "a" * 40
SOL = "So11111111111111111111111111111111111111112"

EVALUATION_GUIDANCE = {
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
# destination: polygon and optimism (both audited 1bp source executors).
SOURCE_ONLY_CHAINS = {"polygon", "optimism"}
DESTINATION_ENDPOINTS = [(c, t) for (c, t) in ENDPOINTS if c not in SOURCE_ONLY_CHAINS]


def all_routes():
    """Generate all 76 execution-ready directed routes."""
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
        "execution_ready_routes": 76,
        "execution_implemented_routes": 76,
        "currently_prepare_ready_routes": 76,
        "temporarily_unavailable_routes": [],
        "temporarily_unavailable_route_count": 0,
        "execution_availability": {"status":"available","provider":"circle_iris","provider_dependent_routes":50,"recent_fee_snapshot_usable":True,"guarantees_future_availability":False},
        "phase_b_blocked_routes": 0,
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
        "blocked_source_only_routes": [],
        "amount_usd": {"minimum": 1, "maximum": None, "policy": "no_business_maximum"},
        "evaluation_guidance": dict(EVALUATION_GUIDANCE),
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


def valid_quote(fc, ft, tc, tt, amount, *, source_only=False, fee=1):
    steps = [{"kind": "burn"}]
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
            "fee_blocker": None,
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
        "caller_action_plan_handoff_v2": executable_handoff_v2(),
        "handoff_schema_version": 2,
    }


def with_cost_summary(quote):
    amount=float(quote["intent"]["amount_usd"]);expected=float(quote["offer"]["expected_receive_usd"]);minimum=float(quote["offer"]["estimated_min_receive_usd"])
    ec=max(0.0,amount-expected);mc=max(0.0,amount-minimum)
    quote["cost_summary"]={"scope":"token_path_only_network_gas_excluded","input_value_usd":amount,"expected_receive_value_usd":expected,"minimum_receive_value_usd":minimum,"expected_total_cost_usd":ec,"maximum_total_cost_usd":mc,"expected_total_cost_percent":ec/amount*100,"maximum_total_cost_percent":mc/amount*100,"assetfare_service_fee":{"bps":1,"estimated_usd":min(amount/10_000,5.0),"included_in_receive_amount":True,"note":"service fee only"},"provider_fee_components":[],"unpriced_costs":["source_chain_network_fee"],"rankable_all_in":False,"small_amount_warning":mc/amount>=.01,"warning":None}
    quote["eta"]={"estimated_time_seconds":quote["offer"]["estimated_time_seconds"],"estimated_time_range_seconds":[8,23],"complete_route_estimate":True,"sources":[],"note":"estimate"}
    return quote


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


# ---- capabilities (6-chain surface: 11 endpoints, all 76 executable) ----
def test_capabilities_ok():
    caps = client({"/v2/capabilities": valid_caps(), "/v2/status": valid_status()}).get_capabilities()
    assert caps["directed_conversion_routes"] == 76
    assert caps["unsigned_route_plans_ready"] == 76
    assert caps["execution_ready_routes"] == 76
    assert caps["currently_prepare_ready_routes"] == 76
    assert caps["phase_b_blocked_routes"] == 0
    assert len(caps["asset_endpoints"]) == 11
    assert caps["server_signs_or_submits"] is False
    assert sorted(caps["source_only_asset_endpoints"]) == ["optimism:USDC", "polygon:USDC"]
    assert set(caps["source_only_routes"]) == {
        "polygon:USDC->base:USDC",
        "polygon:USDC->arbitrum:USDC",
        "optimism:USDC->base:USDC",
        "optimism:USDC->arbitrum:USDC",
    }
    assert caps["blocked_source_only_routes"] == []
    assert caps["amount_usd"] == {
        "minimum": 1.0,
        "maximum": None,
        "policy": "no_business_maximum",
    }
    assert caps["evaluation_guidance"] == EVALUATION_GUIDANCE


@pytest.mark.parametrize(
    "mut",
    [
        lambda c: c.update(directed_conversion_routes=75),
        lambda c: c.update(execution_ready_routes=72),
        lambda c: c.update(phase_b_blocked_routes=4),
        lambda c: c.update(blocked_source_only_routes=["polygon:USDC->base:USDC"]),
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
    lambda c:c.update(currently_prepare_ready_routes=75,temporarily_unavailable_routes=["evil:USDC->base:USDC"],temporarily_unavailable_route_count=1,execution_availability={"status":"degraded","provider":"circle_iris","guarantees_future_availability":False}),
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
        lambda c: c["evaluation_guidance"].update(evidence_as_of="2026-09-24"),
        lambda c: c["evaluation_guidance"].update(extra=True),
    ],
)
def test_capabilities_evaluation_guidance_must_match_core_exactly(mut):
    caps = valid_caps()
    mut(caps)
    with pytest.raises(AssetFareError, match="assetfare_evaluation_guidance_invalid"):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


# ---- quote happy path + fee surfaced ----
def test_quote_exact_body_and_bounded_return():
    q = with_cost_summary(valid_quote("solana", "SOL", "base", "ETH", 250))
    c = client({"/v2/quote": q})
    out = c.get_quote("solana", "SOL", "base", "ETH", 250)
    assert out["from"] == "solana:SOL" and out["to"] == "base:ETH"
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
    assert out["evaluation_guidance"] == EVALUATION_GUIDANCE
    assert out["server_signs_or_submits"] is False


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
    q=with_cost_summary(valid_quote("solana","SOL","base","ETH",250));mut(q)
    with pytest.raises(AssetFareError):client({"/v2/quote":q}).get_quote("solana","SOL","base","ETH",250)


def test_rollback_core_without_cost_derives_honest_total():
    q=valid_quote("solana","SOL","base","ETH",250)
    out=client({"/v2/quote":q}).get_quote("solana","SOL","base","ETH",250)
    assert out["cost_summary"]["scope"]=="token_path_only_network_gas_excluded"
    assert "provider_fee_breakdown_unavailable_legacy_core" in out["cost_summary"]["unpriced_costs"]


def test_rollback_core_large_amount_fee_is_exact_one_bp_without_maximum():
    amount = 100_000
    q = valid_quote("solana", "SOL", "base", "ETH", amount)
    out = client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", amount)
    assert out["cost_summary"]["assetfare_service_fee"]["estimated_usd"] == 10


def test_quote_accepts_sub_micro_usd_rounding_alignment():
    q=with_cost_summary(valid_quote("solana","SOL","base","ETH",250));q["offer"]["expected_receive_usd"]=249.1234567;q["offer"]["estimated_min_receive_usd"]=248.123456;q["cost_summary"].update(expected_receive_value_usd=249.123457,minimum_receive_value_usd=248.123456,expected_total_cost_usd=.876543,maximum_total_cost_usd=1.876544,expected_total_cost_percent=.3506172,maximum_total_cost_percent=.7506176,small_amount_warning=False,warning=None)
    out=client({"/v2/quote":q}).get_quote("solana","SOL","base","ETH",250)
    assert out["cost_summary"]["expected_receive_value_usd"]==249.123457










# ---- FAIL-CLOSED handoff (no local fallback) ----




# ---- source-only directional quotes use the same executable handoff ----
def test_source_only_quote_execution_ready():
    q = valid_quote("polygon", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    out = client({"/v2/quote": q}).get_quote("polygon", "USDC", "base", "USDC", 100)
    assert out["source_only"] is True
    assert out["execution_supported"] is True
    assert out["execution_blocker"] is None
    assert out["fee_collectible_now"] is True
    assert "caller_action_plan_handoff" not in out
    assert "caller_action_plan_handoff_v2" not in out
    assert "handoff_schema_version" not in out


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
    q = valid_quote("optimism", "USDC", "arbitrum", "USDC", 100, source_only=True, fee=1)
    out = client({"/v2/quote": q}).get_quote("optimism", "USDC", "arbitrum", "USDC", 100)
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
    q = valid_quote("solana", "SOL", "base", "ETH", 250)
    mut(q["offer"])
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "SOL", "base", "ETH", 250)


def test_source_only_fee_collectible_now_false_rejected():
    q = valid_quote("polygon", "USDC", "base", "USDC", 100, source_only=True, fee=1)
    q["offer"]["fee_collectible_now"] = False
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("polygon", "USDC", "base", "USDC", 100)


# ---- route enumeration ----
def test_all_76_routes_generated_and_executable():
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
@pytest.mark.parametrize("amt", [0.99, "5", True, float("nan"), float("inf"), None])
def test_amount_rejected(amt):
    with pytest.raises(AssetFareError):
        client({}).get_quote("solana", "SOL", "base", "ETH", amt)


@pytest.mark.parametrize("amt", [1000.01, 5000])
def test_amount_above_former_business_maximum_accepted(amt):
    quote = with_cost_summary(valid_quote("solana", "SOL", "base", "ETH", amt))
    result = client({"/v2/quote": quote}).get_quote("solana", "SOL", "base", "ETH", amt)
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
