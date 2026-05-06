#!/usr/bin/env python3
"""Helpers for writing generated artifacts into the repo-local result directory."""

from pathlib import Path


SUMMARY_FILENAME = "hsle_debug_agent_summary.txt"
TEMP_DIRNAME = "tmp"


def repo_root() -> Path:
    """Return the repository root for this scripts package."""
    return Path(__file__).resolve().parents[4]


def result_dir() -> Path:
    """Return the repo-local result directory, creating it if needed."""
    path = repo_root() / "result"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir() -> Path:
    """Return the repo-local scratch directory for temporary artifacts."""
    path = result_dir() / TEMP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_summary_output_path(run_dir: str) -> str:
    """Return the default summary output path under the result directory."""
    run_name = Path(run_dir).resolve().name or "hsle_run"
    return str(result_dir() / f"{run_name}_{SUMMARY_FILENAME}")


import gzip
import re


def extract_bios_id(run_dir: str) -> str:
    """
    Extract BIOS Version from testbench.log serconsole output.
    Looks for pattern: BIOS ID: <version>
    Returns: BIOS version string or "NOT FOUND"
    """
    log_path = Path(run_dir) / "testbench.log"
    log_gz_path = Path(run_dir) / "testbench.log.gz"
    
    bios_pattern = re.compile(r"BIOS ID:\s+([A-Za-z0-9.]+)")
    
    try:
        if log_gz_path.exists():
            with gzip.open(log_gz_path, "rt", errors="ignore") as f:
                for line in f:
                    match = bios_pattern.search(line)
                    if match:
                        return match.group(1)
        elif log_path.exists():
            with open(log_path, "r", errors="ignore") as f:
                for line in f:
                    match = bios_pattern.search(line)
                    if match:
                        return match.group(1)
    except Exception:
        pass
    
    return "NOT FOUND"


def ensure_parent_dir(output_path: str) -> str:
    """Create the parent directory for a caller-specified output path."""
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def temp_artifact_path(name: str) -> str:
    """Return a scratch file path under result/tmp for agent-created artifacts."""
    safe_name = Path(name).name or "temp.txt"
    return str(temp_dir() / safe_name)
