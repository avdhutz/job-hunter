"""Loads config/*.yaml once and exposes it as plain dicts."""

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

load_dotenv(ROOT / ".env")


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f) or {}


@lru_cache
def settings() -> dict:
    return _load("settings.yaml")


@lru_cache
def companies() -> dict:
    return _load("companies.yaml")


@lru_cache
def academic() -> dict:
    return _load("academic.yaml")


@lru_cache
def skills() -> dict:
    return _load("skills.yaml")


def path(rel: str) -> Path:
    """Resolve a settings path (supports ~ and project-relative paths)."""
    p = Path(rel).expanduser()
    return p if p.is_absolute() else ROOT / p
