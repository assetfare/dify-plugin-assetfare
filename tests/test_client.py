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
]


def valid_caps():
    return {
        "status": "capped_public_agent_release",
        "public_api_enabled": True,
        "directed_conversion_routes": 74,
        "unsigned_route_plans_ready": 74,
        "server_signing": False,
        "server_submission": False,
        "chains": ["arbitrum", "base", "polygon", "robinhood", "solana"],
        "asset_endpoints": [{"chain": c, "token": t} for c, t in ENDPOINTS],
        "source_only_asset_endpoints": [{"chain": "polygon", "token": "USDC"}],
        "source_only_routes": ["polygon:USDC->base:USDC", "polygon:USDC->arbitrum:USDC"],
    }


def valid_status():
    return {"status": "capped_public_agent_release", "server_signing": False, "server_submission": False}


def valid_quote(fc, ft, tc, tt, amount):
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
            "assetfare_fee_bps": 1,
            "fee_collection_steps": [],
            "estimated_time_seconds": 23,
        },
        "route": {
            "route": f"{fc}:{ft}->{tc}:{tt}",
            "steps": [{"kind": "burn"}],
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
        "execution": {
            "supported": True,
            "first_unsigned_action_supported": True,
            "future_actions_require_verified_receipts": True,
        },
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
        return entry  # a _Resp or any response-like object (e.g. a hostile stub)


def client(routes):
    return af(session=_Session(routes))


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


# ---- capabilities ----
def test_capabilities_ok():
    caps = client({"/v2/capabilities": valid_caps(), "/v2/status": valid_status()}).get_capabilities()
    assert caps["directed_conversion_routes"] == 74
    assert len(caps["asset_endpoints"]) == 10
    assert caps["server_signs_or_submits"] is False


@pytest.mark.parametrize(
    "mut",
    [
        lambda c: c.update(status="x"),
        lambda c: c.update(public_api_enabled=False),
        lambda c: c.update(directed_conversion_routes=71),
        lambda c: c.update(unsigned_route_plans_ready=70),
        lambda c: c.update(server_signing=True),
        lambda c: c.update(chains=["arbitrum", "base", "solana"]),
        lambda c: c.update(chains=["arbitrum", "base", "solana", "solana"]),  # duplicate substitution
        lambda c: c["asset_endpoints"].pop(),
        lambda c: c.update(source_only_routes=["polygon:USDC->base:ETH", "polygon:USDC->arbitrum:USDC"]),
    ],
)
def test_capabilities_boundary_fail(mut):
    caps = valid_caps()
    mut(caps)
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": caps, "/v2/status": valid_status()}).get_capabilities()


def test_status_boundary_fail():
    with pytest.raises(AssetFareError):
        client(
            {"/v2/capabilities": valid_caps(), "/v2/status": {**valid_status(), "server_signing": True}}
        ).get_capabilities()


# ---- quote wiring + 74 routes ----
def test_quote_exact_body_and_bounded_return():
    s = _Session({"/v2/quote": valid_quote("solana", "USDC", "base", "ETH", 250)})
    out = af(session=s).get_quote("solana", "usdc", "base", "eth", 250)
    body = s.calls[0]["json"]
    assert s.calls[0]["method"] == "POST"
    assert body == {
        "from_chain": "solana",
        "from_token": "USDC",
        "to_chain": "base",
        "to_token": "ETH",
        "amount_usd": 250.0,
    }
    assert out["output_symbol"] == "ETH" and out["server_signs_or_submits"] is False
    assert "fee_collection_steps" not in out


def test_all_74_routes_and_9_identity():
    ok = identity = 0
    for fc, ft in ENDPOINTS:
        for tc, tt in [endpoint for endpoint in ENDPOINTS if endpoint[0] != "polygon"]:
            s = _Session({"/v2/quote": valid_quote(fc, ft, tc, tt, 100)})
            c = af(session=s)
            if (fc, ft) == (tc, tt):
                with pytest.raises(AssetFareError):
                    c.get_quote(fc, ft, tc, tt, 100)
                identity += 1
            elif fc != "polygon" or (ft == "USDC" and tc in {"base", "arbitrum"} and tt == "USDC"):
                c.get_quote(fc, ft, tc, tt, 100)
                ok += 1
            else:
                with pytest.raises(AssetFareError):
                    c.get_quote(fc, ft, tc, tt, 100)
    assert ok == 74 and identity == 9


def test_polygon_destination_rejected():
    with pytest.raises(AssetFareError):
        client({"/v2/quote": valid_quote("base", "USDC", "polygon", "USDC", 10)}).get_quote("base", "USDC", "polygon", "USDC", 10)


@pytest.mark.parametrize("amt", [0.5, 0, 1500, True, "250", float("nan"), float("inf")])
def test_amount_rejected(amt):
    with pytest.raises(AssetFareError):
        client({"/v2/quote": valid_quote("solana", "USDC", "base", "ETH", 100)}).get_quote(
            "solana", "USDC", "base", "ETH", amt
        )


def test_unsupported_endpoint_rejected():
    with pytest.raises(AssetFareError):
        client({"/v2/quote": valid_quote("solana", "USDC", "base", "ETH", 100)}).get_quote(
            "solana", "ETH", "base", "ETH", 100
        )


@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q["intent"].update(**{"from": "base:USDC"}),
        lambda q: q["intent"].update(to="arbitrum:ETH"),
        lambda q: q["intent"].update(amount_usd=249),
        lambda q: q["route"].update(route="solana:SOL->base:USDC"),  # label not the corridor
        lambda q: q["offer"].update(output_symbol="USDC"),
        lambda q: q["offer"].update(expected_receive_amount=0.05),  # < minimum
        lambda q: q["offer"].update(estimated_min_receive_amount=0),
        lambda q: q["risk"].update(server_signing=True),
        lambda q: q["route"].update(server_submission=True),
        lambda q: q["execution"].update(supported=False),
        lambda q: q["risk"].update(non_atomic="yes"),
        lambda q: q.pop("offer"),
    ],
)
def test_quote_response_rejected(mut):
    q = valid_quote("solana", "USDC", "base", "ETH", 250)
    mut(q)
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDC", "base", "ETH", 250)


