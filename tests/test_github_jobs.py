"""Tests for GitHub slicing jobs: request validation, job states, and results already archived."""

from pathlib import Path
from unittest.mock import patch

import pytest

from auto_slicer import github_jobs
from auto_slicer.github_catalog import GithubError
from auto_slicer.github_jobs import (
    JOB_TTL, SlicePlan, choice_overrides, choice_prefix, finished, latest_folders,
    model_dir, new_job, parse_choice, printer_path, prune_jobs, run_slice,
    sliced, split_folder_name,
)
from auto_slicer.github_service import call
from auto_slicer.matrix import DEFAULT_SELECTION, compose_overrides

from .github_helpers import ARTIFACT_ID, ARTIFACT_NAME, GCODES_PREFIX, MODEL, fake_slice_file, make_config, make_context

RULES = {"matrix": True, "rules": {"Thin lid": {"description": "", "settings": {"layer_height": "0.1"}}}}


class TestParseChoice:
    def test_selection_filled(self):
        assert parse_choice({"selection": {"material": "TPU"}}, None) == {
            "selection": {**DEFAULT_SELECTION, "material": "TPU"},
        }

    def test_rule(self):
        assert parse_choice({"rule": "Thin lid"}, RULES) == {"rule": "Thin lid"}

    @pytest.mark.parametrize("params", [{}, {"selection": {}, "rule": "Thin lid"}])
    def test_needs_exactly_one(self, params):
        with pytest.raises(GithubError, match="either"):
            parse_choice(params, RULES)

    def test_unknown_rule(self):
        with pytest.raises(GithubError, match="Unknown rule"):
            parse_choice({"rule": "nope"}, RULES)

    def test_rule_without_rules_file(self):
        with pytest.raises(GithubError):
            parse_choice({"rule": "Thin lid"}, None)

    def test_bad_selection(self):
        with pytest.raises(GithubError) as raised:
            parse_choice({"selection": {"material": "ABS"}}, None)
        assert raised.value.status == 400

    def test_selection_not_object(self):
        with pytest.raises(GithubError, match="object"):
            parse_choice({"selection": "PLA"}, None)

    def test_matrix_off(self):
        with pytest.raises(GithubError, match="rules only"):
            parse_choice({"selection": {}}, {**RULES, "matrix": False})


class TestChoiceHelpers:
    def test_rule_overrides(self):
        assert choice_overrides({"rule": "Thin lid"}, RULES) == {"layer_height": "0.1"}

    def test_selection_overrides(self):
        selection = {**DEFAULT_SELECTION, "material": "PETG"}
        assert choice_overrides({"selection": selection}, None) == compose_overrides(selection)

    def test_prefixes(self):
        assert choice_prefix({"rule": "Thin lid"}) == "Thin-lid"
        assert choice_prefix({"selection": dict(DEFAULT_SELECTION)}) == "PLA_No-support_Skirt_Standard"

    def test_model_dir(self):
        assert model_dir(Path("/a"), "kbdkid5-7300786", "sub/plate.stl") == Path("/a/kbdkid5-7300786/plate")

    def test_printer_path(self):
        assert printer_path("Sync/Autoslice Github", "x/y.gcode") == "Sync/Autoslice Github/x/y.gcode"
        assert printer_path("", "x/y.gcode") == "x/y.gcode"


class TestJobRecords:
    def test_new_job(self):
        job = new_job("j1", 5, "m.stl", {"rule": "Thin lid"}, 10.0)
        assert job["state"] == "queued"
        assert job["rule"] == "Thin lid"
        assert job["selection"] is None
        assert job["finished_at"] is None

    def test_finished_done(self):
        job = finished(new_job("j1", 5, "m.stl", {"rule": "r"}, 10.0), True, "ok", "a/b.gcode", "P", 20.0)
        assert (job["state"], job["path"], job["printer_path"], job["finished_at"]) == ("done", "a/b.gcode", "P/a/b.gcode", 20.0)

    def test_finished_error(self):
        job = finished(new_job("j1", 5, "m.stl", {"rule": "r"}, 10.0), False, "boom", None, "P", 20.0)
        assert (job["state"], job["message"], job["path"]) == ("error", "boom", None)

    def test_success_without_gcode_is_error(self):
        job = finished(new_job("j1", 5, "m.stl", {"rule": "r"}, 10.0), True, "ok", None, "P", 20.0)
        assert job["state"] == "error"

    def test_prune(self):
        running = new_job("a", 1, "m", {"rule": "r"}, 0.0)
        recent = {**new_job("b", 1, "m", {"rule": "r"}, 0.0), "finished_at": 1000.0}
        old = {**new_job("c", 1, "m", {"rule": "r"}, 0.0), "finished_at": 1.0}
        kept = prune_jobs({"a": running, "b": recent, "c": old}, JOB_TTL + 10.0)
        assert list(kept) == ["a", "b"]


