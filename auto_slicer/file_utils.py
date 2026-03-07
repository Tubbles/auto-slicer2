"""Shared file utilities for model discovery."""

from pathlib import Path


def find_models_in_zip(zip_dir: Path) -> list[Path]:
    """Recursively find STL/3MF files in an extracted ZIP, skipping macOS artifacts."""
    stls = list(zip_dir.rglob("*.[sS][tT][lL]"))
    threemfs = list(zip_dir.rglob("*.[3][mM][fF]"))
    return [
        p for p in stls + threemfs
        if "__MACOSX" not in p.parts and not p.name.startswith("._")
    ]