# ---- transport ----
def test_non_json_rejected():
    with pytest.raises(AssetFareError):
        client(
            {"/v2/capabilities": _Resp(content_type="text/html", raw=b"<html>"), "/v2/status": valid_status()}
        ).get_capabilities()


def test_oversize_rejected():
    big = b'{"x":"' + b"a" * (1_048_600) + b'"}'
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": _Resp(raw=big), "/v2/status": valid_status()}).get_capabilities()


def test_non_2xx_rejected():
    with pytest.raises(AssetFareError):
        client(
            {"/v2/capabilities": _Resp(status_code=500, raw=b'{"error":"x"}'), "/v2/status": valid_status()}
        ).get_capabilities()


def test_malformed_and_array_rejected():
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": _Resp(raw=b"[1,2,3]"), "/v2/status": valid_status()}).get_capabilities()
    with pytest.raises(AssetFareError):
        client({"/v2/capabilities": _Resp(raw=b"{ bad"), "/v2/status": valid_status()}).get_capabilities()


# ---- new strict fields (remediation) ----
@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q.update(status="ok"),  # not the release literal
        lambda q: q.update(quote_id="not-a-uuid"),
        lambda q: q.update(as_of="2026-09-17"),  # not datetime
        lambda q: q.update(ttl_seconds=0),
        lambda q: q.update(ttl_seconds=90000),  # over bound
        lambda q: q["intent"].update(estimated_input_base=0),
        lambda q: q["offer"].update(expected_receive_usd=100),  # < min_usd (248)
        lambda q: q["offer"].update(estimated_min_receive_usd=0),
        lambda q: q["offer"].update(fee_collection_steps=[{}]),  # non-int
        lambda q: q["offer"].update(fee_collection_steps=[-1]),  # negative
        lambda q: q["route"].update(steps=[]),  # empty
        lambda q: q["route"].update(steps=[1, 2]),  # non-object
        lambda q: q["route"].update(quote_latency_ms=-5),
        lambda q: q["risk"].update(fresh_quote_required_each_step=False),
        lambda q: q["execution"].update(future_actions_require_verified_receipts=False),
    ],
)
def test_quote_new_strict_fields_rejected(mut):
    q = valid_quote("solana", "USDC", "base", "ETH", 250)
    mut(q)
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDC", "base", "ETH", 250)


