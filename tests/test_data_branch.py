"""The data branch: the workflow's git script and the site build from a data checkout.

The script tests run real git against a local bare repository standing in for
GitHub, with shallow clones like the ones actions/checkout makes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from news.config import ROOT
from news.serve import build_site

SCRIPT = ROOT / ".github" / "scripts" / "data_branch.sh"
BRANCH = "data"
DIR = "data-branch"

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="needs git and bash")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", DATA_BRANCH_RETRY_SECONDS="0",
               GIT_TERMINAL_PROMPT="0")
    return env


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", *args], cwd=cwd,
                          env=_env(), check=True, capture_output=True, text=True).stdout.strip()


def script(cwd: Path, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), cmd, BRANCH, DIR], cwd=cwd, env=_env(), capture_output=True, text=True)


@pytest.fixture
def origin(tmp_path) -> Path:
    """A bare repository with one commit on main, like the GitHub repository."""
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "--quiet", str(bare), str(seed))
    (seed / "README.md").write_text("code\n")
    git(seed, "add", "README.md")
    git(seed, "commit", "--quiet", "-m", "Initial commit")
    git(seed, "push", "--quiet", "origin", "HEAD:main")
    return bare


def checkout(tmp_path: Path, origin: Path, name: str) -> Path:
    """A fresh shallow clone of main, then the script's checkout step."""
    work = tmp_path / name
    git(tmp_path, "clone", "--quiet", "--depth", "1", "--branch", "main", origin.as_uri(), str(work))
    res = script(work, "checkout")
    assert res.returncode == 0, res.stderr + res.stdout
    return work


def remote_files(origin: Path) -> dict[str, str]:
    names = git(origin, "ls-tree", "-r", "--name-only", BRANCH).splitlines()
    return {n: git(origin, "show", f"{BRANCH}:{n}") for n in names}


def write(work: Path, name: str, value) -> None:
    path = work / DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def test_first_run_starts_the_branch_and_later_runs_build_on_it(tmp_path, origin):
    main_before = git(origin, "rev-parse", "main")

    work = checkout(tmp_path, origin, "run1")
    assert sorted(p.name for p in (work / DIR).iterdir() if p.name != ".git") == [".gitignore", "README.md"]
    write(work, "news.json", {"items": [1]})
    write(work, "archive/2026-11.json", {"items": [1]})
    write(work, "meta.json", {"run": 1})
    write(work, "http_cache.json", {"etag": "x"})
    res = script(work, "commit")
    assert res.returncode == 0, res.stderr
    files = remote_files(origin)
    assert set(files) == {".gitignore", "README.md", "news.json", "archive/2026-11.json", "meta.json"}
    assert files[".gitignore"] == "http_cache.json"
    # The data branch has its own history and main is untouched.
    assert git(origin, "rev-list", "--count", BRANCH) == "1"
    assert git(origin, "rev-parse", "main") == main_before
    assert "Update news data" in git(origin, "log", "-1", "--format=%s %an", BRANCH)

    # The next run starts from what the first one pushed.
    work2 = checkout(tmp_path, origin, "run2")
    assert json.loads((work2 / DIR / "news.json").read_text()) == {"items": [1]}

    # Only meta.json and the HTTP cache changed: no commit.
    write(work2, "meta.json", {"run": 2})
    write(work2, "http_cache.json", {"etag": "y"})
    res = script(work2, "commit")
    assert res.returncode == 0 and "nothing to commit" in res.stdout
    assert git(origin, "rev-list", "--count", BRANCH) == "1"

    # A real change is committed, with meta.json alongside it.
    write(work2, "news.json", {"items": [1, 2]})
    res = script(work2, "commit")
    assert res.returncode == 0, res.stderr
    files = remote_files(origin)
    assert json.loads(files["news.json"]) == {"items": [1, 2]}
    assert json.loads(files["meta.json"]) == {"run": 2}
    assert "http_cache.json" not in files
    assert git(origin, "rev-list", "--count", BRANCH) == "2"
    assert git(origin, "rev-parse", "main") == main_before


def _edit_by_hand(tmp_path: Path, origin: Path, name: str, value) -> None:
    hand = tmp_path / "hand"
    git(tmp_path, "clone", "--quiet", "--branch", BRANCH, origin.as_uri(), str(hand))
    (hand / name).parent.mkdir(parents=True, exist_ok=True)
    (hand / name).write_text(json.dumps(value) + "\n")
    git(hand, "commit", "--quiet", "-am", "Edit by hand")
    git(hand, "push", "--quiet", "origin", BRANCH)


def _seed_data(tmp_path: Path, origin: Path) -> None:
    work = checkout(tmp_path, origin, "seed-run")
    write(work, "news.json", {"items": [1]})
    write(work, "archive/2026-11.json", {"items": [1]})
    assert script(work, "commit").returncode == 0


def test_an_edit_by_hand_during_a_run_is_kept(tmp_path, origin):
    _seed_data(tmp_path, origin)
    work = checkout(tmp_path, origin, "run")
    _edit_by_hand(tmp_path, origin, "archive/2026-11.json", {"items": []})  # pushed while the run is going
    write(work, "news.json", {"items": [1, 2]})
    res = script(work, "commit")
    assert res.returncode == 0, res.stderr
    files = remote_files(origin)
    assert json.loads(files["archive/2026-11.json"]) == {"items": []}
    assert json.loads(files["news.json"]) == {"items": [1, 2]}


def test_a_conflicting_edit_stops_the_run_with_an_error(tmp_path, origin):
    _seed_data(tmp_path, origin)
    work = checkout(tmp_path, origin, "run")
    _edit_by_hand(tmp_path, origin, "news.json", {"items": ["by hand"]})
    write(work, "news.json", {"items": [1, 2]})
    res = script(work, "commit")
    assert res.returncode == 1
    assert "Could not replay the data commit" in res.stdout
    assert json.loads(remote_files(origin)["news.json"]) == {"items": ["by hand"]}


def test_build_site_takes_data_and_feed_from_the_data_checkout(tmp_path):
    data = tmp_path / "data-branch"
    (data / "archive").mkdir(parents=True)
    for name in ("news.json", "meta.json", "search.json", "seen.json", "http_cache.json", "archive/2026-11.json"):
        (data / name).write_text("{}\n")
    (data / "feed.xml").write_text("<feed/>\n")
    out = build_site(tmp_path / "_site", data_dir=data)
    assert (out / "feed.xml").read_text() == "<feed/>\n"  # served at /feed.xml, as the page links it
    assert sorted(p.name for p in (out / "data").iterdir()) == ["meta.json", "news.json", "search.json"]
    assert (out / "index.html").exists()
