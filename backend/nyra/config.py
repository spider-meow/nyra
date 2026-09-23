"""Load config.yaml into typed, importable settings.

Nothing here is hard-coded elsewhere: thresholds and crawl behavior all
flow from a single YAML file so they can be tuned (or recalibrated) without
touching code. In the hosted product an organization can override a
whitelisted subset of these values (`org_settings`, see `with_overrides`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

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
    # Hard ceiling for any single crawl, whatever a request or an
    # organization asks for.
    max_pages_limit: int = 2000
    # Pages rendered at the same time. Each worker still waits
    # delay_seconds_min..max between two of its own pages.
    concurrency: int = 3
    delay_seconds_min: float = 1.0
    delay_seconds_max: float = 2.0
    user_agent: str = "NyraBot/0.1"
    min_image_side_px: int = 200
    max_image_bytes: int = 25_000_000
    max_image_pixels: int = 60_000_000
    respect_robots_txt: bool = True
    request_timeout_seconds: int = 20
    page_load_timeout_ms: int = 30000
    # Try to get past cookie banners and age gates (spirits sites put one
    # in front of every page) before reading the DOM.
    dismiss_overlays: bool = True
    # Extra CSS selectors clicked, in order, on every page after load.
    pre_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchConfig:
    phash_threshold: int = 8
    dhash_threshold: int = 8
    clip_similarity_high: float = 0.92
    clip_similarity_medium: float = 0.85
    clip_similarity_floor: float = 0.75
    clip_model_name: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    embedding_batch_size: int = 16


@dataclass(frozen=True)
class ReportConfig:
    default_within_days: int = 90


@dataclass(frozen=True)
class Config:
    crawl: CrawlConfig = field(default_factory=CrawlConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    report: ReportConfig = field(default_factory=ReportConfig)


# Keys an organization may override from the interface. The model name is
# deliberately absent: changing it invalidates every stored embedding.
ORG_OVERRIDABLE = {
    "crawl": {"max_pages", "concurrency", "delay_seconds_min", "delay_seconds_max", "min_image_side_px",
              "respect_robots_txt", "dismiss_overlays", "pre_actions"},
    "match": {"phash_threshold", "dhash_threshold", "clip_similarity_high", "clip_similarity_medium",
              "clip_similarity_floor"},
    "report": {"default_within_days"},
}


def _build(cls, raw: dict[str, Any]):
    known = {f.name for f in fields(cls)}
    values = {k: v for k, v in (raw or {}).items() if k in known}
    if "pre_actions" in values:
        values["pre_actions"] = tuple(str(item) for item in values["pre_actions"] or ())
    return cls(**values)


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    return Config(
        crawl=_build(CrawlConfig, raw.get("crawl", {})),
        match=_build(MatchConfig, raw.get("match", {})),
        report=_build(ReportConfig, raw.get("report", {})),
    )


def validate_overrides(overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep only whitelisted keys, coerce types, and reject nonsense.

    Raises ValueError with a French message the API can show as is.
    """
    base = Config()
    clean: dict[str, dict[str, Any]] = {}
    for section, allowed in ORG_OVERRIDABLE.items():
        given = (overrides or {}).get(section) or {}
        if not isinstance(given, dict):
            raise ValueError(f"Section {section} invalide.")
        defaults = getattr(base, section)
        out: dict[str, Any] = {}
        for key, value in given.items():
            if key not in allowed or value is None or value == "":
                continue
            current = getattr(defaults, key)
            try:
                if isinstance(current, bool):
                    out[key] = bool(value)
                elif isinstance(current, int):
                    out[key] = int(value)
                elif isinstance(current, float):
                    out[key] = float(value)
                elif isinstance(current, tuple):
                    out[key] = [str(item).strip() for item in value if str(item).strip()]
                else:
                    out[key] = str(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Valeur invalide pour {section}.{key}.") from exc
        clean[section] = out

    match = {**{f.name: getattr(base.match, f.name) for f in fields(MatchConfig)}, **clean["match"]}
    if not (0 < match["clip_similarity_floor"] <= match["clip_similarity_medium"] <= match["clip_similarity_high"] <= 1):
        raise ValueError("Les seuils de similarité doivent respecter plancher ≤ moyen ≤ haut, entre 0 et 1.")
    for key in ("phash_threshold", "dhash_threshold"):
        if not 0 <= match[key] <= 32:
            raise ValueError("Les seuils de hachage vont de 0 à 32.")
    crawl = clean["crawl"]
    if "concurrency" in crawl and not 1 <= crawl["concurrency"] <= 8:
        raise ValueError("Le parallélisme va de 1 à 8 pages.")
    if "max_pages" in crawl and crawl["max_pages"] < 1:
        raise ValueError("Le nombre de pages doit être positif.")
    if min(crawl.get("delay_seconds_min", 1), crawl.get("delay_seconds_max", 1)) < 0.5:
        raise ValueError("Le délai entre deux pages est d'au moins 0,5 s.")
    return clean


def with_overrides(config: Config, overrides: dict[str, Any] | None) -> Config:
    """Apply an organization's saved overrides on top of config.yaml."""
    if not overrides:
        return config
    clean = validate_overrides(overrides)
    crawl_values = dict(clean["crawl"])
    if "pre_actions" in crawl_values:
        crawl_values["pre_actions"] = tuple(crawl_values["pre_actions"])
    if "max_pages" in crawl_values:
        crawl_values["max_pages"] = min(crawl_values["max_pages"], config.crawl.max_pages_limit)
    return Config(
        crawl=replace(config.crawl, **crawl_values),
        match=replace(config.match, **clean["match"]),
        report=replace(config.report, **clean["report"]),
    )