def test_quote_bounded_return_includes_usd_and_ttl():
    out = client({"/v2/quote": valid_quote("solana", "USDC", "base", "ETH", 250)}).get_quote(
        "solana", "USDC", "base", "ETH", 250
    )
    assert out["expected_receive_usd"] == 250 and out["estimated_min_receive_usd"] == 249.98
    assert out["ttl_seconds"] == 30 and out["as_of"] == "2026-09-17T00:00:00Z"


def test_iter_content_error_sanitized():
    class _Boom:
        status_code = 200
        is_redirect = False

        def __init__(self):
            self.headers = {"content-type": "application/json"}

        def iter_content(self, chunk_size=65536):
            raise ConnectionError("stream broke SECRETMARKER_pk_0xdead")

        def close(self):
            pass

    s = _Session({})
    s.routes = {"/v2/capabilities": _Boom(), "/v2/status": valid_status()}
    with pytest.raises(AssetFareError) as ei:
        af(session=s).get_capabilities()
    assert "SECRETMARKER" not in str(ei.value)
    assert ei.value.__cause__ is None


@pytest.mark.parametrize(
    "mut",
    [
        lambda q: q["execution"].pop("first_unsigned_action_supported"),  # missing
        lambda q: q["execution"].update(first_unsigned_action_supported="yes"),  # wrong type
        lambda q: q["execution"].update(first_unsigned_action_supported=1),  # int, not bool
    ],
)
def test_quote_execution_first_unsigned_flag_rejected(mut):
    q = valid_quote("solana", "USDC", "base", "ETH", 250)
    mut(q)
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDC", "base", "ETH", 250)


# ---- as_of RFC3339 freshness (clock injected) ----
@pytest.mark.parametrize(
    "as_of",
    [
        "2026-09-17T00:00:00",  # naive (no timezone) -> rejected
        "2020-01-01T00:00:00Z",  # expired (as_of + ttl < now)
        "2026-09-17T01:00:00Z",  # excessive future skew (> now + 5 min)
        "2026-13-40T99:99:99Z",  # malformed
        "2026-09-17",  # date only, no time
    ],
)
def test_as_of_rejected(as_of):
    q = valid_quote("solana", "USDC", "base", "ETH", 250)
    q["as_of"] = as_of
    with pytest.raises(AssetFareError):
        client({"/v2/quote": q}).get_quote("solana", "USDC", "base", "ETH", 250)


def test_as_of_numeric_offset_accepted():
    q = valid_quote("solana", "USDC", "base", "ETH", 250)
    q["as_of"] = "2026-09-17T00:00:05+00:00"  # tz-aware offset form, fresh
    out = client({"/v2/quote": q}).get_quote("solana", "USDC", "base", "ETH", 250)
    assert out["as_of"] == "2026-09-17T00:00:05+00:00"


# ---- stale-budget check (fake clock) ----
class _Clock:
    def __init__(self, values):
        self._values = list(values)

    def __call__(self):
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]


def test_stale_budget_rejects_delayed_chunk():
    # A (delayed) chunk is received only after the stale budget has already
    # elapsed, so the chunk-boundary check rejects it. This is NOT an absolute
    # mid-read cancel -- it fires once the delayed chunk actually arrives.
    # monotonic: #1 budget base=0 (=> deadline 45), #2 remaining check=0 (ok),
    # #3 at the chunk boundary=100 (> 45) => stale reject.
    clock = _Clock([0.0, 0.0, 100.0])
    s = _Session({"/v2/quote": valid_quote("solana", "USDC", "base", "ETH", 250)})
    c = af(session=s, monotonic=clock)
    with pytest.raises(AssetFareError):
        c.get_quote("solana", "USDC", "base", "ETH", 250)


def test_stale_budget_pre_request_rejected():
    # Stale budget already exhausted before the request is even made.
    clock = _Clock([0.0, 100.0])
    s = _Session({"/v2/quote": valid_quote("solana", "USDC", "base", "ETH", 250)})
    c = af(session=s, monotonic=clock)
    with pytest.raises(AssetFareError):
        c.get_quote("solana", "USDC", "base", "ETH", 250)
