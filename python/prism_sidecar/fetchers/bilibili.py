"""Bilibili fetcher — PoC scope (3 UP 主).

Supports three source identification modes via ``source.config_json``:

* ``{"mid": "339137722"}``     — UP 主投稿列表（按发布时间倒序拉 N 条）
* ``{"bvid": "BV1xxxxxxxxx"}`` — 单视频
* ``{"keyword": "..."}``      — PoC 范围不实现,留 TODO 给 v0.2c 全量

The per-video flow:

1. ``video.Video(bvid=...).get_info()`` 拿元信息 (title / desc / pubdate / mid)
2. ``video.Video(bvid=...).get_subtitle(cid=cid)`` 拿字幕轨道列表
3. 在多条字幕里挑最优:
   - 优先 CC 字幕 (UP 主上传的人工字幕, ``type == 1`` 或
     ``ai_type`` 不为 ``1``)
   - 退到 AI 字幕 (B 站自动生成的, ``ai_type == 1``)
   - 都没有 → 优雅降级,只返回标题 + 描述
4. 拼成 markdown: 标题 / UP 主 / 发布时间 / 时长 / 简介 / 字幕(带时间戳)

Anti-rate-limit: 每个视频之间 sleep 0.5s,UP 主列表里 page size 上限 30。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from prism_sidecar import _http
from prism_sidecar.config import FETCH_TIMEOUT_SEC
from prism_sidecar.fetchers import _retry
from prism_sidecar.fetchers._subtitle import format_timestamp, subtitle_body_to_markdown
from prism_sidecar.fetchers.base import FetchError, RawItem
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)

# Representative URL for the B 站 API host, used to key the shared
# per-host throttle (`_retry.throttle`). The bilibili_api library makes
# its own HTTP calls internally, so we can't throttle at the transport —
# instead we `wait()` on this host before every library call, which
# makes the process-wide bilibili.com interval (see _retry._HOST_INTERVALS)
# actually apply to this fetcher. Before v0.5.x the interval was
# configured but never enforced here — only the fetcher's own
# inter-video sleep stood between us and the rate limit.
_BILI_API_URL = "https://api.bilibili.com/"


# --- bilibili_api-python import guarded so tests can monkeypatch -----

try:  # pragma: no cover - exercised via tests with monkeypatch
    from bilibili_api import user as _bili_user
    from bilibili_api import video as _bili_video
except Exception:  # pragma: no cover
    _bili_user = None  # type: ignore[assignment]
    _bili_video = None  # type: ignore[assignment]


# --- module-level helpers (pure functions, easy to unit-test) ---------


def _pick_subtitle_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the best subtitle track from the player subtitle list.

    Preference:
    1. Human CC track (``type == 1``) whose ``lan`` looks Chinese (``zh*``).
    2. Any human CC track (English etc).
    3. AI track (``ai_type == 1``) whose ``lan`` is ``ai-zh``.
    4. Any AI track.

    Tracks look like::

        {
          "id": 123,
          "lan": "zh-CN",          # language
          "lan_doc": "中文（中国大陆）",
          "subtitle_url": "https://aisubtitle.hdslb.com/...json",
          "type": 1,               # 0 = AI, 1 = CC (human)
          "ai_type": 0,            # 0 = non-AI, 1 = AI
          ...
        }

    B 站历史上有过几次字段命名变更,所以两套字段都认。
    """
    if not tracks:
        return None

    def _is_zh(lan: str | None) -> bool:
        if not lan:
            return False
        lan_lower = lan.lower()
        return lan_lower.startswith("zh") or lan_lower == "ai-zh"

    def _is_human(t: dict[str, Any]) -> bool:
        # type==1 was the historical "CC" flag; ai_type==0 means
        # "not AI" on newer responses. Either being true marks human.
        return t.get("type") == 1 or t.get("ai_type") == 0

    def _is_ai(t: dict[str, Any]) -> bool:
        return t.get("type") == 0 and t.get("ai_type") == 1

    # Tier 1: human + zh
    for t in tracks:
        if _is_human(t) and _is_zh(t.get("lan")):
            return t
    # Tier 2: human (any lang)
    for t in tracks:
        if _is_human(t):
            return t
    # Tier 3: ai + zh
    for t in tracks:
        if _is_ai(t) and _is_zh(t.get("lan")):
            return t
    # Tier 4: any AI
    for t in tracks:
        if _is_ai(t):
            return t
    return None


