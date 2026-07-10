"""X (Twitter) fetcher — v0.2c PoC.

选型说明（PoC conclusion）
--------------------------
X 没有免费、稳定、无鉴权的「时间线」接口:

* **FxTwitter**（``api.fxtwitter.com``）文档稳定、无需鉴权,但只服务
  **单条推文**（``/:handle/status/:id``）—— 它没有 timeline 端点,
  没法用来「发现某个账号的新推文」,只能做单推富化。
* **无鉴权抓时间线**（syndication CDN / 页面抓取）极其脆弱: 端点随时变、
  返回结构不稳定,写死解析器就是在赌一个没法在 CI 里验证的 schema。
* **Nitter / 自托管 RSSHub** 把一个账号的时间线暴露成标准 RSS/Atom feed,
  这正好能复用 Prism 已经跑了三个 fetcher 的下载/重试/lookback/FetchError
  机制。

所以 PoC 选型 = **bridge-RSS**: X 源指向一个 bridge feed（自托管 RSSHub 的
``/twitter/user/:handle`` 或 Nitter 的 ``/:handle/rss``）,``XFetcher`` 继承
``RSSFetcher``（跟 ``PodcastFetcher`` 一个套路),只做三件 X 专属的事:

1. **URL/handle 归一化** —— 用户可以填 ``@simonw`` / ``simonw`` /
   ``x.com/simonw`` / 直接一整条 bridge feed URL;
2. **推文元数据** —— 从 entry link 里抽 tweet id + handle,识别 RT/回复,
   打上 ``feed_kind="x"`` / ``content_type=post``;
3. **短文本 prompt** —— 推文是短文本(常常是 thread),走 ``distillers``
   里的 X 专属 prompt,而不是为长文写的通用模板。

FxTwitter 之后可作为「单推富化」层叠加(拉全文/引用推),不影响这里的发现逻辑。

Source 配置约定
---------------
* ``source.url`` 可以是:
  - 一整条 bridge feed URL(host 不是 x.com/twitter.com,直接当 feed 用),或
  - ``@handle`` / ``handle`` / ``x.com/handle`` 之类的账号标识。
* 账号标识模式下,需要一个 bridge base:``config_json.bridge``
  (如 ``https://rsshub.example.com``);缺失 → ``FetchError(retryable=False)``,
  用户会在 ``sources.last_error`` 里看到明确的配置提示。
* ``config_json.feed_url`` 可以直接钉死最终 feed URL(优先级最高)。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from prism_sidecar.fetchers.base import FetchError, RawItem
from prism_sidecar.fetchers.rss import RSSFetcher
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)

# Path segments that are X routes, not handles — reject these so
# `x.com/home` doesn't get treated as a user named "home".
_RESERVED_HANDLES = frozenset(
    {"home", "search", "explore", "notifications", "messages", "i", "settings", "compose"}
)

# A valid X handle: 1–15 chars, letters/digits/underscore. (X caps at 15.)
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# Extract tweet id + handle from a status URL, tolerating bridge hosts:
#   https://x.com/simonw/status/123
#   https://nitter.net/simonw/status/123#m
#   https://rsshub.example.com/twitter/user/simonw ... /status/123
_STATUS_RE = re.compile(r"/(?P<handle>[A-Za-z0-9_]{1,15})/status/(?P<id>\d+)")


def extract_handle(raw: str) -> str | None:
    """Normalise a user-supplied identifier to a bare handle, or None.

    Accepts ``@simonw`` / ``simonw`` / ``x.com/simonw`` /
    ``https://twitter.com/simonw/`` / ``nitter.net/simonw``.
    Returns None if no plausible handle can be extracted.
    """
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("@"):
        s = s[1:]
    # Bare handle?
    if _HANDLE_RE.match(s) and s.lower() not in _RESERVED_HANDLES:
        return s
    # URL form — pull the first path segment.
    if "//" not in s:
        s = "//" + s  # let urlsplit see a host even without a scheme
    parts = urlsplit(s)
    host = (parts.hostname or "").lower()
    if host and not (
        "x.com" in host or "twitter.com" in host or "nitter" in host
    ):
        # Not an X/twitter/nitter URL — caller decides whether to treat
        # the whole thing as a direct feed URL.
        return None
    segments = [seg for seg in parts.path.split("/") if seg]
    if not segments:
        return None
    candidate = segments[0]
    if _HANDLE_RE.match(candidate) and candidate.lower() not in _RESERVED_HANDLES:
        return candidate
    return None


def _looks_like_direct_feed(url: str) -> bool:
    """True if `url` is a full http(s) URL whose host isn't X/twitter —
    i.e. a bridge/Nitter/RSSHub feed we can fetch as-is."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower()
    return not ("x.com" in host or "twitter.com" in host)


