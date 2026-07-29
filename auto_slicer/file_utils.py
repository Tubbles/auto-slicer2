"""Shared file utilities for model discovery and format conversion."""

from pathlib import Path

from .stepfile import convert_step_to_stl
from .threemf import convert_3mf_to_stl

# Extensions that must be converted to STL before CuraEngine sees them
CONVERTIBLE_EXTENSIONS = {".3mf", ".step", ".stp"}


def convert_model_to_stl(model_path: Path, stl_path: Path) -> None:
    """Convert a non-STL model file (3MF or STEP) to STL."""
    ext = model_path.suffix.lower()
    if ext == ".3mf":
        convert_3mf_to_stl(model_path, stl_path)
    elif ext in (".step", ".stp"):
        convert_step_to_stl(model_path, stl_path)
    else:
        raise ValueError(f"Cannot convert {ext} to STL")


def find_models_in_zip(zip_dir: Path) -> list[Path]:
    """Recursively find STL/3MF/STEP files in an extracted ZIP, skipping macOS artifacts."""
    found = (
        list(zip_dir.rglob("*.[sS][tT][lL]"))
        + list(zip_dir.rglob("*.[3][mM][fF]"))
        + list(zip_dir.rglob("*.[sS][tT][eE][pP]"))
        + list(zip_dir.rglob("*.[sS][tT][pP]"))
    )
    return [
        p for p in found
        if "__MACOSX" not in p.parts and not p.name.startswith("._")
    ]
