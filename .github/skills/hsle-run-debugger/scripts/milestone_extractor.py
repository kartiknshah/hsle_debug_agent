#!/usr/bin/env python3
"""
HSLE Run Milestone Extractor

Extracts stage milestones from testbench.log by grep-based pattern matching.
Implements the same logic as the hsle_debug agent's Step 2-3 procedure.

Usage:
    from milestone_extractor import extract_milestones
    milestones = extract_milestones("/path/to/run_dir")
    
    # Or standalone:
    python milestone_extractor.py /path/to/run_dir
"""

import re
import gzip
import os
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict


@dataclass
class Milestone:
    """A single milestone match from testbench.log"""
    stage: int
    substage: str
    line_number: int
    line_content: str
    pattern_name: str


@dataclass
class StageResult:
    """Result of checking a single stage"""
    stage: int
    status: str  # PASS, FAIL, PARTIAL, NOT_REACHED
    milestones: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    notes: str = ""


# Stage milestone patterns: (pattern_name, regex, required)
# These match the KEY GREP patterns from flow.txt
STAGE_PATTERNS = {
    0: [
        ("spark_session", r"spark_session\.log", True),
        ("zebu_config", r"ZeBu.*config|zse5.*init|ZSE5.*device", True),
    ],
    1: [
        ("emu_log_init", r"emu_log.*init|emu\.devices.*init|emulation.*start", True),
        ("zse5_device", r"ZSE5.*device|zebu.*device|ZeBu.*ready", True),
    ],
    2: [
        ("model_init", r"model.*init|IDI.*link|VP.*RTL.*connect", True),
        ("idi_link", r"IDI.*connect|idi.*link.*up", False),
    ],
    3: [
        ("zebu_compile", r"ZeBu.*compil|hardware.*compil|build.*complete", True),
    ],
    4: [
        ("simics_run", r"Simics.*run|simics.*start|Running simulation", True),
        ("vp_processor", r"VP.*processor|processor.*creat", True),
    ],
    5: [
        ("reset_phase_1", r"RESET_PHASE_1", True),
        ("reset_phase_3", r"RESET_PHASE_3", True),
        ("reset_phase_6", r"RESET_PHASE_6", True),
        ("idi_mux_enable", r"IDI.*[Mm]ux.*enabl", True),
    ],
    6: [
        ("bios_start", r"BIOS.*start|SEC.*entry|debug_port.*0x00", False),
        ("early_pch_init", r"EarlyPlatformPchInit", False),
        ("start_mrc", r"START_MRC_RUN", False),
        ("pei_install_memory", r"PeiInstallPeiMemory", False),
        ("dxe_ipl", r"DXE IPL Entry|Loading DXE CORE", False),
        ("bds_boot", r"\[Bds\]Booting", False),
        ("exit_boot_services", r"ExitBootServices|Decompressing Linux", False),
        ("bios_aced", r"BIOS_TAIL_ACED_FFFFFF00", False),
    ],
    7: [
        ("os_boot", r"Linux version|SVOS|CentOS|kernel.*boot", False),
        ("ppr_test_done", r"PPR_TEST_DONE", True),
    ],
}


def open_log(run_dir):
    """Open testbench.log (plain or gzipped).
    
    Returns:
        tuple: (file_handle, is_gzipped)
    """
    plain = os.path.join(run_dir, "testbench.log")
    gzipped = os.path.join(run_dir, "testbench.log.gz")
    
    if os.path.exists(plain):
        return open(plain, "r", errors="replace"), False
    elif os.path.exists(gzipped):
        return gzip.open(gzipped, "rt", errors="replace"), True
    else:
        raise FileNotFoundError(f"No testbench.log found in {run_dir}")


