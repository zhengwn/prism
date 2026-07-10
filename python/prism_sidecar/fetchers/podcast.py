"""Podcast fetcher — v0.2c (RSS variant with enclosure / duration).

播客 feed 就是带 iTunes 扩展的 RSS，所以这里直接继承 ``RSSFetcher``
（下载 / 重试 / 解析 / lookback 全部复用），只覆写单条 entry 的
构建钩子 ``_entry_to_raw``：

* enclosure → ``metadata.audio_url`` / ``audio_type``（音频直链）
* ``itunes:duration`` → ``RawItem.duration_sec``（"HH:MM:SS" / "MM:SS" /
  纯秒数三种形态都认）
* ``itunes:episode`` / ``itunes:season`` → metadata（有就带上）
* ``content_type`` = audio，``feed_kind`` = "podcast"

Show notes（entry 的 content/summary）就是喂给 distiller 的正文——
播客没有字幕可拉（转写留给未来的 whisper 集成），show notes 是
现阶段信息密度最高的可用文本。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from prism_sidecar.fetchers.base import RawItem
from prism_sidecar.fetchers.rss import RSSFetcher
from prism_sidecar.models import ContentType, Source, SourceKind

log = logging.getLogger(__name__)


def parse_itunes_duration(value: Any) -> int | None:
    """``"HH:MM:SS"`` / ``"MM:SS"`` / ``"3725"`` / ``3725`` → seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
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


def _entry_enclosure(entry: Any) -> tuple[str | None, str | None]:
    """First audio enclosure's (href, mime type). Audio preferred; if no
    enclosure is typed as audio, fall back to the first one at all."""
    enclosures = getattr(entry, "enclosures", None) or []
    first_href: str | None = None
    first_type: str | None = None
    for enc in enclosures:
        href = enc.get("href") if isinstance(enc, dict) else getattr(enc, "href", None)
        etype = enc.get("type") if isinstance(enc, dict) else getattr(enc, "type", None)
        if not href:
            continue
        if first_href is None:
            first_href, first_type = str(href), (str(etype) if etype else None)
        if etype and str(etype).startswith("audio/"):
            return str(href), str(etype)
    return first_href, first_type


class PodcastFetcher(RSSFetcher):
    """RSS-variant fetcher for podcast feeds. Inherits the whole
    download/parse/lookback pipeline (and the v0.2c FetchError contract)
    from RSSFetcher; only the per-entry build differs."""

    kind: SourceKind = SourceKind.podcast

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

        audio_url, audio_type = _entry_enclosure(entry)
        duration_sec = parse_itunes_duration(getattr(entry, "itunes_duration", None))

        raw.content_type = ContentType.audio
        raw.duration_sec = duration_sec
        raw.metadata["feed_kind"] = "podcast"
        if audio_url:
            raw.metadata["audio_url"] = audio_url
        if audio_type:
            raw.metadata["audio_type"] = audio_type
        episode = getattr(entry, "itunes_episode", None)
        if episode:
            raw.metadata["episode"] = str(episode)
        season = getattr(entry, "itunes_season", None)
        if season:
            raw.metadata["season"] = str(season)

        if not audio_url:
            # Not fatal — some feeds put teaser posts in the same feed.
            # Keep the item (show notes still distill fine) but log it.
            log.debug("[podcast] %s: entry %s has no enclosure", source.name, link)
        return raw


__all__ = ["PodcastFetcher", "parse_itunes_duration"]
