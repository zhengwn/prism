"""Webhook delivery + SSRF guard tests (prism_sidecar/webhooks.py).

Outbound POSTs are mocked with respx (a dev dep). Webhook URLs use public IP
literals so the SSRF guard's getaddrinfo is deterministic and never hits DNS
(real hostnames can resolve to private ranges in sandboxed CI).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from prism_sidecar import store, webhooks
from prism_sidecar.db import init_db
from prism_sidecar.distillers.base import DistilledItem
from prism_sidecar.fetchers.base import RawItem

# A public IP literal (no DNS). respx matches on it fine.
PUBLIC_URL = "https://93.184.216.34/hook"


@pytest.fixture
async def initialized():
    await init_db()
    yield


# ---- SSRF guard -----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",           # loopback v4
        "http://localhost/x",           # loopback via name
        "http://10.0.0.5/x",            # private
        "http://192.168.1.1/x",         # private
        "http://172.16.0.1/x",          # private
        "http://169.254.169.254/x",     # link-local (cloud metadata)
        "http://[::1]/x",               # loopback v6
        "ftp://93.184.216.34/x",        # non-http scheme
        "file:///etc/passwd",           # non-http scheme
        "https:///nohost",              # no host
    ],
)
def test_ssrf_guard_blocks(url):
    with pytest.raises(webhooks.UnsafeWebhookURL):
        webhooks.assert_safe_webhook_url(url)


@pytest.mark.parametrize("url", ["https://93.184.216.34/x", "http://8.8.8.8/y", "https://1.1.1.1"])
def test_ssrf_guard_allows_public(url):
    webhooks.assert_safe_webhook_url(url)  # must not raise


# ---- signing --------------------------------------------------------------


def test_sign_matches_hmac_sha256():
    body = b'{"hello":"world"}'
    sig = webhooks.sign("mysecret", body)
    expected = "sha256=" + hmac.new(b"mysecret", body, hashlib.sha256).hexdigest()
    assert sig == expected


# ---- dispatch -------------------------------------------------------------


async def _seed_item(source, title_en, *, tags=None):
    raw = RawItem(
        url=f"https://x/{title_en.replace(' ', '-')}", title=title_en,
        content="body", published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    item_id = await store.insert_item_from_raw(source, raw)
    if tags:
        await store.update_item_distilled(
            item_id,
            DistilledItem(title_zh=title_en, summary_zh="s", key_points_zh=[], tags_zh=tags),
        )
    return item_id


@respx.mock
async def test_dispatch_posts_signed_payload(initialized):
    src = await store.create_source("S", "rss", "https://s")
    item_id = await _seed_item(src, "Hello")
    wh = await store.create_webhook(url=PUBLIC_URL, secret="k")

    route = respx.post(PUBLIC_URL).mock(return_value=httpx.Response(200))
    await webhooks.dispatch_for_items([item_id])

    assert route.called
    req = route.calls.last.request
    body = req.content
    # Signature header verifies against the secret.
    assert req.headers[webhooks.SIGNATURE_HEADER] == webhooks.sign("k", body)
    assert webhooks.DELIVERY_HEADER in req.headers
    payload = json.loads(body)
    assert payload["event"] == "items.new"
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == item_id

    # Success recorded.
    got = await store.get_webhook(wh.id)
    assert got.last_status == "HTTP 200"
    assert got.fail_streak == 0


@respx.mock
async def test_dispatch_source_filter(initialized):
    src_a = await store.create_source("A", "rss", "https://a")
    src_b = await store.create_source("B", "rss", "https://b")
    id_a = await _seed_item(src_a, "From A")
    id_b = await _seed_item(src_b, "From B")
    # Webhook only wants src_b.
    await store.create_webhook(url=PUBLIC_URL, secret="k", source_id=src_b.id)

    route = respx.post(PUBLIC_URL).mock(return_value=httpx.Response(200))
    await webhooks.dispatch_for_items([id_a, id_b])

    assert route.called
    payload = json.loads(route.calls.last.request.content)
    ids = [i["id"] for i in payload["items"]]
    assert ids == [id_b]  # src_a item filtered out


@respx.mock
async def test_dispatch_tag_filter(initialized):
    src = await store.create_source("S", "rss", "https://s")
    id_tagged = await _seed_item(src, "Open source thing", tags=["开源"])
    id_plain = await _seed_item(src, "Something else")
    await store.create_webhook(url=PUBLIC_URL, secret="k", tag="开源")

    route = respx.post(PUBLIC_URL).mock(return_value=httpx.Response(200))
    await webhooks.dispatch_for_items([id_tagged, id_plain])

    payload = json.loads(route.calls.last.request.content)
    assert [i["id"] for i in payload["items"]] == [id_tagged]


@respx.mock
async def test_dispatch_no_match_does_not_post(initialized):
    src = await store.create_source("S", "rss", "https://s")
    item_id = await _seed_item(src, "Untagged")
    await store.create_webhook(url=PUBLIC_URL, secret="k", tag="不存在")

    route = respx.post(PUBLIC_URL).mock(return_value=httpx.Response(200))
    await webhooks.dispatch_for_items([item_id])
    assert not route.called


@respx.mock
async def test_dispatch_failure_bumps_streak_but_never_raises(initialized):
    src = await store.create_source("S", "rss", "https://s")
    item_id = await _seed_item(src, "Hi")
    wh = await store.create_webhook(url=PUBLIC_URL, secret="k")

    respx.post(PUBLIC_URL).mock(return_value=httpx.Response(500))
    # Must not raise into the caller (the sync pipeline).
    await webhooks.dispatch_for_items([item_id])

    got = await store.get_webhook(wh.id)
    assert got.fail_streak == 1
    assert got.last_status == "HTTP 500"


@respx.mock
async def test_dispatch_connection_error_recorded_not_raised(initialized):
    src = await store.create_source("S", "rss", "https://s")
    item_id = await _seed_item(src, "Hi")
    wh = await store.create_webhook(url=PUBLIC_URL, secret="k")

    respx.post(PUBLIC_URL).mock(side_effect=httpx.ConnectError("boom"))
    await webhooks.dispatch_for_items([item_id])  # no raise

    got = await store.get_webhook(wh.id)
    assert got.fail_streak == 1
    assert got.last_status.startswith("error:")


async def test_dispatch_empty_is_noop(initialized):
    # No webhooks, no items — must be a cheap no-op, no network.
    await webhooks.dispatch_for_items([])
    src = await store.create_source("S", "rss", "https://s")
    item_id = await _seed_item(src, "Hi")
    await webhooks.dispatch_for_items([item_id])  # no webhooks registered
