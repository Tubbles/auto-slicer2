"""Shared setup for the GitHub slicing tests: fixtures from the real API and a context that needs no network."""

import json
import shutil
import time
from pathlib import Path

from auto_slicer.config import Config
from auto_slicer.github_catalog import CatalogState, latest_run, parse_artifacts
from auto_slicer.github_service import GithubContext
from auto_slicer.settings_registry import SettingsRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "github"
REPO = "Tubbles/model-workshop"
ARTIFACT_ID = 12959357632  # kbdkid5-7300786 in artifacts.json
ARTIFACT_NAME = "kbdkid5-7300786"
HEAD_SHA = "73007863a31e574b7f75858374a742f2307c5b34"
MODEL = "kbdkid5-plate-left.stl"
GCODES_PREFIX = "Sync/Autoslice Github"


def load_fixture(name: str) -> dict:
    """A saved GitHub API response."""
    return json.loads((FIXTURES / name).read_text())


def make_config(tmp_path: Path) -> Config:
    """A Config with GitHub slicing on, everything under tmp_path."""
    return Config(
        archive_dir=tmp_path / "archive", cura_bin=Path("."), def_dir=Path("."), printer_def="",
        defaults={}, telegram_token="test:token", allowed_users={42}, notify_chat_id=None,
        registry=SettingsRegistry({}, {}, {}),
        github_token="test-token", github_repos=[REPO],
        github_cache_dir=tmp_path / "cache", github_archive_dir=tmp_path / "github",
        moonraker_gcodes_prefix=GCODES_PREFIX,
    )


def make_context(tmp_path: Path, rules: dict | None = None, models: tuple[str, ...] = (MODEL,)) -> GithubContext:
    """A context whose catalog is already listed and downloaded, so no method touches the network."""
    config = make_config(tmp_path)
    state = CatalogState(None, config.github_token, config.github_repos, config.github_cache_dir)
    artifacts = parse_artifacts(load_fixture("artifacts.json"), REPO, latest_run(load_fixture("runs.json"))["head_sha"])
    state.artifacts = {artifact.project["artifact_id"]: artifact for artifact in artifacts}
    state.projects = [artifact.project for artifact in artifacts]
    state.fetched_at = time.time()
    state.rules[(REPO, HEAD_SHA)] = rules
    models_dir = config.github_cache_dir / str(ARTIFACT_ID) / "models"
    models_dir.mkdir(parents=True)
    for name in models:
        (models_dir / name).write_bytes(b"solid model\nendsolid model\n")
    return GithubContext(config, state)


def fake_slice_file(calls: list, success: bool = True):
    """A slice_file stand-in that archives like the real one: gcode into the folder, model beside it."""
    def slice_file(config, stl_path, overrides, archive_folder=None, archive_subdir=""):
        calls.append({"stl_path": stl_path, "overrides": overrides, "archive_folder": archive_folder})
        if not success:
            return False, "CuraEngine error:\nboom", None, {}
        archive_folder.mkdir(parents=True, exist_ok=True)
        (archive_folder / f"{stl_path.stem}.gcode").write_text(";FLAVOR:Marlin\n")
        shutil.move(str(stl_path), archive_folder.parent / stl_path.name)
        return True, "Slicing completed successfully", archive_folder, {}
    return slice_file
