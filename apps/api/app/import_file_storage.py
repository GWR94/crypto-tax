"""Persist uploaded CSV files for connected-source previews."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _default_state_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "data"


STATE_DIR = Path(os.environ.get("CRYPTO_TAX_STATE_DIR", _default_state_dir()))
IMPORT_FILES_DIR = STATE_DIR / "import_files"


def save_import_file(import_id: str, content: bytes) -> None:
    IMPORT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    _file_path(import_id).write_bytes(content)


def read_import_file(import_id: str) -> Optional[bytes]:
    path = _file_path(import_id)
    if not path.exists():
        return None
    return path.read_bytes()


def remove_import_file(import_id: str) -> None:
    path = _file_path(import_id)
    if path.exists():
        path.unlink()


def clear_import_files() -> None:
    if not IMPORT_FILES_DIR.exists():
        return
    for path in IMPORT_FILES_DIR.glob("*.csv"):
        path.unlink()


def _file_path(import_id: str) -> Path:
    return IMPORT_FILES_DIR / f"{import_id}.csv"
