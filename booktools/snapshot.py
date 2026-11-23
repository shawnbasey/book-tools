"""Commit a snapshot of the working tree to a side branch without touching the checkout."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _git(root: Path, *args: str, env: dict | None = None, input_text: str | None = None):
    return subprocess.run(["git", *args], cwd=root, env=env, input=input_text, capture_output=True, text=True)


def git_snapshot(root: Path, label: str, stats: dict, branch: str = "builds") -> str | None:
    """Commit working tree + build-manifest.json to `branch` via a temporary index.

    The checked-out branch, the real index, and working files are not modified.
    Returns the new commit SHA, or None if skipped.
    """
    if not shutil.which("git") or _git(root, "rev-parse", "--git-dir").returncode != 0:
        print("Snapshot skipped: not a Git repository.")
        return None
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "snapshot.index"))
        head_ok = _git(root, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
        if head_ok:
            _git(root, "read-tree", "HEAD", env=env)
        add = _git(root, "add", "-A", env=env)
        if add.returncode != 0:
            print(f"Snapshot skipped: git add failed: {add.stderr.strip()}")
            return None
        blob = _git(root, "hash-object", "-w", "--stdin", input_text=json.dumps(stats, indent=2) + "\n")
        _git(root, "update-index", "--add", "--cacheinfo",
             f"100644,{blob.stdout.strip()},build-manifest.json", env=env)
        tree = _git(root, "write-tree", env=env).stdout.strip()
        parent = _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").stdout.strip()
        if not parent and head_ok:
            parent = _git(root, "rev-parse", "HEAD").stdout.strip()
        msg = (f"build({label}): {stats.get('pages', '-')} pages, {stats.get('words', 0):,} words\n\n"
               f"{json.dumps(stats, indent=2)}\n")
        commit = _git(root, "commit-tree", tree, "-m", msg, *(["-p", parent] if parent else []))
        if commit.returncode != 0:
            print(f"Snapshot skipped: {commit.stderr.strip()}")
            return None
        sha = commit.stdout.strip()
        _git(root, "update-ref", f"refs/heads/{branch}", sha, *([parent] if parent else []))
        print(f"Snapshot:   {sha[:10]} on branch '{branch}' (push with: git push origin {branch})")
        return sha