def _classify_subtitle_source(tracks: list[dict[str, Any]], picked: dict[str, Any] | None) -> str:
    """Return the ``subtitle_source`` audit tag for the logs / metadata.

    Possible values:
    - ``"cc+ai"``        — both kinds exist, picked one of them (the actual
                            provenance is in ``picked``; the value reflects
                            availability, not the choice)
    - ``"cc_only"``      — only CC tracks, picked one
    - ``"ai_only"``      — only AI tracks, picked one
    - ``"none"``         — no tracks at all, or fetch failed

    The picked track's actual provenance (``cc`` / ``ai``) is captured
    separately in ``subtitle_kind`` for downstream debugging.
    """
    if not tracks or picked is None:
        return "none"
    has_human = any(
        t.get("type") == 1 or t.get("ai_type") == 0 for t in tracks
    )
    has_ai = any(
        t.get("type") == 0 and t.get("ai_type") == 1 for t in tracks
    )
    if has_human and has_ai:
        return "cc+ai"
    if has_human:
        return "cc_only"
    return "ai_only"


def _pick_subtitle_kind(picked: dict[str, Any] | None) -> str:
    """What the picked track actually is: ``"cc"`` / ``"ai"`` / ``"unknown"``."""
    if picked is None:
        return "unknown"
    if picked.get("type") == 1 or picked.get("ai_type") == 0:
        return "cc"
    if picked.get("type") == 0 and picked.get("ai_type") == 1:
        return "ai"
    return "unknown"


def _subtitle_url(picked: dict[str, Any]) -> str | None:
    """The JSON subtitle URL on the picked track.

    Some track payloads have a relative path (no ``https://`` prefix).
    B 站's docs say ``subtitle_url`` always includes the protocol, but
    we defend against the legacy relative-path case anyway.
    """
    raw = picked.get("subtitle_url") or ""
    if not raw:
        return None
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return "https://aisubtitle.hdslb.com" + raw
    return raw


# v0.2c: moved to `fetchers/_subtitle.py` so the YouTube fetcher shares
# the exact same cue format (the [CC]/[AI] line shape is a contract with
# distillers/bilibili_prompt.py). Re-bound here under the old private
# names so existing tests / callers keep working.
_format_timestamp = format_timestamp
_subtitle_body_to_markdown = subtitle_body_to_markdown


def _video_to_markdown(
    *,
    title: str,
    author: str | None,
    bvid: str,
    pubdate: datetime | None,
    duration_sec: int | None,
    description: str,
    subtitle_md: str | None,
    subtitle_source: str,
    subtitle_kind: str,
) -> str:
    """Compose the per-video markdown handed to the distiller.

    Subtitle goes at the bottom in a fenced block so the LLM doesn't get
    confused by the metadata header.
    """
    pub_str = ""
    if pubdate is not None:
        pub_str = pubdate.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dur_str = ""
    if duration_sec is not None:
        dur_str = f"{duration_sec // 60}:{duration_sec % 60:02d}"

    header_lines = [
        f"# {title}",
        "",
        f"- UP 主: {author or '未知'}",
        f"- BV 号: {bvid}",
        f"- 发布: {pub_str}" if pub_str else "",
        f"- 时长: {dur_str}" if dur_str else "",
        f"- 字幕: {subtitle_source} ({subtitle_kind})",
        "",
    ]
    header = "\n".join(line for line in header_lines if line is not None)

    body_parts = [header]
    if description.strip():
        body_parts.append("## 视频简介\n")
        body_parts.append(description.strip())
        body_parts.append("")
    if subtitle_md:
        body_parts.append("## 字幕 (B 站官方)\n")
        body_parts.append("```")
        body_parts.append(subtitle_md)
        body_parts.append("```")
    else:
        body_parts.append("## 字幕\n")
        body_parts.append("（无可用字幕,本条仅基于标题 + 简介生成）")
        body_parts.append("")
    return "\n".join(body_parts)


