import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

from celery import signals
from celery.utils.log import get_task_logger

# API docs - https://openrelik.github.io/openrelik-worker-common/openrelik_worker_common/index.html
from openrelik_worker_common.archive_utils import extract_archive
from openrelik_worker_common.file_utils import create_output_file
from openrelik_worker_common.logging import Logger
from openrelik_worker_common.task_utils import create_task_result, get_input_files

from .app import celery

# Task name used to register and route the task to the correct queue.
TASK_NAME = "openrelik-worker-hindsight.tasks.hindsight"

# Task metadata for registration in the core system.
TASK_METADATA = {
    "display_name": "Hindsight Parser",
    "description": "Parses browser artifacts with Hindsight from a supplied ZIP archive (Native Kape or Velociraptor triage) and a provided profile browser path",
    "task_config": [
        {
            "name": "archive_password",
            "label": "Archive password",
            "description": "Password to decrypt the input ZIP archive if protected.",
            "type": "text",
            "required": False,
        },
        {
            "name": "browser_profile",
            "label": "Browser profile (Default Folder) to parse",
            "description": "Insert an exact browser profile default folder to parse (e.g., C:\\Users\Ryan\AppData\Local\Google\Chrome\\User Data\Default), matching is case sensitive. Don't leave blank.",
            "type": "text",
            "required": True,
        },
    ],
}

log_root = Logger()
logger = log_root.get_logger(__name__, get_task_logger(__name__))


def _validate_single_zip(input_files: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    """Ensure exactly one ZIP archive is provided and return it with a display name."""
    if not input_files:
        logger.error("Validation failed: no input files provided.")
        raise ValueError("No input files provided; expected exactly one ZIP archive.")

    if len(input_files) != 1:
        logger.error("Validation failed: %s input files provided (expected 1).", len(input_files))
        raise ValueError(f"Expected exactly one ZIP archive, received {len(input_files)} files.")

    input_file = input_files[0]
    display_name = input_file.get("display_name") or Path(input_file.get("path", "")).name

    if not str(display_name).lower().endswith(".zip"):
        logger.error("Validation failed: input file is not a ZIP (%s).", display_name)
        raise ValueError(f"Input file must be a ZIP archive; received '{display_name}'.")

    logger.info("Validated single ZIP input: %s", display_name)
    return input_file, display_name


def _extract_input_archive(
    input_file: Dict[str, Any],
    output_path: str,
    display_name: str,
    archive_password: Optional[str],
):
    """Extract the archive and return the command string, export directory, and log file."""
    log_file = create_output_file(
        output_path,
        display_name=f"extract_{display_name}.log",
    )

    logger.info("Starting archive extraction for %s", display_name)

    try:
        command_string, export_directory = extract_archive(
            input_file,
            output_path,
            log_file.path,
            [],
            archive_password,
        )
    except Exception as exc:
        logger.error(f"Failed to extract archive '{display_name}': {exc}")
        message = str(exc).lower()
        if not archive_password and ("password" in message or "protected" in message or "execution error" in message):
            raise ValueError("Archive appears to be password-protected; please supply a password.") from exc
        if archive_password:
            raise ValueError("Failed to extract archive; the password may be incorrect.") from exc
        raise

    logger.info("Extraction complete for %s -> %s", display_name, export_directory)
    return command_string, export_directory, log_file


def find_browser_profile(export_directory: str, profile_hint: str) -> str:
    """Resolve the provided profile path hint inside the extracted tree.

    Assumes a proper Windows-style path starting with a drive letter,
    e.g., C:\\Users\\Ryan\\AppData\\Local\\Google\\Chrome\\User Data\\Default.
    Trims whitespace and searches for the tail (starting at Users) anywhere under export_directory.
    """
    if not profile_hint:
        raise ValueError("A browser profile path is required.")

    trimmed = profile_hint.strip()
    normalized = PureWindowsPath(trimmed)
    drive = normalized.drive
    if not drive:
        logger.error("Profile hint missing drive letter: %s", profile_hint)
        raise ValueError("Browser profile path must start with a drive letter (e.g., C:\\...).")

    # Remove the drive component; work with the remainder.
    tail_parts = list(normalized.parts)
    if tail_parts and tail_parts[0].endswith(":"):
        tail_parts = tail_parts[1:]
    if not tail_parts:
        raise ValueError("Browser profile path is invalid or empty after drive removal.")

    # Prefer suffix starting at 'Users' to ignore any leading collection-specific folders.
    lower_parts = [p.lower() for p in tail_parts]
    if "users" in lower_parts:
        idx = lower_parts.index("users")
        tail_parts = tail_parts[idx:]

    relative_suffix = Path(*tail_parts)
    logger.info("Searching for profile hint suffix: %s", relative_suffix.as_posix())
    search_glob = f"**/{relative_suffix.as_posix()}"
    base = Path(export_directory)
    candidates = [p for p in base.glob(search_glob) if p.is_dir()]

    if not candidates:
        logger.error("Profile hint not found: %s", profile_hint)
        raise ValueError(f"Browser profile path not found in archive: {profile_hint}")

    match = sorted(candidates)[0]
    logger.info("Profile hint resolved to: %s", match)
    return str(match)


def _build_and_run_hindsight(profile_path: str, output_dir: str, log_path: str, send_event) -> Tuple[List[str], str]:
    """Construct and execute the Hindsight command.

    - Builds the CLI: hindsight.py -i <profile> (run with cwd=output_dir so the default-named XLSX lands there).
    - Executes, streams progress events, and raises on non-zero exit. Returns (command_list, human_readable_string).
    """
    cmd = [
        "hindsight.py",
        "-i",
        profile_path,
    ]
    human_readable = f'hindsight.py -i \"{profile_path}\"'

    logger.info("Running Hindsight: %s", human_readable)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=output_dir,
        text=True,
        bufsize=1,
    )

    with open(log_path, "w", encoding="utf-8") as log_fh:
        for line in process.stdout:
            log_fh.write(line)
            log_fh.flush()
            send_event("task-progress", data=None)

    process.wait()

    if process.returncode != 0:
        with open(log_path, "r", encoding="utf-8") as log_fh:
            tail = log_fh.read()[-2000:]
        logger.error("Hindsight failed with exit %s", process.returncode)
        raise RuntimeError(f"Hindsight failed (exit {process.returncode}). Tail of log: {tail}")

    return cmd, human_readable


