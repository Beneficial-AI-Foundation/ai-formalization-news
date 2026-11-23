"""URL normalisation, item ids, and the seen store (data/seen.json)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SEEN_ID_DAYS = 90
SEEN_TITLE_DAYS = 7

ARXIV_ID = r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
_ARXIV_PATH = re.compile(r"^/(?:abs|pdf|html)/" + ARXIV_ID + r"(?:\.pdf)?/?$")
_HF_PAPER_PATH = re.compile(r"^/papers/(?P<id>\d{4}\.\d{4,5})/?$")


def arxiv_id_from_url(url: str) -> str | None:
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    if host in _ARXIV_HOSTS:
        m = _ARXIV_PATH.match(p.path)
        return m.group("id") if m else None
    if host in ("huggingface.co", "www.huggingface.co"):
        m = _HF_PAPER_PATH.match(p.path)
        return m.group("id") if m else None
    return None


def _keep_fragment(host: str, fragment: str) -> bool:
    """Zulip addresses messages in the fragment (#narrow/...); dropping it would
    collapse every announcement to the server's home page."""
    return host.endswith(".zulipchat.com") and fragment.startswith("narrow/")


def normalize_url(url: str) -> str:
    """Lower-case scheme and host, drop utm_* parameters and the fragment (except
    Zulip message fragments), map arXiv links (abs, pdf, html, any version,
    Hugging Face paper pages) to https://arxiv.org/abs/<id>, and drop a trailing slash."""
    url = url.strip()
    aid = arxiv_id_from_url(url)
    if aid:
        return f"https://arxiv.org/abs/{aid}"
    p = urlsplit(url)
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")])
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    fragment = p.fragment if _keep_fragment(host, p.fragment) else ""
    return urlunsplit((scheme, host, path, query, fragment))


def make_id(url: str) -> str:
    norm = normalize_url(url)
    aid = arxiv_id_from_url(norm)
    if aid:
        return f"arxiv:{aid}"
    return "u:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def title_key(title: str) -> str | None:
    """Hash of the normalised title, or None for titles too short to compare safely."""
    norm = normalize_title(title)
    if len(norm) < 16 or len(norm.split()) < 3:
        return None
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


class SeenStore:
    """What the job has already handled.

    ids:     every item id with a final decision, kept 90 days.
    titles:  title keys of items finally kept, kept 7 days (for cross-source dedup).
    refused: ids Claude refused to judge, so they are not sent again (90 days).
    alts:    items held back because their title matches an item still waiting
             for Claude, keyed by that item's id. If Claude rejects it, the next
             alternative is released instead of being lost.
    """

    def __init__(
        self,
        ids: dict[str, str] | None = None,
        titles: dict[str, str] | None = None,
        refused: dict[str, str] | None = None,
        alts: dict[str, list[dict]] | None = None,
    ):
        self.ids = ids or {}
        self.titles = titles or {}
        self.refused = refused or {}
        self.alts = alts or {}

    @classmethod
    def load(cls, path: Path) -> "SeenStore":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            ids=data.get("ids") or {},
            titles=data.get("titles") or {},
            refused=data.get("refused") or {},
            alts=data.get("alts") or {},
        )

    def has_id(self, item_id: str) -> bool:
        return item_id in self.ids

    def has_title(self, title: str, now: datetime) -> bool:
        key = title_key(title)
        if not key or key not in self.titles:
            return False
        return self.titles[key] >= (now - timedelta(days=SEEN_TITLE_DAYS)).strftime("%Y-%m-%d")

    def add(self, item_id: str, now: datetime) -> None:
        self.ids.setdefault(item_id, now.strftime("%Y-%m-%d"))

    def add_title(self, title: str, now: datetime) -> None:
        key = title_key(title)
        if key:
            self.titles[key] = now.strftime("%Y-%m-%d")

    def hold_alt(self, rep_id: str, item: dict) -> None:
        held = self.alts.setdefault(rep_id, [])
        if all(a["id"] != item["id"] for a in held):
            held.append(item)

    def prune(self, now: datetime, waiting_ids: set[str] | None = None) -> None:
        """Expire old entries. Alternatives are dropped once their representative
        is no longer waiting for Claude (it was kept, or its retry window passed)."""
        id_cut = (now - timedelta(days=SEEN_ID_DAYS)).strftime("%Y-%m-%d")
        title_cut = (now - timedelta(days=SEEN_TITLE_DAYS)).strftime("%Y-%m-%d")
        self.ids = {k: v for k, v in self.ids.items() if v >= id_cut}
        self.titles = {k: v for k, v in self.titles.items() if v >= title_cut}
        self.refused = {k: v for k, v in self.refused.items() if v >= id_cut}
        if waiting_ids is not None:
            self.alts = {k: v for k, v in self.alts.items() if k in waiting_ids and v}

    def to_json(self) -> dict:
        return {
            "version": 1,
            "ids": dict(sorted(self.ids.items())),
            "titles": dict(sorted(self.titles.items())),
            "refused": dict(sorted(self.refused.items())),
            "alts": dict(sorted(self.alts.items())),
        }
