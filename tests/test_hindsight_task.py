"""Unit tests for hindsight_task helpers."""

import shutil
import tempfile
from pathlib import Path
from typing import List

import pytest

from src import hindsight_task as ht


def _artifact_path(name: str) -> Path:
    # Repo root is three levels up from this test file (tests/ -> project -> dev -> repo).
    return Path(__file__).resolve().parents[3] / "data" / "artifacts" / name


def test_validate_single_zip_accepts_one_zip():
    input_files = [{"path": "sample.zip", "display_name": "sample.zip"}]
    file_dict, display = ht._validate_single_zip(input_files)
    assert file_dict["path"] == "sample.zip"
    assert display == "sample.zip"


def test_validate_single_zip_rejects_multiple_or_non_zip():
    with pytest.raises(ValueError):
        ht._validate_single_zip([])
    with pytest.raises(ValueError):
        ht._validate_single_zip([{"path": "a.zip"}, {"path": "b.zip"}])
    with pytest.raises(ValueError):
        ht._validate_single_zip([{"path": "not_zip.txt"}])


def test_extract_and_find_profile_from_hint_uses_artifact():
    if shutil.which("7z") is None:
        pytest.skip("7z not available on test host")

    zip_path = _artifact_path("2025-11-19T093927_Test3.zip")
    assert zip_path.is_file(), "Test artifact zip is missing."

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir)
        input_files = [{"path": str(zip_path), "display_name": zip_path.name}]

        _, export_dir, log_file = ht._extract_input_archive(
            input_file=input_files[0],
            output_path=str(output_path),
            display_name=zip_path.name,
            archive_password=None,
        )

        try:
            profile = ht.find_browser_profile(
                export_directory=export_dir,
                profile_hint=r"C:\\Users\SANSDFIR\AppData\\Local\\Google\\Chrome\\User Data\Default",
            )
            assert "Chrome/User Data/Default" in profile
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)
            if log_file.path:
                Path(log_file.path).unlink(missing_ok=True)


def test_extract_and_find_profile_with_zip_prefix():
    if shutil.which("7z") is None:
        pytest.skip("7z not available on test host")

    zip_path = _artifact_path("2025-11-19T093927_Test3.zip")
    assert zip_path.is_file(), "Test artifact zip is missing."

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir)
        input_files = [{"path": str(zip_path), "display_name": zip_path.name}]

        _, export_dir, log_file = ht._extract_input_archive(
            input_file=input_files[0],
            output_path=str(output_path),
            display_name=zip_path.name,
            archive_password=None,
        )

        try:
            profile = ht.find_browser_profile(
                export_directory=export_dir,
                profile_hint=rf"C:\{zip_path.name}\C\Users\SANSDFIR\AppData\Local\Google\Chrome\User Data\Default",
            )
            assert "Chrome/User Data/Default" in profile
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)
            if log_file.path:
                Path(log_file.path).unlink(missing_ok=True)


def test_extract_and_find_edge_profile_from_second_artifact():
    if shutil.which("7z") is None:
        pytest.skip("7z not available on test host")

    zip_path = _artifact_path("Collection-DFIR_mshome_net-2025-11-23T05_53_09Z.zip")
    assert zip_path.is_file(), "Second test artifact zip is missing."

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir)
        input_files = [{"path": str(zip_path), "display_name": zip_path.name}]

        _, export_dir, log_file = ht._extract_input_archive(
            input_file=input_files[0],
            output_path=str(output_path),
            display_name=zip_path.name,
            archive_password=None,
        )

        try:
            profile = ht.find_browser_profile(
                export_directory=export_dir,
                profile_hint=r"C:\\Users\Abdullah\AppData\\Local\\Microsoft\\Edge\\User Data\Default",
            )
            assert "Edge/User Data/Default" in profile
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)
            if log_file.path:
                Path(log_file.path).unlink(missing_ok=True)


def test_find_profile_from_hint_raises_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "dummy").mkdir()
        with pytest.raises(ValueError):
            ht.find_browser_profile(
                export_directory=str(base),
                profile_hint=r"C:\\Users\Abdullah\AppData\\Local\\Microsoft\\Edge\\User Data\Default",
            )


class _FakeProcess:
    def __init__(self, lines: List[str], returncode: int = 0):
        self._lines = lines
        self.returncode = returncode
        self.stdout = self

    def __iter__(self):
        for line in self._lines:
            yield line

    def wait(self):
        return self.returncode


def test_build_and_run_hindsight_emits_progress_and_writes_log(monkeypatch):
    events = []

    def fake_send_event(name, data=None):
        events.append(name)

    lines = ["line1\n", "line2\n"]

    def fake_popen(*args, **kwargs):
        return _FakeProcess(lines=lines, returncode=0)

    monkeypatch.setattr(ht.subprocess, "Popen", fake_popen)

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "log.txt"
        cmd, human = ht._build_and_run_hindsight(
            profile_path='"C:/Users/Default"',
            output_dir=tmpdir,
            log_path=str(log_path),
            send_event=fake_send_event,
        )
        assert "hindsight.py" in cmd[0]
        assert human.startswith("hindsight.py -i")
        assert log_path.read_text().strip().splitlines() == [l.strip() for l in lines]
        # Progress events emitted per line.
        assert len(events) == len(lines)


def test_build_and_run_hindsight_failure_raises_and_logs(monkeypatch):
    def fake_send_event(name, data=None):
        return None

    def fake_popen(*args, **kwargs):
        return _FakeProcess(lines=["oops\n"], returncode=1)

    monkeypatch.setattr(ht.subprocess, "Popen", fake_popen)

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "log.txt"
        with pytest.raises(RuntimeError):
            ht._build_and_run_hindsight(
                profile_path="C:/Users/Default",
                output_dir=tmpdir,
                log_path=str(log_path),
                send_event=fake_send_event,
            )
        assert "oops" in log_path.read_text()
