"""Load a repo-root `.env` into the process environment.

`nyra serve`, `nyra worker` and the `nyra cloud-*` commands read their
Supabase settings from the environment. A file on disk does nothing until
something reads it: this does that, without overriding variables already
present in the environment.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_files() -> None:
    roots = [Path.cwd(), Path(__file__).resolve().parents[2]]
    seen: set[Path] = set()
    for root in roots:
        path = (root / ".env").resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        _load(path)


def _load(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value
