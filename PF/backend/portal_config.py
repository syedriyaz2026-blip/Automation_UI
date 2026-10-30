"""Configuration boundary for government portal layout changes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PortalConfigurationError(RuntimeError):
    """Raised when the configured portal contract is unsafe to use."""


class PortalConfig:
    def __init__(self, root: Path | None = None, profile: str = ""):
        self.root = root or Path(__file__).resolve().parents[2] / "Platform" / "backend"
        self.settings = self._read("settings.json")
        self.selectors = self._read("selectors.json")
        self.profile = str(self.selectors.get("profile_name", ""))
        if profile:
            self.selectors = self.with_profile(profile)

    def _read(self, name: str) -> dict[str, Any]:
        path = self.root / "config" / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PortalConfigurationError(f"Unable to load {name}: {exc}") from exc
        if not isinstance(value, dict):
            raise PortalConfigurationError(f"{name} must contain a JSON object.")
        return value

    def with_profile(self, profile: str) -> dict[str, Any]:
        registry = self._read("selector_profiles.json")
        selected = registry.get("profiles", {}).get(profile)
        overrides = selected.get("overrides", {}) if isinstance(selected, dict) else None
        if not isinstance(overrides, dict):
            raise PortalConfigurationError(f"Selector profile '{profile}' is not defined or has invalid overrides.")
        self.profile = profile
        return {**self.selectors, **overrides}

    def selectors_for(self, key: str, fallback: list[str] | None = None) -> list[str]:
        values = self.selectors.get(key)
        if isinstance(values, list) and values:
            return values
        if fallback and self.settings.get("allow_code_selector_fallback", False):
            return fallback
        raise PortalConfigurationError(f"Selector key '{key}' is missing or empty.")

    def contract(self, name: str) -> list[str]:
        keys = self.selectors.get("contracts", {}).get(name)
        if not isinstance(keys, list) or not keys:
            raise PortalConfigurationError(f"Selector contract '{name}' is missing.")
        return [str(key) for key in keys]
