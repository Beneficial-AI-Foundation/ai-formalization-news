"""Build the Pages artifact (_site/) and, unless --build-only, serve it on localhost.

    python -m news.serve                     # build _site/ and serve at http://127.0.0.1:8000/
    python -m news.serve --port 8080
    python -m news.serve --data-dir DIR      # use the data in DIR instead of data/
    python -m news.serve --build-only        # what the workflow runs before uploading _site/
"""

from __future__ import annotations

import argparse
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from news.config import ROOT

# The page reads only these; seen.json, http_cache.json and the monthly archive stay out.
PUBLIC_DATA = ("news.json", "meta.json", "search.json")
# Copied to the top of the site, so its address is /feed.xml.
FEED = "feed.xml"
# Written into every built site; an existing directory is replaced only if it has one.
MARKER = ".baif-news-build"


class UnsafeOutput(ValueError):
    """The output directory could hold something other than a previous build."""


def _check_output(out: Path, root: Path, protected: list[Path]) -> None:
    if out.is_symlink():
        raise UnsafeOutput(f"{out} is a symbolic link")
    target = out.resolve()
    root = root.resolve()
    if target == root or target in root.parents:
        raise UnsafeOutput(f"{out} is or contains the repository")
    for p in protected:
        p = p.resolve()
        if target == p or target in p.parents or p in target.parents:
            raise UnsafeOutput(f"{out} overlaps {p}")
    if target.exists() and not (target / MARKER).exists() and any(target.iterdir()):
        raise UnsafeOutput(f"{out} exists, is not empty, and was not built by this tool")


def build_site(out: Path, site_dir: Path = ROOT / "site", data_dir: Path = ROOT / "data") -> Path:
    """Copy site/, the public data files and the feed into `out`, replacing an earlier build.

    Refuses to touch the repository root, site/, data/, anything containing or
    inside them, symbolic links, and non-empty directories it did not build.
    """
    _check_output(out, ROOT, [site_dir, data_dir])
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site_dir, out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_DATA:
        src = data_dir / name
        if src.exists():
            shutil.copy2(src, out / "data" / name)
    if (data_dir / FEED).exists():
        shutil.copy2(data_dir / FEED, out / FEED)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / MARKER).write_text("Built by python -m news.serve; safe to delete.\n", encoding="utf-8")
    return out


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "_site"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--data-dir", default=str(ROOT / "data"), help="where the data files live (default: data/)")
    args = ap.parse_args(argv)
    out = build_site(Path(args.out), data_dir=Path(args.data_dir))
    print(f"Built {out}")
    if args.build_only:
        return 0
    server = ThreadingHTTPServer((args.host, args.port), partial(_Handler, directory=str(out)))
    print(f"Serving at http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
