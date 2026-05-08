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


def extract_hsle_model(run_dir: str) -> str:
    """
    Extract HSLE Model from testbench.log or emurun.dut_cfg.
    Looks for: "The model we are running on is mcp_1s_ici"
    Returns: Model name (e.g., 'mcp_1s_ici') or "N/A"
    """
    log_path = Path(run_dir) / "testbench.log"
    log_gz_path = Path(run_dir) / "testbench.log.gz"
    
    model_pattern = re.compile(r"The model we are running on is\s+([a-z_0-9]+)")
    model_pattern_imh = re.compile(r"Read the model from emurun\.dut_cfg file:\s+([a-z_0-9]+)")
    
    try:
        # Try testbench.log first
        if log_gz_path.exists():
            with gzip.open(log_gz_path, "rt", errors="ignore") as f:
                for line in f:
                    match = model_pattern.search(line) or model_pattern_imh.search(line)
                    if match:
                        return match.group(1)
        elif log_path.exists():
            with open(log_path, "r", errors="ignore") as f:
                for line in f:
                    match = model_pattern.search(line) or model_pattern_imh.search(line)
                    if match:
                        return match.group(1)
        
        # Fallback: check emurun.dut_cfg
        cfg_path = Path(run_dir) / "emurun.dut_cfg"
        cfg_gz_path = Path(run_dir) / "emurun.dut_cfg.gz"
        cfg_pattern = re.compile(r"emu::build_info::model_name\s*=\s*([a-z_0-9]+)")
        
        if cfg_gz_path.exists():
            with gzip.open(cfg_gz_path, "rt", errors="ignore") as f:
                for line in f:
                    match = cfg_pattern.search(line)
                    if match:
                        return match.group(1)
        elif cfg_path.exists():
            with open(cfg_path, "r", errors="ignore") as f:
                for line in f:
                    match = cfg_pattern.search(line)
                    if match:
                        return match.group(1)
    except Exception:
        pass
    
    return "N/A"


def extract_os_image(run_dir: str) -> str:
    """
    Extract OS Image name from testbench.log.
    Looks for: 'os_image : /path/to/svos/26WW09.3/...'
    Returns: OS image version (e.g., 'SVOS 26WW09.3') or "N/A"
    """
    log_path = Path(run_dir) / "testbench.log"
    log_gz_path = Path(run_dir) / "testbench.log.gz"
    
    # Pattern to extract OS image path
    os_pattern = re.compile(r"os_image\s*:\s*([^\s]+)")
    
    try:
        if log_gz_path.exists():
            with gzip.open(log_gz_path, "rt", errors="ignore") as f:
                for line in f:
                    match = os_pattern.search(line)
                    if match:
                        path = match.group(1)
                        # Extract version from path like "/nfs/site/disks/ive_oks_dppci_002/dmr/os/svos/26WW09.3/..."
                        if "svos" in path.lower():
                            version_match = re.search(r"svos[/\\]([\d.A-Za-z]+)", path, re.IGNORECASE)
                            if version_match:
                                return f"SVOS {version_match.group(1)}"
                            return "SVOS"
                        elif "centos" in path.lower():
                            version_match = re.search(r"centos[/\\]([\d.A-Za-z]+)", path, re.IGNORECASE)
                            if version_match:
                                return f"CentOS {version_match.group(1)}"
                            return "CentOS"
                        else:
                            # Extract last meaningful part of path
                            parts = path.rstrip("/").split("/")
                            return parts[-1] if parts else path
        elif log_path.exists():
            with open(log_path, "r", errors="ignore") as f:
                for line in f:
                    match = os_pattern.search(line)
                    if match:
                        path = match.group(1)
                        if "svos" in path.lower():
                            version_match = re.search(r"svos[/\\]([\d.A-Za-z]+)", path, re.IGNORECASE)
                            if version_match:
                                return f"SVOS {version_match.group(1)}"
                            return "SVOS"
                        elif "centos" in path.lower():
                            version_match = re.search(r"centos[/\\]([\d.A-Za-z]+)", path, re.IGNORECASE)
                            if version_match:
                                return f"CentOS {version_match.group(1)}"
                            return "CentOS"
                        else:
                            parts = path.rstrip("/").split("/")
                            return parts[-1] if parts else path
    except Exception:
        pass
    
    return "N/A"