def _ts_to_dt(epoch_seconds: int | float | None) -> datetime | None:
    """B 站 pubdate / ctime 都是 Unix 秒,统一转 UTC datetime。"""
    if epoch_seconds is None:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _parse_duration(value: Any) -> int | None:
    """Normalize a duration value to seconds.

    B 站 surfaces durations in two shapes:
    - ``get_info`` returns ``duration`` as an int (seconds).
    - ``get_videos`` vlist returns ``length`` as ``"MM:SS"`` or ``"HH:MM:SS"``.
    Either is acceptable input; we always return ``int`` seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        # "HH:MM:SS" or "MM:SS"
        parts = s.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:
            h, m, sec = nums
        elif len(nums) == 2:
            h, m, sec = 0, nums[0], nums[1]
        else:
            return None
        return h * 3600 + m * 60 + sec
    return None


def _config_get(source: Source, *keys: str) -> str | None:
    """Read a string field from ``source.config_json``.

    The fixture / store may have keys at any depth; we only ever read
    top-level string fields, so this stays simple.
    """
    cfg = source.config_json or {}
    for k in keys:
        v = cfg.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return None


# --- the fetcher ------------------------------------------------------


class BilibiliFetcher:
    """PoC Bilibili fetcher — see module docstring for the modes."""

    kind: SourceKind = SourceKind.bilibili

    # Don't hammer B 站 — 0.5s between videos keeps us well under the
    # rate limit on un-authed calls (B 站 un-authed throttle is ~1 req/s
    # for the player info endpoint).
    _INTER_VIDEO_SLEEP_SEC = 0.5

    def __init__(
        self,
        timeout: float = FETCH_TIMEOUT_SEC,
        inter_video_sleep: float = _INTER_VIDEO_SLEEP_SEC,
        videos_per_page: int = 20,
        max_videos_per_up: int = 20,
    ) -> None:
        self._timeout = timeout
        self._sleep = inter_video_sleep
        self._ps = max(1, min(30, videos_per_page))
        self._max_videos = max(1, max_videos_per_up)

    # -- public --------------------------------------------------------

    async def fetch(self, source: Source, **_: Any) -> list[RawItem]:
        """Dispatch on the config_json shape and return RawItems.

        ``**kwargs`` swallows the ``lookback_days`` the pipeline passes
        to every fetcher — B 站 doesn't have a meaningful lookback
        (we either fetch a UP's first page, or a specific bvid), so we
        silently ignore it. (RSSFetcher, by contrast, does use
        ``lookback_days`` to bound its cutoff — see ``fetchers/rss.py``.)

        v0.2c contract: unrecoverable whole-source errors (mode missing,
        lib not installed, listing endpoint dead) raise ``FetchError``;
        per-video errors are skipped so one broken video doesn't sink
        the UP 主's whole sync.
        """
        if _bili_user is None or _bili_video is None:
            raise FetchError(
                "bilibili-api-python not installed", retryable=False,
            )

        mid = _config_get(source, "mid")
        bvid = _config_get(source, "bvid")
        keyword = _config_get(source, "keyword")

        try:
            if mid:
                return await self._fetch_up(mid=mid, source=source)
            if bvid:
                return await self._fetch_one(bvid=bvid, source=source)
            if keyword:
                # PoC 范围外:keyword 搜索留 TODO。Non-retryable so the
                # cooldown logic doesn't hammer an unimplemented mode.
                raise FetchError(
                    "keyword-mode Bilibili sources are not implemented yet",
                    retryable=False,
                )
            raise FetchError(
                "config_json has no mid/bvid/keyword", retryable=False,
            )
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "[bilibili] source %s fetch raised: %s", source.id, exc,
            )
            raise FetchError(f"unexpected error: {exc!r}") from exc

    # -- mid mode (UP 主投稿列表) ------------------------------------

    async def _fetch_up(self, *, mid: str, source: Source) -> list[RawItem]:
        log.info("[bilibili] UP mode: source=%s mid=%s", source.id, mid)
        try:
            await _retry.throttle.wait(_BILI_API_URL)
            u = _bili_user.User(uid=int(mid))  # type: ignore[union-attr]
            page = await u.get_videos(pn=1, ps=self._ps)
        except Exception as exc:  # noqa: BLE001
            # The listing call IS the source — its failure is whole-source.
            log.error(
                "[bilibili] get_videos(mid=%s) failed: %s", mid, exc,
            )
            raise FetchError(f"get_videos(mid={mid}) failed: {exc}") from exc

        # `page` is the B 站 response shape.
        #
        # bilibili-api-python v17+ already auto-unwraps the envelope
        # (`{"code": 0, "message": "0", "ttl": 1, "data": {...}}` →
        # `{...}`), so we get the inner ``data`` block directly:
        #   {"list": {"vlist": [video_dict, ...]}, "page": {...}}
        #
        # We still defend against the raw envelope in case a future
        # lib version stops unwrapping.
        if isinstance(page, dict) and "data" in page and isinstance(page["data"], dict):
            data = page["data"]
        elif isinstance(page, dict):
            data = page
        else:
            # Non-dict response = API shape changed = whole-source problem.
            log.warning("[bilibili] get_videos(mid=%s) returned no data: %r", mid, page)
            raise FetchError(f"get_videos(mid={mid}) returned unexpected shape")
        if not data:
            log.warning("[bilibili] get_videos(mid=%s) returned no data: %r", mid, page)
            raise FetchError(f"get_videos(mid={mid}) returned empty data")
        list_block = data.get("list") or {}
        vlist = list_block.get("vlist") or []
        if not isinstance(vlist, list) or not vlist:
            log.info("[bilibili] UP mid=%s has no videos", mid)
            return []

        # Cap to most-recent N — keeps PoC sync bounded.
        vlist = list(vlist)[: self._max_videos]
        log.info(
            "[bilibili] UP mid=%s: %d candidate videos (capped at %d)",
            mid, len(vlist), self._max_videos,
        )

        raw_items: list[RawItem] = []
        for entry in vlist:
            bvid = entry.get("bvid")
            if not bvid:
                continue
            try:
                raw = await self._build_raw_for_bvid(
                    bvid=bvid, source=source,
                    fallback_info=entry,
                )
                if raw is not None:
                    raw_items.append(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[bilibili] UP mid=%s video %s failed: %s",
                    mid, bvid, exc,
                )
            # Be polite to B 站 — including after a FAILED video (the
            # pre-fix `if raw_items and …` guard skipped the sleep
            # whenever the first video errored, which is exactly when
            # backing off matters most).
            if self._sleep > 0:
                await asyncio.sleep(self._sleep)

        log.info(
            "[bilibili] UP mid=%s: %d items built", mid, len(raw_items),
        )
        return raw_items

    # -- bvid mode (单视频) -------------------------------------------

    async def _fetch_one(self, *, bvid: str, source: Source) -> list[RawItem]:
        log.info("[bilibili] bvid mode: source=%s bvid=%s", source.id, bvid)
        raw = await self._build_raw_for_bvid(
            bvid=bvid, source=source, fallback_info=None,
        )
        return [raw] if raw is not None else []

    # -- per-video builder (shared between mid / bvid modes) ----------

    async def _build_raw_for_bvid(
        self,
        *,
        bvid: str,
        source: Source,
        fallback_info: dict[str, Any] | None,
    ) -> RawItem | None:
        """Build one RawItem from a single bvid.

        ``fallback_info`` is the cheap entry returned by ``get_videos``
        for an UP's 投稿列表 — we use it as a free pubdate / author / title
        when ``get_info`` fails, so we still produce a row instead of
        dropping the video entirely.
        """
        # Step 1: get_info — title / desc / pubdate / cid / duration.
        info: dict[str, Any] | None = None
        try:
            await _retry.throttle.wait(_BILI_API_URL)
            v = _bili_video.Video(bvid=bvid)  # type: ignore[union-attr]
            info = await v.get_info()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[bilibili] get_info(%s) failed: %s; falling back to list entry",
                bvid, exc,
            )
            info = None

        title, description, author, pubdate, duration_sec, cid = (
            _extract_info_fields(info, fallback_info, bvid)
        )

        # Step 2: subtitle — best-effort; on failure, log + fall through.
        subtitle_md: str | None = None
        subtitle_source = "none"
        subtitle_kind = "unknown"
        subtitle_track_url: str | None = None
        tracks: list[dict[str, Any]] = []

        if cid is not None and info is not None:
            try:
                # get_subtitle returns the full player_info dict
                # ``{"subtitles": [...], "subtitles_info": ..., ...}`` —
                # we only want the ``subtitles`` list.
                await _retry.throttle.wait(_BILI_API_URL)
                raw_tracks = await _bili_video.Video(bvid=bvid).get_subtitle(cid=cid)  # type: ignore[union-attr]
                if isinstance(raw_tracks, dict):
                    tracks = raw_tracks.get("subtitles") or []
                elif isinstance(raw_tracks, list):
                    tracks = raw_tracks
                else:
                    tracks = []
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[bilibili] get_subtitle(%s, cid=%s) failed: %s",
                    bvid, cid, exc,
                )
                tracks = []

            picked = _pick_subtitle_track(tracks)
            subtitle_source = _classify_subtitle_source(tracks, picked)
            subtitle_kind = _pick_subtitle_kind(picked)
            subtitle_track_url = _subtitle_url(picked) if picked else None

            if subtitle_track_url:
                try:
                    # Shared client + per-host throttle (hdslb.com is in
                    # _retry._HOST_INTERVALS) instead of a fresh client
                    # per video.
                    client = _http.get_client()
                    await _retry.throttle.wait(subtitle_track_url)
                    resp = await client.get(
                        subtitle_track_url,
                        timeout=self._timeout,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                                "10_15_7) AppleWebKit/537.36"
                            ),
                        },
                    )
                    resp.raise_for_status()
                    body_json = resp.json()
                    body = body_json.get("body") if isinstance(body_json, dict) else None
                    if isinstance(body, list):
                        # tag every cue with the picked track's provenance
                        # ([CC] / [AI]) so the distiller can split them.
                        subtitle_md = _subtitle_body_to_markdown(
                            body, cue_kind=subtitle_kind,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "[bilibili] download subtitle json for %s failed: %s",
                        bvid, exc,
                    )
                    # Keep tracks / source classification — just drop the body.
                    subtitle_md = None
        else:
            log.debug(
                "[bilibili] %s: no cid or info; skipping subtitle fetch",
                bvid,
            )

        markdown = _video_to_markdown(
            title=title,
            author=author,
            bvid=bvid,
            pubdate=pubdate,
            duration_sec=duration_sec,
            description=description,
            subtitle_md=subtitle_md,
            subtitle_source=subtitle_source,
            subtitle_kind=subtitle_kind,
        )

        return RawItem(
            url=f"https://www.bilibili.com/video/{bvid}",
            title=title,
            content=markdown,
            published_at=pubdate or datetime.now(timezone.utc),
            author=author,
            content_type=ContentType.video,
            duration_sec=duration_sec,
            metadata={
                "source_name": source.name,
                "bvid": bvid,
                "up_mid": (info or {}).get("owner", {}).get("mid") if isinstance(info, dict) else None,
                "subtitle_source": subtitle_source,
                "subtitle_kind": subtitle_kind,
                "subtitle_track_count": len(tracks),
                "feed_kind": "bilibili",
            },
        )


def _extract_info_fields(
    info: dict[str, Any] | None,
    fallback: dict[str, Any] | None,
    bvid: str,
) -> tuple[str, str, str | None, datetime | None, int | None, int | None]:
    """Pull the fields we need from ``get_info``, falling back to the
    cheap ``vlist`` entry when ``get_info`` failed.

    Returns: (title, description, author, pubdate, duration_sec, cid).
    """
    title = ""
    description = ""
    author: str | None = None
    pubdate: datetime | None = None
    duration_sec: int | None = None
    cid: int | None = None

    if isinstance(info, dict):
        title = (info.get("title") or "").strip()
        description = (info.get("desc") or "").strip()
        owner = info.get("owner") or {}
        if isinstance(owner, dict):
            author = owner.get("name")
        pubdate = _ts_to_dt(info.get("pubdate")) or _ts_to_dt(info.get("ctime"))
        duration_sec = _parse_duration(info.get("duration"))
        # Multi-part videos (合集 / 分P) → first part's cid.
        pages = info.get("pages") or []
        if isinstance(pages, list) and pages:
            first = pages[0]
            if isinstance(first, dict):
                cid = first.get("cid")

    # Fallback from get_videos's list entry (cheap preview data).
    if fallback:
        if not title:
            title = (fallback.get("title") or "").strip()
        if not author:
            author = fallback.get("author")
        if pubdate is None:
            pubdate = _ts_to_dt(fallback.get("created"))
        if duration_sec is None:
            # ``length`` here is a "MM:SS" string, not seconds.
            duration_sec = _parse_duration(fallback.get("length"))

    if not title:
        title = f"(无标题) {bvid}"

    return title, description, author, pubdate, duration_sec, cid


__all__ = ["BilibiliFetcher", "_pick_subtitle_track", "_classify_subtitle_source",
           "_pick_subtitle_kind", "_subtitle_body_to_markdown", "_video_to_markdown",
           "_format_timestamp"]