"""Load config.yaml into typed, importable settings.

Nothing here is hard-coded elsewhere: thresholds and crawl behavior all
flow from a single YAML file so they can be tuned (or recalibrated) without
touching code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

def _default_config_path() -> Path:
    """Repo-root config.yaml when the package lives in backend/nyra."""
    packaged = Path(__file__).resolve().parents[2] / "config.yaml"
    if packaged.is_file():
        return packaged
    cwd = Path.cwd() / "config.yaml"
    return cwd if cwd.is_file() else packaged


DEFAULT_CONFIG_PATH = _default_config_path()


@dataclass(frozen=True)
class CrawlConfig:
    max_pages: int = 300
    delay_seconds_min: float = 1.0
    delay_seconds_max: float = 2.0
    user_agent: str = "NyraBot/0.1"
    min_image_side_px: int = 200
    respect_robots_txt: bool = True
    request_timeout_seconds: int = 20
    page_load_timeout_ms: int = 30000


@dataclass(frozen=True)
class MatchConfig:
    phash_threshold: int = 8
    dhash_threshold: int = 8
    clip_similarity_high: float = 0.92
    clip_similarity_medium: float = 0.85
    clip_similarity_floor: float = 0.75
    clip_model_name: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"


@dataclass(frozen=True)
class ReportConfig:
    default_within_days: int = 90


@dataclass(frozen=True)
class Config:
    crawl: CrawlConfig
    match: MatchConfig
    report: ReportConfig


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    crawl_raw = raw.get("crawl", {})
    match_raw = raw.get("match", {})
    report_raw = raw.get("report", {})

    return Config(
        crawl=CrawlConfig(**{**CrawlConfig().__dict__, **crawl_raw}),
        match=MatchConfig(**{**MatchConfig().__dict__, **match_raw}),
        report=ReportConfig(**{**ReportConfig().__dict__, **report_raw}),
    )