class TestRunSlice:
    def test_copies_model_and_reports_relative_gcode(self, tmp_path):
        config = make_config(tmp_path)
        source = tmp_path / "cache" / MODEL
        source.parent.mkdir(parents=True)
        source.write_bytes(b"solid")
        directory = model_dir(config.github_archive_dir, ARTIFACT_NAME, MODEL)
        plan = SlicePlan(source, {"layer_height": "0.1"}, directory, "Thin-lid")
        calls = []
        with patch.object(github_jobs, "slice_file", fake_slice_file(calls)):
            result = run_slice(config, plan, "20261030_141500")
        assert result == (True, "Slicing completed successfully",
                          f"{ARTIFACT_NAME}/kbdkid5-plate-left/Thin-lid_20261030_141500/kbdkid5-plate-left.gcode")
        assert source.exists()
        assert calls[0]["stl_path"] != source
        assert calls[0]["archive_folder"] == directory / "Thin-lid_20261030_141500"
        assert (directory / MODEL).exists()

    def test_failure(self, tmp_path):
        config = make_config(tmp_path)
        source = tmp_path / MODEL
        source.write_bytes(b"solid")
        plan = SlicePlan(source, {}, tmp_path / "github" / "x" / "y", "p")
        with patch.object(github_jobs, "slice_file", fake_slice_file([], success=False)):
            assert run_slice(config, plan, "20261030_141500") == (False, "CuraEngine error:\nboom", None)


class TestJobQueue:
    @pytest.mark.asyncio
    async def test_states_and_events(self, tmp_path):
        context = make_context(tmp_path, rules=RULES)
        events = []

        async def listener(job):
            events.append(job)
        context.jobs.listeners.append(listener)
        calls = []
        with patch.object(github_jobs, "slice_file", fake_slice_file(calls)):
            job = await call(context, "slice", {"artifact_id": ARTIFACT_ID, "model": MODEL, "selection": {"material": "TPU"}})
            assert job["state"] == "queued"
            await context.jobs.pending.join()
        context.jobs.worker.cancel()
        assert [event["state"] for event in events] == ["queued", "slicing", "done"]
        done = await call(context, "jobs", {"id": job["id"]})
        assert done["path"].startswith(f"{ARTIFACT_NAME}/kbdkid5-plate-left/TPU_No-support_Skirt_Standard_")
        assert done["printer_path"] == f"{GCODES_PREFIX}/{done['path']}"
        assert calls[0]["overrides"]["speed_print"] == "25"

    @pytest.mark.asyncio
    async def test_one_at_a_time(self, tmp_path):
        context = make_context(tmp_path, rules=RULES)
        active, peak = [], []

        def slow_slice(config, stl_path, overrides, archive_folder=None, archive_subdir=""):
            active.append(1)
            peak.append(len(active))
            result = fake_slice_file([])(config, stl_path, overrides, archive_folder)
            active.pop()
            return result
        with patch.object(github_jobs, "slice_file", slow_slice):
            for rule_or_cell in ({"rule": "Thin lid"}, {"selection": {}}, {"selection": {"adhesion": "Brim"}}):
                await call(context, "slice", {"artifact_id": ARTIFACT_ID, "model": MODEL, **rule_or_cell})
            await context.jobs.pending.join()
        context.jobs.worker.cancel()
        assert peak == [1, 1, 1]
        assert [job["state"] for job in await call(context, "jobs", {})] == ["done", "done", "done"]

    @pytest.mark.asyncio
    async def test_error_state(self, tmp_path):
        context = make_context(tmp_path, rules=RULES)
        with patch.object(github_jobs, "slice_file", fake_slice_file([], success=False)):
            job = await call(context, "slice", {"artifact_id": ARTIFACT_ID, "model": MODEL, "rule": "Thin lid"})
            await context.jobs.pending.join()
        context.jobs.worker.cancel()
        failed = await call(context, "jobs", {"id": job["id"]})
        assert failed["state"] == "error"
        assert "boom" in failed["message"]

    @pytest.mark.asyncio
    async def test_unknown_model_not_queued(self, tmp_path):
        context = make_context(tmp_path)
        with pytest.raises(GithubError) as raised:
            await call(context, "slice", {"artifact_id": ARTIFACT_ID, "model": "nope.stl", "selection": {}})
        assert raised.value.status == 404
        assert await call(context, "jobs", {}) == []

    @pytest.mark.asyncio
    async def test_unknown_job(self, tmp_path):
        with pytest.raises(GithubError) as raised:
            await call(make_context(tmp_path), "jobs", {"id": "nope"})
        assert raised.value.status == 404


