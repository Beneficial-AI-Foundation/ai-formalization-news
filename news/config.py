"""Load config/sources.yaml and config/search_synonyms.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.yaml"
SYNONYMS_PATH = ROOT / "config" / "search_synonyms.yaml"

TYPES = ("paper", "lean", "news", "blog", "social")
KINDS = ("feed", "arxiv", "arxiv_rss", "github_releases", "hackernews", "hf_papers", "zulip")
FILTERS = ("none", "keywords")
EVERY = ("1h", "6h", "daily")

# Fields every source has; anything else in the YAML entry is kept in `params`.
_BASE_FIELDS = {"id", "name", "kind", "type", "url", "enabled", "filter", "every", "note"}


class ConfigError(ValueError):
    """Raised when config/sources.yaml is not valid."""


@dataclass
class Source:
    id: str
    name: str
    kind: str
    type: str
    url: str | None = None
    enabled: bool = False
    filter: str = "none"
    every: str = "1h"
    params: dict[str, Any] = field(default_factory=dict)

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


@dataclass
class ClaudeSettings:
    model: str = "claude-haiku-5-5"
    effort: str = "low"
    max_items_per_run: int = 100
    max_tokens: int = 16000
    retry_days: int = 3


@dataclass
class Config:
    sources: list[Source]
    keywords: list[str]
    claude: ClaudeSettings
    site: dict[str, str]

    @property
    def enabled_sources(self) -> list[Source]:
        return [s for s in self.sources if s.enabled]

    def source(self, source_id: str) -> Source:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)


def _parse_source(raw: dict[str, Any]) -> Source:
    missing = [k for k in ("id", "name", "kind", "type") if k not in raw]
    if missing:
        raise ConfigError(f"source {raw!r} is missing {', '.join(missing)}")
    src = Source(
        id=str(raw["id"]),
        name=str(raw["name"]),
        kind=str(raw["kind"]),
        type=str(raw["type"]),
        url=raw.get("url"),
        enabled=bool(raw.get("enabled", False)),
        filter=str(raw.get("filter", "none")),
        every=str(raw.get("every", "1h")),
        params={k: v for k, v in raw.items() if k not in _BASE_FIELDS},
    )
    if src.kind not in KINDS:
        raise ConfigError(f"source {src.id}: unknown kind {src.kind!r}")
    if src.type not in TYPES:
        raise ConfigError(f"source {src.id}: unknown type {src.type!r}")
    if src.filter not in FILTERS:
        raise ConfigError(f"source {src.id}: filter must be one of {FILTERS}")
    if src.every not in EVERY:
        raise ConfigError(f"source {src.id}: every must be one of {EVERY}")
    if src.kind in ("feed", "arxiv_rss", "hf_papers") and not src.url:
        raise ConfigError(f"source {src.id}: kind {src.kind} needs a url")
    if src.kind == "github_releases" and not src.param("repo"):
        raise ConfigError(f"source {src.id}: github_releases needs a repo")
    if src.param("exclude_titles"):
        try:
            re.compile(str(src.param("exclude_titles")))
        except re.error as e:
            raise ConfigError(f"source {src.id}: exclude_titles is not a valid regular expression: {e}") from e
    return src


def load_config(path: Path | str = CONFIG_PATH) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = [_parse_source(s) for s in data.get("sources") or []]
    ids = [s.id for s in sources]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConfigError(f"duplicate source ids: {sorted(dupes)}")
    claude_raw = data.get("claude") or {}
    claude = ClaudeSettings(**{k: v for k, v in claude_raw.items() if k in ClaudeSettings.__dataclass_fields__})
    return Config(
        sources=sources,
        keywords=[str(k) for k in data.get("keywords") or []],
        claude=claude,
        site={k: str(v) for k, v in (data.get("site") or {}).items()},
    )


def load_synonyms(path: Path | str = SYNONYMS_PATH) -> list[list[str]]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    groups = []
    for group in data.get("synonyms") or []:
        terms = [str(t).strip().lower() for t in group if str(t).strip()]
        if len(terms) >= 2:
            groups.append(terms)
    return groups
