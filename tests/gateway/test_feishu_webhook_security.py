"""Feishu callbacks must not lose authenticated capacity to anonymous traffic.

Regression for CVE-2026-10224 / upstream issue #29154.
"""

import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as feishu


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", ["token", "signature", "both"])
async def test_invalid_http_burst_preserves_authenticated_delivery_quota(monkeypatch, auth_mode):
    # A reverse proxy makes invalid requests and real deliveries share request.remote.
    # Exercise the real HTTP reader, parser, auth checks and quota on that same peer.
    token = "test-verification-token" if auth_mode != "signature" else ""
    key = "test-encrypt-key" if auth_mode != "token" else ""
    monkeypatch.delenv("FEISHU_VERIFICATION_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_ENCRYPT_KEY", raising=False)
    adapter = feishu.FeishuAdapter(PlatformConfig(extra={
        "app_id": "test-app", "verification_token": token, "encrypt_key": key,
    }))
    anonymous_burst = 3 * feishu._FEISHU_WEBHOOK_RATE_LIMIT_MAX
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_LIMIT_MAX", 3)
    app = web.Application(client_max_size=feishu._FEISHU_WEBHOOK_MAX_BODY_BYTES)
    app.router.add_post(adapter._webhook_path, adapter._handle_webhook_request)

    async with TestClient(TestServer(app)) as client:
        async def send(payload, *, sign=False, content_type="application/json"):
            body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
            headers = {"Content-Type": content_type}
            if sign:
                headers.update({
                    "x-lark-request-timestamp": "1700000000",
                    "x-lark-request-nonce": "test-nonce",
                    "x-lark-signature": hashlib.sha256(
                        b"1700000000test-nonce" + key.encode() + body
                    ).hexdigest(),
                })
            async with client.post(adapter._webhook_path, data=body, headers=headers) as response:
                await response.read()
                return response.status

        for _ in range(anonymous_burst):
            assert await send(b"not json", content_type="text/plain") == 415
            assert await send(b"not json") == 400
            assert await send({"header": {"token": "wrong"}}, sign=bool(token and key)) == 401
            if key:
                assert await send({"header": {"token": token}}) == 401
            # Encrypt-key-only setups must not anonymously reflect challenges or
            # let them consume a quota intended for authenticated callbacks.
            assert await send({"type": "url_verification", "token": "wrong"}) == 401

        for malformed in ([], None, {"header": ["invalid"]}, b"[" * 2000 + b"]" * 2000):
            assert await send(malformed) == 400
        if token:
            assert await send({"token": "\ud800"}) == 401

        assert adapter._webhook_rate_counts == {}
        for _ in range(feishu._FEISHU_WEBHOOK_RATE_LIMIT_MAX):
            assert await send({"header": {"token": token}}, sign=bool(key)) == 200
        assert await send({"header": {"token": token}}, sign=bool(key)) == 429

        for rate_key, (count, started) in adapter._webhook_rate_counts.items():
            adapter._webhook_rate_counts[rate_key] = (
                count, started - feishu._FEISHU_WEBHOOK_RATE_WINDOW_SECONDS - 1,
            )
        if token:
            assert await send({
                "type": "url_verification", "token": token, "challenge": "challenge",
            }) == 200
        else:
            assert await send({"header": {}}, sign=True) == 200


def test_anonymous_source_tracking_is_bounded_and_recovers_after_expiry(monkeypatch):
    adapter = feishu.FeishuAdapter(PlatformConfig())
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_ANOMALY_MAX_KEYS", 3)
    now = 1000.0
    monkeypatch.setattr(feishu.time, "time", lambda: now)
    for number in range(20):
        adapter._webhook_reject(f"192.0.2.{number}", "401-token", 401)
    assert len(adapter._webhook_anomaly_counts) == 3
    adapter._webhook_reject("192.0.2.0", "401-token", 401)
    assert adapter._webhook_anomaly_counts["192.0.2.0"][0] == 2

    now += feishu._FEISHU_WEBHOOK_ANOMALY_TTL_SECONDS + 1
    adapter._webhook_reject("198.51.100.1", "401-token", 401)
    assert set(adapter._webhook_anomaly_counts) == {"198.51.100.1"}
