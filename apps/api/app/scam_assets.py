"""User-marked scam / spam tokens hidden from portfolio views."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import List, Set

STATE_DIR = Path(
    os.environ.get(
        "CRYPTO_TAX_STATE_DIR",
        Path(__file__).resolve().parents[3] / "data",
    )
)
SCAM_ASSETS_FILE = STATE_DIR / "scam_assets.json"


class ScamAssetRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._assets: Set[str] = set()
        self._load()

    def _load(self) -> None:
        if not SCAM_ASSETS_FILE.exists():
            self._assets = set()
            return
        try:
            raw = json.loads(SCAM_ASSETS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._assets = {str(item) for item in raw if str(item).strip()}
            else:
                self._assets = set()
        except (json.JSONDecodeError, ValueError, TypeError):
            self._assets = set()

    def _persist(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = sorted(self._assets)
        SCAM_ASSETS_FILE.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def all(self) -> List[str]:
        with self._lock:
            return sorted(self._assets)

    def is_hidden(self, asset: str) -> bool:
        with self._lock:
            return asset in self._assets

    def add(self, asset: str) -> bool:
        key = asset.strip()
        if not key:
            return False
        with self._lock:
            if key in self._assets:
                return False
            self._assets.add(key)
            self._persist()
            return True

    def remove(self, asset: str) -> bool:
        key = asset.strip()
        with self._lock:
            if key not in self._assets:
                return False
            self._assets.remove(key)
            self._persist()
            return True


registry = ScamAssetRegistry()