def resolve_feed_url(source: Source) -> str:
    """Turn an X source's config into a concrete bridge feed URL.

    Precedence: explicit ``config_json.feed_url`` → a direct-feed
    ``source.url`` → ``{config_json.bridge}/twitter/user/{handle}``.
    Raises FetchError(retryable=False) for unusable config so the user
    sees it in ``sources.last_error``.
    """
    cfg = source.config_json or {}

    feed_url = cfg.get("feed_url")
    if isinstance(feed_url, str) and feed_url.strip():
        return feed_url.strip()

    url = (source.url or "").strip()
    if url and _looks_like_direct_feed(url):
        return url

    handle = extract_handle(url)
    if not handle:
        raise FetchError(
            f"X source {source.id}: can't parse a handle from url={url!r}; "
            "provide @handle, an x.com profile URL, or a direct config_json.feed_url",
            retryable=False,
        )

    bridge = cfg.get("bridge")
    if not (isinstance(bridge, str) and bridge.strip()):
        raise FetchError(
            f"X source {source.id} ({handle}): needs config_json.bridge "
            "(a self-hosted RSSHub/Nitter base URL, e.g. https://rsshub.example.com) "
            "or a direct config_json.feed_url — no-auth X timeline scraping isn't "
            "reliable enough to hardcode a default",
            retryable=False,
        )

    base = bridge.strip().rstrip("/")
    return f"{base}/twitter/user/{handle}"


def parse_status_link(link: str) -> tuple[str | None, str | None]:
    """Return (handle, tweet_id) parsed from a status URL, or (None, None)."""
    m = _STATUS_RE.search(link or "")
    if not m:
        return None, None
    return m.group("handle"), m.group("id")


def _classify_tweet(title: str) -> str:
    """Rough kind hint from the entry title prefix (Nitter/RSSHub style):
    ``RT by …`` → retweet, ``R to …`` / leading ``@`` → reply, else post."""
    t = (title or "").lstrip()
    low = t.lower()
    if low.startswith("rt by") or low.startswith("rt @"):
        return "retweet"
    if low.startswith("r to ") or t.startswith("@"):
        return "reply"
    return "post"


class XFetcher(RSSFetcher):
    """X (Twitter) fetcher — bridge-RSS PoC. Inherits the whole
    download/parse/lookback pipeline (and the v0.2c FetchError contract)
    from RSSFetcher; resolves a handle to a bridge feed URL and enriches
    each entry with tweet metadata."""

    kind: SourceKind = SourceKind.x

    async def fetch(
        self, source: Source, *, lookback_days: int | None = None
    ) -> list[RawItem]:
        feed_url = resolve_feed_url(source)  # may raise FetchError (config)
        # Delegate to RSSFetcher against the resolved feed. A shallow copy
        # keeps the original Source (and its id/name/config) intact while
        # swapping only the URL the RSS pipeline downloads.
        resolved = source.model_copy(update={"url": feed_url})
        return await super().fetch(resolved, lookback_days=lookback_days)

    def _entry_to_raw(
        self,
        entry: Any,
        source: Source,
        *,
        link: str,
        published_at: datetime,
    ) -> RawItem | None:
        raw = super()._entry_to_raw(entry, source, link=link, published_at=published_at)
        if raw is None:
            return None

        raw.content_type = ContentType.post
        raw.metadata["feed_kind"] = "x"

        handle, tweet_id = parse_status_link(link)
        if handle:
            raw.metadata["handle"] = handle
        if tweet_id:
            raw.metadata["tweet_id"] = tweet_id

        raw.metadata["tweet_kind"] = _classify_tweet(raw.title)
        return raw


__all__ = [
    "XFetcher",
    "extract_handle",
    "resolve_feed_url",
    "parse_status_link",
]