@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_):
    log_root.bind(
        task_id=task_id,
        task_name=task.name,
        worker_name=TASK_METADATA.get("display_name"),
    )


@celery.task(bind=True, name=TASK_NAME, metadata=TASK_METADATA)
def hindsight(
    self,
    pipe_result: Optional[str] = None,
    input_files: Optional[List[Dict]] = None,
    output_path: Optional[str] = None,
    workflow_id: Optional[str] = None,
    task_config: Optional[Dict] = None,
) -> str:
    """Run Hindsight against a single ZIP archive containing browser artifacts."""
    task_config = task_config or {}
    archive_password = task_config.get("archive_password")
    browser_profile_hint = task_config.get("browser_profile")

    if not browser_profile_hint:
        raise ValueError("Browser Profile is required and must point to a Default folder to parse.")

    log_root.bind(workflow_id=workflow_id)
    logger.info(f"Starting {TASK_NAME} for workflow {workflow_id}")

    input_files = get_input_files(pipe_result, input_files or [])
    input_file, display_name = _validate_single_zip(input_files)

    output_files = []
    command_string, export_directory, _ = _extract_input_archive(
        input_file,
        output_path,
        display_name,
        archive_password,
    )
    logger.info("Archive extracted (task) for %s -> %s", display_name, export_directory)

    profile_path = find_browser_profile(export_directory, browser_profile_hint)
    logger.info(f"Resolved browser profile path: {profile_path}")

    # Run Hindsight in the output directory so its default-named report lands there.
    timestamp = datetime.now(timezone.utc)
    # Capture Hindsight stdout into a log file for UI visibility and debugging.
    timestamp_str = timestamp.strftime("%Y%m%dT%H%M%SZ")
    log_file = create_output_file(
        output_path,
        display_name=f"hindsight_{timestamp_str}_log.txt",
    )

    try:
        cmd, human_cmd = _build_and_run_hindsight(profile_path, output_path, log_file.path, self.send_event)
    except Exception:
        output_files.append(log_file.to_dict())
        if export_directory and Path(export_directory).exists():
            shutil.rmtree(export_directory, ignore_errors=True)
        raise
    logger.info(f"Executed Hindsight command: {human_cmd}")

    report_candidates = sorted(
        Path(output_path).glob("Hindsight Report *.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if report_candidates:
        generated_report = report_candidates[0]
        logger.info("Hindsight generated report: %s", generated_report)
        output_file = create_output_file(
            output_path,
            display_name=generated_report.name,
            data_type="openrelik:hindsight:report",
        )
        shutil.move(generated_report, output_file.path)
        output_files.append(output_file.to_dict())
    else:
        # Surface the log so the user can inspect what went wrong.
        logger.error("Hindsight did not produce a report file; exposing log to user.")
        output_files.append(log_file.to_dict())
        if export_directory and Path(export_directory).exists():
            shutil.rmtree(export_directory, ignore_errors=True)
        raise RuntimeError("Hindsight did not produce a report file. See the attached log for details.")

    # Always include the run log on success.
    output_files.append(log_file.to_dict())

    # Clean up extracted data to leave only the original ZIP and outputs.
    if export_directory and Path(export_directory).exists():
        shutil.rmtree(export_directory, ignore_errors=True)

    return create_task_result(
        output_files=output_files,
        workflow_id=workflow_id,
        command=human_cmd,
        meta={
            "profile_path": profile_path,
        },
    )