class TestSliced:
    def test_split_folder_name(self):
        assert split_folder_name("PLA_No-support_Skirt_Standard_20261030_141500") == (
            "PLA_No-support_Skirt_Standard", "20261030_141500",
        )
        assert split_folder_name("notes") is None

    def test_latest_folders(self):
        names = ["a_20261030_141500", "a_20261031_090000", "b_20261029_000000", "junk"]
        assert latest_folders(names) == {"a": "a_20261031_090000", "b": "b_20261029_000000"}

    def _result(self, directory: Path, folder: str, gcode: bool = True) -> None:
        (directory / folder).mkdir(parents=True)
        if gcode:
            (directory / folder / "kbdkid5-plate-left.gcode").write_text(";")

    def test_cells_and_rules(self, tmp_path):
        directory = model_dir(tmp_path, ARTIFACT_NAME, MODEL)
        self._result(directory, "PLA_No-support_Skirt_Standard_20261030_141500")
        self._result(directory, "PLA_No-support_Skirt_Standard_20261030_151500")
        self._result(directory, "TPU_Vase_Brim_Fine_20261030_141500", gcode=False)
        self._result(directory, "Thin-lid_20261030_141500")
        self._result(directory, "Removed-rule_20261030_141500")
        (directory / MODEL).write_bytes(b"solid")
        results = sliced(directory, tmp_path, RULES, "P")
        assert results == [
            {"selection": dict(DEFAULT_SELECTION), "rule": None,
             "path": f"{ARTIFACT_NAME}/kbdkid5-plate-left/PLA_No-support_Skirt_Standard_20261030_151500/kbdkid5-plate-left.gcode",
             "printer_path": f"P/{ARTIFACT_NAME}/kbdkid5-plate-left/PLA_No-support_Skirt_Standard_20261030_151500/kbdkid5-plate-left.gcode"},
            {"selection": None, "rule": "Thin lid",
             "path": f"{ARTIFACT_NAME}/kbdkid5-plate-left/Thin-lid_20261030_141500/kbdkid5-plate-left.gcode",
             "printer_path": f"P/{ARTIFACT_NAME}/kbdkid5-plate-left/Thin-lid_20261030_141500/kbdkid5-plate-left.gcode"},
        ]

    def test_nothing_sliced(self, tmp_path):
        assert sliced(tmp_path / "missing", tmp_path, None, "") == []

    @pytest.mark.asyncio
    async def test_method(self, tmp_path):
        context = make_context(tmp_path, rules=RULES)
        directory = model_dir(context.config.github_archive_dir, ARTIFACT_NAME, MODEL)
        self._result(directory, "Thin-lid_20261030_141500")
        results = await call(context, "sliced", {"artifact_id": ARTIFACT_ID, "model": MODEL})
        assert [r["rule"] for r in results] == ["Thin lid"]
