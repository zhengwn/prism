"""Webhook delivery (v0.3).

External agents register a callback URL (via the MCP ``prism_register_webhook``
tool). After a sync, the sidecar POSTs matching new items to each enabled
webhook, signed with HMAC-SHA256 so the receiver can verify authenticity.

Security note: an attacker who can register a webhook could otherwise use the
sidecar as an SSRF proxy into the local network / cloud metadata endpoint.
``assert_safe_webhook_url`` blocks non-HTTP(S) schemes and any host that
resolves to a loopback / private / link-local / reserved / multicast address.
It runs at registration AND again right before each delivery (re-resolving the
host) to blunt DNS-rebinding.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from prism_sidecar import config, store

log = logging.getLogger(__name__)

# Item fields delivered in a webhook payload — the compact projection, same
# shape prism_recent_items returns. Kept here (not imported from mcp_server)
# so the sidecar delivery path never imports the MCP module.
_ITEM_KEYS = (
    "id", "sourceId", "sourceName", "url", "title", "summary",
    "tags", "author", "publishedAt", "status", "contentType", "durationSec",
)

SIGNATURE_HEADER = "X-Prism-Signature"
DELIVERY_HEADER = "X-Prism-Delivery"


class UnsafeWebhookURL(ValueError):
    """Raised when a webhook URL is not a safe public HTTP(S) target."""


def generate_secret() -> str:
    """A URL-safe signing secret handed to the agent once at registration."""
    return secrets.token_urlsafe(32)


def _ip_is_unsafe(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local  # includes 169.254.169.254 cloud metadata
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _parse_webhook_url(url: str) -> tuple[str, Optional[int]]:
    """Scheme + host validation shared by the sync/async checkers.

    Returns ``(host, port)`` for the resolver step, or raises
    UnsafeWebhookURL.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeWebhookURL(
            f"webhook url must be http(s), got scheme {parsed.scheme!r}"
        )
    host = parsed.hostname
    if not host:
        raise UnsafeWebhookURL(f"webhook url has no host: {url!r}")
    return host, parsed.port


def _check_resolved_ips(host: str, resolved: set[str]) -> None:
    """Raise UnsafeWebhookURL unless every resolved IP is public."""
    if not resolved:
        raise UnsafeWebhookURL(f"webhook host {host!r} resolved to nothing")
    for ip in resolved:
        if _ip_is_unsafe(ip):
            raise UnsafeWebhookURL(
                f"webhook host {host!r} resolves to a non-public address ({ip}); "
                "loopback / private / link-local targets are blocked"
            )


def assert_safe_webhook_url(url: str) -> None:
    """Raise UnsafeWebhookURL unless ``url`` is an http(s) URL whose host
    resolves entirely to public addresses.

    Synchronous variant — ``socket.getaddrinfo`` BLOCKS. Fine from sync
    code and tests; on the event loop use
    :func:`assert_safe_webhook_url_async` instead, or a slow DNS lookup
    stalls every coroutine in the process.
    """
    host, port = _parse_webhook_url(url)
    try:
        infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhookURL(f"cannot resolve webhook host {host!r}: {exc}") from exc
    _check_resolved_ips(host, {info[4][0] for info in infos})


async def assert_safe_webhook_url_async(url: str) -> None:
    """Async variant of :func:`assert_safe_webhook_url`.

    Resolves via the loop's resolver (thread-pool backed), so DNS never
    blocks the event loop. Used at registration (MCP tool) and right
    before each delivery.
    """
    host, port = _parse_webhook_url(url)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhookURL(f"cannot resolve webhook host {host!r}: {exc}") from exc
    _check_resolved_ips(host, {info[4][0] for info in infos})


def sign(secret: str, body: bytes) -> str:
    """The value for the X-Prism-Signature header: ``sha256=<hex hmac>``."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _brief_item(item: Any) -> dict[str, Any]:
    full = item.model_dump(by_alias=True, mode="json")
    return {k: full[k] for k in _ITEM_KEYS}


def _matches(webhook: store.Webhook, item: Any) -> bool:
    if webhook.source_id is not None and item.source_id != webhook.source_id:
        return False
    if webhook.tag is not None and webhook.tag not in (item.tags_zh or []):
        return False
    return True


async def _deliver_one(
    client: httpx.AsyncClient,
    webhook: store.Webhook,
    matched: list[Any],
) -> None:
    """POST matched items to one webhook. Never raises — records the result."""
    body = json.dumps(
        {
            "event": "items.new",
            "webhookId": webhook.id,
            "count": len(matched),
            "items": [_brief_item(i) for i in matched],
        },
        ensure_ascii=False,
    ).encode()
    delivery_id = uuid.uuid4().hex
    try:
        # Re-check the URL right before sending (DNS-rebinding defense).
        # Async variant: DNS goes through the loop's resolver so a slow
        # lookup can't stall the whole event loop mid-sync.
        await assert_safe_webhook_url_async(webhook.url)
        resp = await client.post(
            webhook.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                SIGNATURE_HEADER: sign(webhook.secret, body),
                DELIVERY_HEADER: delivery_id,
            },
        )
        ok = 200 <= resp.status_code < 300
        await store.record_webhook_delivery(
            webhook.id, ok=ok, status=f"HTTP {resp.status_code}",
            max_fails=config.WEBHOOK_MAX_FAILS,
        )
        if not ok:
            log.warning(
                "[webhook] %s delivery got HTTP %s", webhook.id, resp.status_code
            )
    except Exception as exc:  # noqa: BLE001 — one bad webhook must not break sync
        await store.record_webhook_delivery(
            webhook.id, ok=False, status=f"error: {exc}",
            max_fails=config.WEBHOOK_MAX_FAILS,
        )
        log.warning("[webhook] %s delivery failed: %s", webhook.id, exc)


async def dispatch_for_items(item_ids: list[str]) -> None:
    """Fan new items out to matching enabled webhooks. Never raises.

    Called from the sync pipeline after a source syncs successfully. A no-op
    when there are no items or no webhooks, so the common case is one cheap
    query.
    """
    if not item_ids:
        return
    try:
        webhooks = await store.list_enabled_webhooks()
        if not webhooks:
            return
        items = []
        for item_id in item_ids:
            item = await store.get_item(item_id)
            if item is not None:
                items.append(item)
        if not items:
            return
        timeout = httpx.Timeout(config.WEBHOOK_TIMEOUT_SEC)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for webhook in webhooks:
                matched = [it for it in items if _matches(webhook, it)]
                if matched:
                    await _deliver_one(client, webhook, matched)
    except Exception:  # noqa: BLE001 — dispatch is best-effort, never break sync
        log.exception("[webhook] dispatch failed")
