#!/usr/bin/env python3
"""Semantic versioning for the local Scout sandbox gate engine."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


SANDBOX_DIR = Path(__file__).resolve().parent
REPO_ROOT = SANDBOX_DIR.parent
ENGINE_VERSION_MAJOR = 3
ENGINE_VERSION_MINOR = 0
GATE_LOGIC_FILES = (
    "scout-gates-sandbox/run_gates.py",
    "scout-gates-sandbox/explainability.py",
    "scout-gates-sandbox/directionality.py",
    "scout-gates-sandbox/option_picker.py",
)


def file_fingerprint() -> str:
    digest = hashlib.sha256()
    for relative in GATE_LOGIC_FILES:
        path = REPO_ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.exists():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def git_output(args: list[str]) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=REPO_ROOT,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()


def gate_logic_commit_count() -> int:
    try:
        output = git_output(["rev-list", "--count", "HEAD", "--", *GATE_LOGIC_FILES])
        return int(output or "0")
    except Exception:
        return 0


def gate_logic_has_uncommitted_changes() -> bool:
    try:
        porcelain = git_output(["status", "--porcelain", "--", *GATE_LOGIC_FILES])
    except Exception:
        return False
    return bool(porcelain)


def current_engine_version() -> str:
    patch = gate_logic_commit_count()
    fingerprint = file_fingerprint()
    if gate_logic_has_uncommitted_changes():
        return f"{ENGINE_VERSION_MAJOR}.{ENGINE_VERSION_MINOR}.{patch + 1}-dirty.{fingerprint}"
    return f"{ENGINE_VERSION_MAJOR}.{ENGINE_VERSION_MINOR}.{patch}+{fingerprint}"
