#!/usr/bin/env python3
"""Build a clean, source-only distributable release ZIP.

Excludes development artifacts, caches, virtual environments, .env files,
local databases, and runtime audit logs.
"""

import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
OUTPUT_ZIP = DIST_DIR / "agentic_ai_personalized_medicine_source.zip"

EXCLUDE_DIRS = {
    "venv", ".venv", "__pycache__", ".pytest_cache", ".git", ".idea",
    ".vscode", "htmlcov", "pgdata", "dist", "node_modules", ".coverage"
}

EXCLUDE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".db", ".log", ".sqlite", ".sqlite3"
}

EXCLUDE_FILES = {
    ".env", ".env.local", "app.db", ".DS_Store", "Thumbs.db"
}

def is_excluded(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return True
    if path.name in EXCLUDE_FILES:
        return True
    if path.suffix in EXCLUDE_EXTENSIONS:
        return True
    if "logs" in rel_parts and path.suffix in (".jsonl", ".log"):
        return True
    return False

def build_release_zip() -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()
    
    included_count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT):
            root_path = Path(root)
            # Prune excluded directories
            dirs[:] = [d for d in dirs if not is_excluded(root_path / d)]
            
            for file_name in files:
                file_path = root_path / file_name
                if not is_excluded(file_path):
                    rel_path = file_path.relative_to(ROOT)
                    zf.write(file_path, arcname=rel_path)
                    included_count += 1

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"[OK] Release zip created: {OUTPUT_ZIP}")
    print(f"     Included {included_count} source files ({size_mb:.2f} MB)")
    return OUTPUT_ZIP

if __name__ == "__main__":
    build_release_zip()
