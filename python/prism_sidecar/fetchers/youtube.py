"""YouTube fetcher — v0.2c (design: docs/design/youtube-fetcher.md).

Backed by yt-dlp (no API key; covers channel listing, video metadata,
and both manual + automatic captions). Two source modes via
``source.config_json``, mirroring the Bilibili fetcher's mid/bvid pair:

* ``{"channel": "@handle" | channel URL | "UCxxxx"}`` — 频道最近上传
* ``{"video": "<video id>" | video URL}``            — 单视频
* ``{"playlist": ...}``                              — 未实现,留 TODO

Per-video flow:

1. ``extract_flat`` 拉频道 /videos 页 → 最近 N 条的 id 列表
2. 逐视频 ``extract_info(download=False)`` → 标题/简介/时长/上传日期 +
   字幕轨道（``subtitles`` = 人工, ``automatic_captions`` = 自动）
3. 字幕四级优先: 人工 zh → 人工任意 → 自动 zh → 自动 en
4. json3 格式下载 → 归一化成 ``{"from": sec, "content": str}`` cues →
   复用 ``_subtitle.subtitle_body_to_markdown``（[CC]/[AI] 行格式是和
   distiller 的契约）

与 Bilibili 不同: YouTube 的 ``upload_date`` 可靠,所以 ``lookback_days``
真正生效——列表按时间倒序,碰到第一条过期视频就停止。

yt-dlp is synchronous — every call goes through ``asyncio.to_thread``.
Errors follow the v0.2c contract: whole-source failures raise
``FetchError`` (with any already-built items attached as partials);
single-video failures are skipped.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from prism_sidecar.config import FETCH_LOOKBACK_DAYS, FETCH_TIMEOUT_SEC
from prism_sidecar.fetchers import _retry
from prism_sidecar.fetchers._retry import retry_async
from prism_sidecar.fetchers._subtitle import subtitle_body_to_markdown
from prism_sidecar.fetchers.base import FetchError, Fetcher, RawItem
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)


# --- yt-dlp import guarded so tests can monkeypatch -------------------

try:  # pragma: no cover - exercised via tests with monkeypatch
    import yt_dlp as _yt_dlp
except Exception:  # pragma: no cover
    _yt_dlp = None  # type: ignore[assignment]


# --- module-level helpers (pure functions, easy to unit-test) ---------

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_WATCH_URL_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
_SHORT_URL_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})")


def parse_video_ref(value: str) -> str | None:
    """Normalize a video reference (bare id / watch URL / youtu.be) to an id."""
    v = (value or "").strip()
    if not v:
        return None
    if _VIDEO_ID_RE.match(v):
        return v
    m = _WATCH_URL_RE.search(v) or _SHORT_URL_RE.search(v)
    return m.group(1) if m else None


def parse_channel_ref(value: str) -> str | None:
    """Normalize a channel reference to the /videos listing URL.

    Accepts: ``@handle``, ``UCxxxx…`` channel ids, and any
    ``youtube.com/(@handle|channel/UC…|c/name|user/name)`` URL.
    """
    v = (value or "").strip().rstrip("/")
    if not v:
        return None
    if v.startswith("@"):
        return f"https://www.youtube.com/{v}/videos"
    if re.match(r"^UC[A-Za-z0-9_-]{10,}$", v):
        return f"https://www.youtube.com/channel/{v}/videos"
    if "youtube.com/" in v:
        # Strip an existing /videos suffix, then re-add it.
        v = re.sub(r"/(videos|streams|shorts|featured)$", "", v)
        m = re.search(r"(youtube\.com/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+))", v)
        if m:
            return f"https://www.{m.group(1)}/videos"
    return None


def _upload_dt(info: dict[str, Any]) -> datetime | None:
    """Prefer epoch ``timestamp``; fall back to ``upload_date`` (YYYYMMDD)."""
    ts = info.get("timestamp") or info.get("release_timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    raw = info.get("upload_date")
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _is_zh(lang: str) -> bool:
    return lang.lower().startswith("zh")


def _pick_json3_url(tracks: list[dict[str, Any]]) -> str | None:
    """From one language's track list, prefer the json3 entry."""
    for t in tracks:
        if t.get("ext") == "json3" and t.get("url"):
            return t["url"]
    return None