def extract_milestones(run_dir, max_lines=None):
    """
    Extract all stage milestones from testbench.log.
    
    Scans the log file once and matches all stage patterns. Returns results
    for Stages 0-7 (first cold boot milestones per flow.txt).
    
    Args:
        run_dir: Path to the HSLE run directory containing testbench.log
        max_lines: Optional limit on lines to scan (for large logs)
    
    Returns:
        tuple: (dict mapping stage number -> StageResult, total_line_count)
    """
    log_file, is_gzipped = open_log(run_dir)
    
    # Compile all patterns for efficiency
    compiled = {}
    for stage, patterns in STAGE_PATTERNS.items():
        compiled[stage] = [(name, re.compile(regex, re.IGNORECASE), req) 
                          for name, regex, req in patterns]
    
    # Track which patterns have been found (first occurrence only)
    found = {stage: {} for stage in STAGE_PATTERNS}
    
    line_num = 0
    with log_file:
        for line in log_file:
            line_num += 1
            if max_lines and line_num > max_lines:
                break
            
            # Skip common noise lines for performance
            if line.startswith("//") or line.startswith("#"):
                continue
            
            for stage, patterns in compiled.items():
                for name, regex, required in patterns:
                    if name not in found[stage]:
                        match = regex.search(line)
                        if match:
                            found[stage][name] = Milestone(
                                stage=stage,
                                substage=name,
                                line_number=line_num,
                                line_content=line.strip()[:200],
                                pattern_name=name
                            )
    
    # Build stage results with proper status logic
    results = {}
    for stage in sorted(STAGE_PATTERNS.keys()):
        patterns = STAGE_PATTERNS[stage]
        stage_found = found[stage]
        
        required_patterns = [name for name, _, req in patterns if req]
        
        required_missing = [n for n in required_patterns if n not in stage_found]
        all_found = sorted(stage_found.values(), key=lambda m: m.line_number)
        
        if not required_missing:
            status = "PASS"
        elif len(stage_found) == 0:
            # No milestones found at all for this stage
            prev_stage = stage - 1
            if prev_stage >= 0 and prev_stage in results:
                if results[prev_stage].status in ("FAIL", "NOT_REACHED"):
                    status = "NOT_REACHED"
                else:
                    status = "FAIL"
            else:
                status = "FAIL" if stage == 0 else "NOT_REACHED"
        else:
            status = "PARTIAL"
        
        results[stage] = StageResult(
            stage=stage,
            status=status,
            milestones=all_found,
            missing=required_missing
        )
    
    return results, line_num


def get_log_line_count(run_dir):
    """Get total line count of testbench.log without storing content."""
    plain = os.path.join(run_dir, "testbench.log")
    gzipped = os.path.join(run_dir, "testbench.log.gz")
    
    if os.path.exists(plain):
        count = 0
        with open(plain, "r", errors="replace") as f:
            for _ in f:
                count += 1
        return count
    elif os.path.exists(gzipped):
        count = 0
        with gzip.open(gzipped, "rt", errors="replace") as f:
            for _ in f:
                count += 1
        return count
    return 0


def check_results_log(run_dir):
    """Check test/results.log for run outcome.
    
    Returns:
        str or None: Content of results.log, or None if not found
    """
    results_path = os.path.join(run_dir, "test", "results.log")
    if not os.path.exists(results_path):
        return None
    with open(results_path, "r") as f:
        content = f.read().strip()
    return content


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: milestone_extractor.py <run_dir>")
        sys.exit(1)
    
    run_dir = sys.argv[1]
    results, total_lines = extract_milestones(run_dir)
    
    print(f"\nHSLE Run Milestone Extraction: {run_dir}")
    print(f"Total log lines: {total_lines}")
    print(f"{'='*70}")
    
    for stage in sorted(results.keys()):
        r = results[stage]
        print(f"  Stage {stage}: {r.status}")
        for m in r.milestones:
            print(f"    [{m.substage}] line {m.line_number}: {m.line_content[:80]}")
        if r.missing:
            print(f"    MISSING: {', '.join(r.missing)}")
    
    # Check results.log
    res = check_results_log(run_dir)
    if res:
        print(f"\n  results.log: {res}")
    else:
        print(f"\n  results.log: NOT FOUND")
