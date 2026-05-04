#!/usr/bin/env python3
"""
HSLE Run Milestone Extractor - Optimized single-pass stage analysis.

Extracts Stage 0-7 milestones from testbench.log. Uses compiled regex patterns
matched against each line in a single pass. Handles both plain and gzipped logs.

Performance: ~30s for 1M-line log on NFS (I/O bound).
"""

import re
import gzip
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple


@dataclass
class Milestone:
    """A matched milestone from testbench.log."""
    stage: int
    substage: str
    line_number: int
    line_content: str


@dataclass
class StageResult:
    """Aggregated result for one stage."""
    stage: int
    status: str  # PASS, PARTIAL, FAIL, NOT_REACHED
    milestones: List[Milestone] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    notes: str = ""


# Stage patterns: more precise than generic grep.
# (name, compiled_regex, required_for_pass)
# Patterns are tuned to actual testbench.log content from reference runs.
STAGE_PATTERNS = {
    0: [
        ("spark_session", re.compile(r"spark_session\.log|SPARK.*session", re.I), True),
        ("zebu_config", re.compile(r"ZSE5_DMR_MCP|ZeBu.*[Cc]onfig|zRci.*init", re.I), True),
    ],
    1: [
        ("emu_log_init", re.compile(r"\[emu\.devices\]|emu_log.*open|emulation.*init", re.I), True),
        ("zse5_device", re.compile(r"ZSE5.*device|zRci.*device|zRci_init|designName", re.I), True),
    ],
    2: [
        ("model_init", re.compile(r"model.*loaded|IDI.*link|socket.*config|hsle.*model", re.I), True),
        ("idi_connect", re.compile(r"IDI.*connect|idi_mux|VP.*RTL", re.I), False),
    ],
    3: [
        ("zebu_compile", re.compile(r"Compilation.*complete|ZeBu.*compil|partition.*compil|ZSYN.*done|Hardware ready", re.I), True),
    ],
    4: [
        ("simics_run", re.compile(r"simics.*run|simulation.*start|Running.*sim|continue-alone", re.I), True),
        ("vp_create", re.compile(r"processor.*creat|VP.*creat|x86.*creat|cpu.*object", re.I), False),
    ],
    5: [
        ("reset_phase_1", re.compile(r"RESET_PHASE_1\b", re.I), True),
        ("reset_phase_3", re.compile(r"RESET_PHASE_3\b", re.I), True),
        ("reset_phase_6", re.compile(r"RESET_PHASE_6\b", re.I), True),
        ("idi_mux_enable", re.compile(r"IDI.*[Mm]ux.*enabl", re.I), True),
    ],
    6: [
        ("bios_start", re.compile(r"BIOS.*[Ss]tart|SEC phase|debug_port.*0x000[1-9]", re.I), False),
        ("early_pch_init", re.compile(r"EarlyPlatformPchInit", re.I), False),
        ("start_mrc", re.compile(r"START_MRC_RUN", re.I), False),
        ("pei_memory", re.compile(r"PeiInstallPeiMemory", re.I), False),
        ("dxe_ipl", re.compile(r"DXE IPL Entry|Loading DXE CORE", re.I), False),
        ("bds_boot", re.compile(r"\[Bds\]Boot", re.I), False),
        ("exit_boot_svc", re.compile(r"ExitBootServices", re.I), False),
        ("bios_aced", re.compile(r"BIOS_TAIL_ACED", re.I), False),
    ],
    7: [
        ("os_kernel", re.compile(r"Linux version \d|SVOS.*[Bb]oot|CentOS|vmlinuz|Decompressing Linux", re.I), False),
        ("ppr_test_done", re.compile(r"PPR_TEST_DONE", re.I), True),
    ],
}

# Lines to skip for performance (comments, blank, known noise)
SKIP_RE = re.compile(r"^(?://|#\s|$|.*wait-for-log|.*Expected.*pattern)")


def open_log(run_dir):
    """Open testbench.log (plain or gzipped). Returns (file_handle, is_gz)."""
    plain = os.path.join(run_dir, "testbench.log")
    gz = os.path.join(run_dir, "testbench.log.gz")
    if os.path.exists(plain):
        return open(plain, "r", errors="replace"), False
    elif os.path.exists(gz):
        return gzip.open(gz, "rt", errors="replace"), True
    raise FileNotFoundError(f"No testbench.log in {run_dir}")


def extract_milestones(run_dir, max_lines=None):
    """
    Single-pass milestone extraction.
    
    Returns:
        (dict[int, StageResult], int total_lines)
    """
    fh, _ = open_log(run_dir)
    
    # First-match tracking (we only need first occurrence per pattern)
    found = {s: {} for s in STAGE_PATTERNS}
    # Total patterns to find
    total_needed = sum(len(pats) for pats in STAGE_PATTERNS.values())
    total_found = 0
    
    line_num = 0
    with fh:
        for line in fh:
            line_num += 1
            if max_lines and line_num > max_lines:
                break
            # Early exit if all patterns found
            if total_found >= total_needed:
                # Still count lines for total
                continue
            # Skip noise
            if len(line) < 5 or SKIP_RE.match(line):
                continue
            
            for stage, patterns in STAGE_PATTERNS.items():
                for name, regex, _ in patterns:
                    if name not in found[stage]:
                        if regex.search(line):
                            found[stage][name] = Milestone(
                                stage=stage, substage=name,
                                line_number=line_num,
                                line_content=line.strip()[:200]
                            )
                            total_found += 1
    
    # Build results
    results = {}
    for stage in sorted(STAGE_PATTERNS.keys()):
        pats = STAGE_PATTERNS[stage]
        stage_found = found[stage]
        required = [n for n, _, req in pats if req]
        missing = [n for n in required if n not in stage_found]
        all_ms = sorted(stage_found.values(), key=lambda m: m.line_number)
        
        if not missing:
            status = "PASS"
        elif not stage_found:
            prev = stage - 1
            if prev >= 0 and prev in results and results[prev].status in ("FAIL", "NOT_REACHED"):
                status = "NOT_REACHED"
            else:
                status = "FAIL"
        else:
            status = "PARTIAL"
        
        results[stage] = StageResult(stage=stage, status=status,
                                     milestones=all_ms, missing=missing)
    
    return results, line_num


def get_log_line_count(run_dir):
    """Quick line count."""
    plain = os.path.join(run_dir, "testbench.log")
    gz = os.path.join(run_dir, "testbench.log.gz")
    count = 0
    if os.path.exists(plain):
        with open(plain, "r", errors="replace") as f:
            for _ in f:
                count += 1
    elif os.path.exists(gz):
        with gzip.open(gz, "rt", errors="replace") as f:
            for _ in f:
                count += 1
    return count


def check_results_log(run_dir):
    """Read test/results.log content."""
    p = os.path.join(run_dir, "test", "results.log")
    if os.path.exists(p):
        return open(p).read().strip()
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python milestone_extractor.py <run_dir>")
        sys.exit(1)
    results, total = extract_milestones(sys.argv[1])
    print(f"Total lines: {total}")
    for s in sorted(results):
        r = results[s]
        ms_info = f" (last: {r.milestones[-1].substage} @ line {r.milestones[-1].line_number})" if r.milestones else ""
        miss = f" MISSING: {r.missing}" if r.missing else ""
        print(f"  Stage {s}: {r.status}{ms_info}{miss}")