def pick_caption_track(
    subtitles: dict[str, list[dict[str, Any]]] | None,
    automatic: dict[str, list[dict[str, Any]]] | None,
) -> tuple[str, str, str] | None:
    """Pick the best caption track. Returns ``(url, lang, kind)`` or None.

    Tiers (mirrors the Bilibili fetcher's CC-first policy):
      1. manual zh-*      → kind "cc"
      2. manual any lang  → kind "cc"  (en preferred within the tier)
      3. automatic zh-*   → kind "ai"
      4. automatic en     → kind "ai"
    Only json3-format tracks are used; a language without a json3 entry
    is treated as unavailable (graceful degradation handles the rest).
    """
    subs = subtitles or {}
    autos = automatic or {}

    # Tier 1: manual zh
    for lang, tracks in subs.items():
        if _is_zh(lang):
            url = _pick_json3_url(tracks)
            if url:
                return url, lang, "cc"
    # Tier 2: manual any (en first for determinism)
    for lang in sorted(subs.keys(), key=lambda l: (not l.startswith("en"), l)):
        url = _pick_json3_url(subs[lang])
        if url:
            return url, lang, "cc"
    # Tier 3: automatic zh
    for lang, tracks in autos.items():
        if _is_zh(lang):
            url = _pick_json3_url(tracks)
            if url:
                return url, lang, "ai"
    # Tier 4: automatic en
    for lang in sorted(autos.keys(), key=lambda l: (not l.startswith("en"), l)):
        if lang.startswith("en"):
            url = _pick_json3_url(autos[lang])
            if url:
                return url, lang, "ai"
    return None


def json3_events_to_body(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """YouTube json3 ``{"events": [...]}`` → the shared cue shape
    ``[{"from": seconds, "content": text}]`` consumed by
    ``_subtitle.subtitle_body_to_markdown``."""
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return []
    body: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        segs = ev.get("segs")
        if not isinstance(segs, list):
            continue
        text = "".join(
            (s.get("utf8") or "") for s in segs if isinstance(s, dict)
        ).strip()
        if not text or text == "\n":
            continue
        start_ms = ev.get("tStartMs", 0)
        try:
            start = float(start_ms) / 1000.0
        except (TypeError, ValueError):
            start = 0.0
        body.append({"from": start, "content": text})
    return body


def _video_markdown(
    *,
    title: str,
    author: str | None,
    video_id: str,
    pubdate: datetime | None,
    duration_sec: int | None,
    description: str,
    subtitle_md: str | None,
    subtitle_lang: str,
    subtitle_kind: str,
) -> str:
    """Per-video markdown handed to the distiller (same skeleton as the
    Bilibili fetcher's `_video_to_markdown`: metadata header + 简介 +
    fenced subtitle block)."""
    pub_str = ""
    if pubdate is not None:
        pub_str = pubdate.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dur_str = ""
    if duration_sec is not None:
        dur_str = f"{duration_sec // 60}:{duration_sec % 60:02d}"

    header_lines = [
        f"# {title}",
        "",
        f"- 频道主: {author or '未知'}",
        f"- 视频 ID: {video_id}",
        f"- 发布: {pub_str}" if pub_str else "",
        f"- 时长: {dur_str}" if dur_str else "",
        f"- 字幕: {subtitle_lang or 'none'} ({subtitle_kind})",
        "",
    ]
    header = "\n".join(line for line in header_lines if line)

    parts = [header]
    if description.strip():
        parts.append("## 视频简介\n")
        parts.append(description.strip())
        parts.append("")
    if subtitle_md:
        parts.append("## 字幕 (YouTube 官方)\n")
        parts.append("```")
        parts.append(subtitle_md)
        parts.append("```")
    else:
        parts.append("## 字幕\n")
        parts.append("（无可用字幕,本条仅基于标题 + 简介生成）")
        parts.append("")
    return "\n".join(parts)


def _config_get(source: Source, *keys: str) -> str | None:
    cfg = source.config_json or {}
    for k in keys:
        v = cfg.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return None


# --- the fetcher ------------------------------------------------------


class YouTubeFetcher:
    """yt-dlp-backed YouTube fetcher — see module docstring for modes."""

    kind: SourceKind = SourceKind.youtube

    # YouTube is stricter with anonymous clients than B 站 — space the
    # per-video metadata calls out further than Bilibili's 0.5s.
    _INTER_VIDEO_SLEEP_SEC = 1.0

    def __init__(
        self,
        timeout: float = FETCH_TIMEOUT_SEC,
        inter_video_sleep: float = _INTER_VIDEO_SLEEP_SEC,
        max_videos_per_channel: int = 20,
    ) -> None:
        self._timeout = timeout
        self._sleep = inter_video_sleep
        self._max_videos = max(1, max_videos_per_channel)

    # -- yt-dlp plumbing ------------------------------------------------

    def _extract(self, url: str, *, flat: bool) -> dict[str, Any]:
        """Synchronous yt-dlp extract_info — always call via to_thread."""
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": self._timeout,
        }
        if flat:
            opts["extract_flat"] = "in_playlist"
            opts["playlistend"] = self._max_videos
        else:
            # We need the caption track listings.
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
        with _yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[union-attr]
            info = ydl.extract_info(url, download=False)
        return info if isinstance(info, dict) else {}

    # -- public ----------------------------------------------------------

    async def fetch(
        self, source: Source, *, lookback_days: int | None = None
    ) -> list[RawItem]:
        if _yt_dlp is None:
            raise FetchError("yt-dlp not installed", retryable=False)

        channel = _config_get(source, "channel")
        video = _config_get(source, "video")
        playlist = _config_get(source, "playlist")

        lookback = lookback_days if lookback_days is not None else FETCH_LOOKBACK_DAYS
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback)

        if channel:
            listing_url = parse_channel_ref(channel)
            if not listing_url:
                raise FetchError(
                    f"unrecognized channel ref: {channel!r}", retryable=False,
                )
            return await self._fetch_channel(
                listing_url=listing_url, source=source, cutoff=cutoff,
            )
        if video:
            video_id = parse_video_ref(video)
            if not video_id:
                raise FetchError(
                    f"unrecognized video ref: {video!r}", retryable=False,
                )
            raw = await self._build_raw_for_video(video_id=video_id, source=source)
            return [raw] if raw is not None else []
        if playlist:
            raise FetchError(
                "playlist-mode YouTube sources are not implemented yet",
                retryable=False,
            )
        raise FetchError(
            "config_json has no channel/video/playlist", retryable=False,
        )

    # -- channel mode ----------------------------------------------------

    async def _fetch_channel(
        self, *, listing_url: str, source: Source, cutoff: datetime,
    ) -> list[RawItem]:
        log.info("[youtube] channel mode: source=%s url=%s", source.id, listing_url)
        try:
            await _retry.throttle.wait(listing_url)
            page = await asyncio.to_thread(self._extract, listing_url, flat=True)
        except Exception as exc:  # noqa: BLE001
            # The listing call IS the source — its failure is whole-source.
            log.error("[youtube] listing %s failed: %s", listing_url, exc)
            raise FetchError(f"channel listing failed: {exc}") from exc

        entries = page.get("entries") or []
        if not isinstance(entries, list) or not entries:
            log.info("[youtube] channel %s has no videos", listing_url)
            return []

        raw_items: list[RawItem] = []
        for entry in entries[: self._max_videos]:
            if not isinstance(entry, dict):
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            try:
                raw = await self._build_raw_for_video(
                    video_id=str(video_id), source=source, fallback=entry,
                )
            except Exception as exc:  # noqa: BLE001
                # Per-video failure: skip, keep going (v0.2c contract).
                log.warning("[youtube] video %s failed: %s", video_id, exc)
                continue
            if raw is None:
                continue
            if raw.published_at < cutoff:
                # Listing is newest-first — everything after this one is
                # older still. This is where lookback_days actually bites
                # (unlike Bilibili, YouTube upload dates are reliable).
                log.info(
                    "[youtube] %s older than lookback cutoff; stopping",
                    video_id,
                )
                break
            raw_items.append(raw)
            if self._sleep > 0:
                await asyncio.sleep(self._sleep)

        log.info(
            "[youtube] channel %s: %d items built", listing_url, len(raw_items),
        )
        return raw_items

    # -- per-video builder -------------------------------------------------

    async def _build_raw_for_video(
        self,
        *,
        video_id: str,
        source: Source,
        fallback: dict[str, Any] | None = None,
    ) -> RawItem | None:
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        info: dict[str, Any] | None = None
        try:
            await _retry.throttle.wait(watch_url)
            info = await asyncio.to_thread(self._extract, watch_url, flat=False)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[youtube] extract_info(%s) failed: %s; falling back to list entry",
                video_id, exc,
            )
            info = None

        title = ""
        description = ""
        author: str | None = None
        pubdate: datetime | None = None
        duration_sec: int | None = None
        subtitles: dict[str, Any] = {}
        automatic: dict[str, Any] = {}

        if isinstance(info, dict) and info:
            title = (info.get("title") or "").strip()
            description = (info.get("description") or "").strip()
            author = info.get("uploader") or info.get("channel")
            pubdate = _upload_dt(info)
            dur = info.get("duration")
            duration_sec = int(dur) if isinstance(dur, (int, float)) else None
            subtitles = info.get("subtitles") or {}
            automatic = info.get("automatic_captions") or {}

        if fallback:
            if not title:
                title = (fallback.get("title") or "").strip()
            if author is None:
                author = fallback.get("uploader") or fallback.get("channel")
            if duration_sec is None:
                dur = fallback.get("duration")
                duration_sec = int(dur) if isinstance(dur, (int, float)) else None

        if not title:
            title = f"(无标题) {video_id}"

        # Subtitle: best-effort; degrade to title + description on failure.
        subtitle_md: str | None = None
        subtitle_lang = "none"
        subtitle_kind = "unknown"
        picked = pick_caption_track(subtitles, automatic)
        if picked is not None:
            track_url, subtitle_lang, subtitle_kind = picked
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=True,
                ) as client:

                    async def _get() -> dict[str, Any]:
                        await _retry.throttle.wait(track_url)
                        resp = await client.get(track_url)
                        resp.raise_for_status()
                        return resp.json()

                    payload = await retry_async(
                        _get, describe=f"[youtube] captions {video_id}",
                    )
                body = json3_events_to_body(payload)
                if body:
                    subtitle_md = subtitle_body_to_markdown(
                        body, cue_kind=subtitle_kind,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[youtube] caption download for %s failed: %s", video_id, exc,
                )
                subtitle_md = None

        markdown = _video_markdown(
            title=title,
            author=author,
            video_id=video_id,
            pubdate=pubdate,
            duration_sec=duration_sec,
            description=description,
            subtitle_md=subtitle_md,
            subtitle_lang=subtitle_lang,
            subtitle_kind=subtitle_kind,
        )

        return RawItem(
            url=watch_url,
            title=title,
            content=markdown,
            published_at=pubdate or datetime.now(timezone.utc),
            author=author,
            content_type=ContentType.video,
            duration_sec=duration_sec,
            metadata={
                "source_name": source.name,
                "video_id": video_id,
                "channel_id": (info or {}).get("channel_id"),
                "subtitle_lang": subtitle_lang,
                "subtitle_kind": subtitle_kind,
                "feed_kind": "youtube",
            },
        )


__all__ = [
    "YouTubeFetcher",
    "parse_video_ref",
    "parse_channel_ref",
    "pick_caption_track",
    "json3_events_to_body",
]
