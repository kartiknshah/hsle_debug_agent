#!/usr/bin/env python3
"""
HSLE Run Analyzer - Single-pass unified log analysis.

Replaces separate milestone_extractor.py + reset_detector.py with ONE pass
through testbench.log that extracts everything simultaneously:
  - Stage 0-7 milestones (from flow.txt)
  - Reset triggers and type classification (from cold/warm/global_reset_flow.txt)
  - Reset stage markers (Stages 8-13)
  - Failure context (lines around the failure point)

Performance: ~30-60s for 1M-line log (single I/O pass, all regex compiled).
Token savings: Agent calls this script ONCE, reads JSON output, writes summary.
No manual grepping needed.

Usage:
    python3 hsle_analyzer.py <run_directory>
    python3 hsle_analyzer.py <run_directory> --json   # machine-readable
    python3 hsle_analyzer.py <run_directory> --summary # auto-generate summary file
"""

import re
import gzip
import os
import sys
import json
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from pathlib import Path

from output_paths import default_summary_output_path, ensure_parent_dir, extract_bios_id, extract_hsle_model, extract_os_image


# ===========================================================================
#  COMPILED PATTERN DEFINITIONS
#  Encodes knowledge from: flow.txt, bios_flow.txt, reset_phase_flow.txt,
#  cold_reset_flow.txt, warm_reset_flow.txt, global_reset_flow.txt
# ===========================================================================

# --- Stage 0-7: First boot milestones (from flow.txt / flow_imh.txt) ---
# IMPORTANT: Patterns must be precise enough to avoid false matches.
# - Stage 0-1: Well-defined SPARK/sle.simics markers (reliable)
# - Stage 2-4: Setup/mount/compile phases -- markers vary by build; use
#   non-required patterns and rely on Stage 5 (hybrid_switch) as the gate
# - Stage 5: Hybrid switch = definitive RTL-to-VP handoff (critical)
# - Stage 6-7: BIOS/OS boot markers from serconsole stream (reliable)
STAGE_PATTERNS = {
    0: [
        ("spark_session", re.compile(r"spark_session\.log|SPARK.*[Ss]ession|SPARK Version:", re.I), True),
        ("zebu_config", re.compile(r"ZSE[45]_DMR_(?:MCP|IMH)|ZeBu.*[Cc]onfig|zRci.*init|designName", re.I), True),
    ],
    1: [
        ("sle_simics_setup", re.compile(r"Running sle\.simics setup_script"), True),
        ("model_detect", re.compile(r"determine_model\.py|model we are running on is|Read the model from emurun\.dut_cfg"), True),
    ],
    2: [
        ("rti_pre_cycle", re.compile(r"RTI:\s*Pre Cycle 0"), False),
        ("rti_pre_mount", re.compile(r"RTI:\s*Pre Mount"), False),
        ("rti_mounted", re.compile(r"RTI:\s*Mounted"), False),
    ],
    3: [
        ("zebu_memory_load", re.compile(r"-- ZeBu : (?:zServer|simics-common) :"), False),
    ],
    4: [
        ("fuse_load_start", re.compile(r"\[fuse_load\.py\].*(?:STARTING|Model:)"), False),
        ("post_setup_script", re.compile(r"Running post setup_script:"), False),
    ],
    5: [
        ("reset_phase_1", re.compile(r"RESET_PHASE_1\b"), False),
        ("reset_phase_3", re.compile(r"RESET_PHASE_3\b"), False),
        ("reset_phase_end", re.compile(r"RESET_PHASE_6\b|RESET_SEQ_PREPARE_FOR_LOOP"), False),
        ("hybrid_switch", re.compile(r"IDI.*[Mm]ux.*enabl|Enabling simics cores"), True),
    ],
    6: [
        ("bios_start", re.compile(r"BIOS.*[Ss]tart|SEC phase|debug_port.*0x000[1-9]"), False),
        ("early_pch_init", re.compile(r"EarlyPlatformPchInit"), False),
        ("start_mrc", re.compile(r"START_MRC_RUN"), False),
        ("pei_memory", re.compile(r"PeiInstallPeiMemory"), False),
        ("dxe_ipl", re.compile(r"DXE IPL Entry|Loading DXE CORE"), False),
        ("bds_boot", re.compile(r"\[Bds\]Boot"), False),
        ("exit_boot_svc", re.compile(r"ExitBootServices"), False),
        ("bios_aced", re.compile(r"BIOS_TAIL_ACED"), False),
    ],
    7: [
        ("os_kernel", re.compile(r"Linux version \d|SVOS.*[Bb]oot|CentOS|vmlinuz|Decompressing Linux"), False),
        ("ppr_test_done", re.compile(r"PPR_TEST_DONE"), True),
    ],
}

# --- Reset detection patterns (from cold/warm/global_reset_flow.txt) ---
RESET_PATTERNS = {
    # Stage 8: Trigger
    "ppr_test_done":      re.compile(r"PPR_TEST_DONE"),
    "ppr_got_cf9":        re.compile(r"PPR check: GOT RESET CF9\s+(\d+)"),
    "rst_tag_triggering": re.compile(r"RST_TAG:\s*triggering\s+(\S+)"),
    "cold_through":       re.compile(r"COLD_RESET\s+through\s+(.+?)(?:\s+triggered|$)"),
    "warm_through":       re.compile(r"WARM_RESET\s+through\s+(.+?)(?:\s+triggered|$)"),
    "global_through":     re.compile(r"GLOBAL_RESET\s+through\s+(.+?)(?:\s+triggered|$)"),
    "global_sequence":    re.compile(r"RST_TAG\s+Global\s+reset\s+sequence\s+will\s+be\s+called"),
    "agr_event":          re.compile(r"RST_TAG\s+AGR\s+event\s+triggered"),
    "awr_event":          re.compile(r"RST_TAG\s+AWR\s+event\s+triggered"),
    "swr_event":          re.compile(r"RST_TAG\s+Reset_BTN\s+event\s+triggered"),
    # Stage 9: Hardware entry
    "hsle_start_reset":   re.compile(r"RST_TAG\s+HSLE\s+starting\s+reset"),
    "cbb_event":          re.compile(r"RST_TAG\s+Creating\s+a\s+CBB\s+event\s+for\s+reset:\s*(\w+)"),
    "pltrst_sync":        re.compile(r"RST_TAG\s+waiting\s+for\s+PLTRST_SYNC"),
    "gbl_rst_warn":       re.compile(r"GBL_RST_WARN|RST_TAG.*[Gg]lobal.*[Rr]eset.*[Ww]arn"),
    "global_reset_n":     re.compile(r"GLOBAL_RESET_N.*(?:forced|assert)|RST_TAG.*GLOBAL_RESET_N"),
    "pwrgood_deassert":   re.compile(r"RST_TAG\s+Forced.*PWRGOOD.*to\s+0|PWRGOOD.*de-?assert"),
    "slp_assertion":      re.compile(r"SLP_S[345].*assert|RST_TAG.*SLP_S[345]"),
    "fuse_reload":        re.compile(r"fuse.*reload|FUSE.*RELOAD|reload_fuse"),
    "reset_n_assert":     re.compile(r"RST_TAG.*XX_RESET_N.*assert|RESET_N.*(?:forced|assert)"),
    "fake_go_rsp":        re.compile(r"Fake\s+GO\s+RSP|fake.*go.*rsp", re.I),
    "begin_reset_flow":   re.compile(r"BEGIN_RESET_FLOW"),
    "reset_triggered":    re.compile(r"RST_TAG\s+Reset\s+triggered"),
    # Stage 10: Second boot RTL
    "boot_fsm_start":     re.compile(r"BOOT_FSM(?:\s+state\s+0x0?1\b|_(?:SWITCH_RO_TO_XTAL|START_FUSE_SENSE|DFX_AGG_FUSE_PULL)\b)"),
    "boot_fsm_end":       re.compile(r"BOOT_FSM\s+state\s+0x(?:41|3c)\b"),
    "reset_phase_3_2nd":  re.compile(r"RESET_PHASE_3"),
    "reset_phase_6_2nd":  re.compile(r"RESET_PHASE_6"),
    "bios_first_fetch":   re.compile(r"RST_TAG\s+waiting\s+for\s+BIOS\s+first\s+fetch"),
    "icecode_reload":     re.compile(r"icecode_load|IceCode.*reload", re.I),
    "hwrs_complete":      re.compile(r"HWRS_RESET_COMPLETE|HWRS.*reset.*complete", re.I),
    # Stage 11: Second BIOS
    "idi_mux_2nd":        re.compile(r"IDI.*[Mm]ux.*enabl|Enabling simics cores"),
    "bios_aced_2nd":      re.compile(r"BIOS_TAIL_ACED"),
    "reset_phase_7":      re.compile(r"RESET_PHASE_7"),
    # Stage 13: Termination
    "auto_exit":          re.compile(r"Auto exit triggered|RESET_TEST_COMPLETE"),
    "rca_check":          re.compile(r"rca_check_found:\s*(\d+)"),
}

# Lines to skip (noise from wait-for-log registrations, hap callbacks)
NOISE_RE = re.compile(r"wait-for-|Watching for|Expected.*pattern|hap_callback")

# FAST PRE-FILTER: Single regex of all critical keywords.
# Only lines matching this get expensive individual pattern checks.
# Skips ~95% of log lines, cutting runtime by 5-10x.
# FAST pre-filter: tuple of keywords for O(n) string 'in' check (10-50x faster than regex)
_PREFILTER_KEYWORDS = (
    # Reset / boot flow
    'RST_TAG', 'PPR_TEST_DONE', 'RESET_PHASE', 'RESET_SEQ_', 'BOOT_FSM', 'BIOS_TAIL',
    'BEGIN_RESET', 'CF9', 'PWRGOOD', 'PWR_OK', 'SLP_S', 'PLTRST',
    'GBL_RST', 'GLOBAL_RESET_N', 'icecode', 'IceCode', 'fuse_reload',
    'FUSE_RELOAD', 'Fake GO', 'fake_go', 'rca_check', 'Auto exit',
    'RESET_TEST', 'PPR check',
    # Stage 0-1: SPARK + sle.simics setup
    'spark_session', 'SPARK', 'ZSE5_DMR', 'ZSE4_DMR', 'ZeBu', 'zRci',
    'sle.simics setup_script', 'determine_model.py',
    'model we are running on is', 'Read the model from emurun',
    # Stage 2: RTI markers
    'RTI:',
    # Stage 3: ZeBu memory loading
    '-- ZeBu :',
    # Stage 4: fuse loading + post setup
    'fuse_load.py', 'post setup_script',
    # Stage 5: hybrid switch
    'Enabling simics cores', 'IDI', 'idi_mux', 'HWRS', 'primecode',
    # Stage 6: BIOS
    'ExitBootServices', 'DXE IPL', 'Loading DXE', 'PeiInstall',
    'START_MRC', 'EarlyPlatformPch', 'BIOS ID:', 'debug_port',
    # Stage 7: OS boot
    'SVOS', 'CentOS', 'Linux version', 'Decompressing',
    'Bds', 'GRUB', 'ramdisk', 'vmlinuz',
    # Reset triggers
    'Reset_BTN', 'AGR event', 'AWR event', 'COLD_RESET',
    'WARM_RESET', 'GLOBAL_RESET',
)



# ===========================================================================
#  DATA STRUCTURES
# ===========================================================================

@dataclass
class Milestone:
    stage: int
    substage: str
    line_number: int
    content: str

@dataclass
class StageResult:
    stage: int
    status: str  # PASS, PARTIAL, FAIL, NOT_REACHED
    milestones: List[Milestone] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

@dataclass
class ResetCycle:
    cycle_number: int
    reset_type: str = "UNKNOWN"
    trigger_source: str = "UNKNOWN"
    cf9_value: str = ""
    trigger_line: int = 0
    trigger_content: str = ""
    # Stage 9
    hsle_start_reset_line: int = 0
    cbb_event_line: int = 0
    cbb_event_type: str = ""
    pltrst_sync_line: int = 0
    gbl_rst_warn_line: int = 0
    global_reset_n_line: int = 0
    pwrgood_deassert_line: int = 0
    slp_assertion_line: int = 0
    fuse_reload_line: int = 0
    reset_n_assert_line: int = 0
    fake_go_rsp_line: int = 0
    begin_reset_flow_line: int = 0
    reset_triggered_line: int = 0
    # Stage 10
    primecode_start_line: int = 0
    primecode_end_line: int = 0
    reset_phase_3_line: int = 0
    reset_phase_6_line: int = 0
    bios_first_fetch_line: int = 0
    icecode_reload_line: int = 0
    hwrs_complete_line: int = 0
    # Stage 11
    idi_mux_line: int = 0
    bios_aced_line: int = 0
    reset_phase_7_line: int = 0
    # Stage 12
    ppr_test_done_line: int = 0
    # Stage 13
    auto_exit_line: int = 0
    # Evaluation
    status: str = "UNKNOWN"
    failing_stage: int = 0
    failing_substage: str = ""
    failure_detail: str = ""
    failure_context: List[str] = field(default_factory=list)
    # Flow validation (type-specific checks from *_reset_flow.txt)
    flow_checks: Dict[str, str] = field(default_factory=dict)


# ===========================================================================
#  SINGLE-PASS ANALYZER
# ===========================================================================

def analyze_run(run_dir, generate_summary=False, output_path=None):
    """
    Single-pass analysis of testbench.log.
    
    Returns dict with:
      - stages: Stage 0-7 results
      - reset_cycles: list of ResetCycle
      - summary: overall metrics (line count, PPR count, result, etc.)
      - failure_context: lines around first failure point
    """
    log_path = _find_log(run_dir)
    
    # State
    stage_found = {s: {} for s in STAGE_PATTERNS}
    reset_events = []
    ppr_lines = []
    rca_found = 0
    context_buf = []  # rolling buffer for failure context
    total_lines = 0
    
    # Track if we're past first reset trigger (for Stage 10+ pattern matching)
    first_trigger_line = 0
    
    # Single pass
    t_start = time.time()
    fh = _open_log(log_path)
    with fh:
        for line in fh:
            total_lines += 1
            
            # Skip short lines
            if len(line) < 5:
                continue
            # FAST PRE-FILTER: skip lines without any keyword
            if not any(kw in line for kw in _PREFILTER_KEYWORDS):
                continue
            # Skip noise
            if NOISE_RE.search(line):
                continue
            
            stripped = line.strip()[:250]
            
            # Rolling context buffer (last 10 meaningful lines)
            if len(stripped) > 10:
                context_buf.append((total_lines, stripped))
                if len(context_buf) > 10:
                    context_buf.pop(0)
            
            # --- Stage 0-7 milestones (only before first reset trigger) ---
            if not first_trigger_line:
                for stage, patterns in STAGE_PATTERNS.items():
                    for name, regex, _ in patterns:
                        if name not in stage_found[stage]:
                            if regex.search(line):
                                stage_found[stage][name] = Milestone(
                                    stage=stage, substage=name,
                                    line_number=total_lines,
                                    content=stripped[:200]
                                )
            
            # --- Reset detection (always) ---
            # PPR_TEST_DONE
            if RESET_PATTERNS["ppr_test_done"].search(line):
                ppr_lines.append((total_lines, stripped[:200]))
            
            # RCA check
            m = RESET_PATTERNS["rca_check"].search(line)
            if m:
                rca_found = int(m.group(1))
            
            # Reset triggers
            triggered = False
            for pname in ("ppr_got_cf9", "rst_tag_triggering", "cold_through",
                          "warm_through", "global_through", "agr_event",
                          "awr_event", "swr_event"):
                m = RESET_PATTERNS[pname].search(line)
                if m:
                    reset_events.append((total_lines, pname, m.groups(),
                                        stripped, list(context_buf[-5:])))
                    if not first_trigger_line:
                        first_trigger_line = total_lines
                    triggered = True
                    break
            
            # Reset flow markers (post-trigger)
            if not triggered and first_trigger_line:
                for pname in ("hsle_start_reset", "cbb_event", "pltrst_sync", "global_sequence",
                              "gbl_rst_warn", "global_reset_n", "pwrgood_deassert",
                              "slp_assertion", "fuse_reload", "reset_n_assert",
                              "fake_go_rsp", "begin_reset_flow", "reset_triggered",
                              "boot_fsm_start", "boot_fsm_end",
                              "reset_phase_3_2nd", "reset_phase_6_2nd",
                              "bios_first_fetch", "icecode_reload", "hwrs_complete",
                              "idi_mux_2nd", "bios_aced_2nd", "reset_phase_7",
                              "auto_exit"):
                    m = RESET_PATTERNS[pname].search(line)
                    if m:
                        reset_events.append((total_lines, pname, m.groups(),
                                            stripped, list(context_buf[-5:])))
                        break
    
    # Build stage results
    stages = _build_stage_results(stage_found)
    _normalize_stage6_completion(stages)
    
    # Build reset cycles
    cycles = _assemble_cycles(reset_events, ppr_lines)
    
    # Classify origin
    origin = "NONE"
    if cycles:
        first_ppr_before_trigger = any(
            p[0] < cycles[0].trigger_line for p in ppr_lines
        )
        origin = "POST_OS_BOOT" if first_ppr_before_trigger else "BIOS_INITIATED"
    
    # Determine overall result
    if not cycles:
        result = _cold_boot_result(stages)
    else:
        result = _reset_result(stages, cycles, ppr_lines)
    
    # Check results.log
    results_log = _read_results_log(run_dir)
    
    summary_info = {
        "run_dir": run_dir,
        "total_lines": total_lines,
        "result": result,
        "scenario": "reset" if cycles else "cold_boot",
        "reset_origin": origin,
        "reset_cycle_count": len(cycles),
        "ppr_total_count": len(ppr_lines),
        "ppr_lines": [(l, c) for l, c in ppr_lines],
        "results_log": results_log,
        "rca_check_found": rca_found,
    }
    
    analysis = {
        "stages": stages,
        "reset_cycles": cycles,
        "summary": summary_info,
        "run_dir": run_dir,
        "log_path": log_path,
    }
    
    # Generate summary file if requested
    if generate_summary:
        out = _write_summary(analysis, output_path)
        summary_info["summary_file"] = out
    
    return analysis


# ===========================================================================
#  INTERNAL HELPERS
# ===========================================================================

def _find_log(run_dir):
    plain = os.path.join(run_dir, "testbench.log")
    gz = os.path.join(run_dir, "testbench.log.gz")
    if os.path.exists(plain):
        return plain
    elif os.path.exists(gz):
        return gz
    raise FileNotFoundError(f"No testbench.log in {run_dir}")


def _open_log(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, "r", errors="replace")


def _read_results_log(run_dir):
    p = os.path.join(run_dir, "test", "results.log")
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def _build_stage_results(stage_found):
    results = {}
    for stage in sorted(STAGE_PATTERNS.keys()):
        pats = STAGE_PATTERNS[stage]
        found = stage_found[stage]
        required = [n for n, _, req in pats if req]
        missing = [n for n in required if n not in found]
        milestones = sorted(found.values(), key=lambda m: m.line_number)
        
        if not missing:
            status = "PASS"
        elif not found:
            prev = stage - 1
            if prev >= 0 and prev in results and results[prev].status in ("FAIL", "NOT_REACHED"):
                status = "NOT_REACHED"
            else:
                status = "FAIL"
        else:
            status = "PARTIAL"
        
        results[stage] = StageResult(stage=stage, status=status,
                                     milestones=milestones, missing=missing)
    return results


def _normalize_stage6_completion(stages):
    """Downgrade Stage 6 when BIOS completion markers were never observed."""
    s6 = stages.get(6)
    s7 = stages.get(7)
    if not s6 or s6.status != "PASS":
        return

    completion_markers = {"exit_boot_svc", "bios_aced"}
    has_bios_completion = any(m.substage in completion_markers for m in s6.milestones)

    # If BIOS completion was never observed and Stage 7 did not pass, Stage 6 is partial.
    if not has_bios_completion and (not s7 or s7.status != "PASS"):
        s6.status = "PARTIAL"
        if "bios_completion_marker" not in s6.missing:
            s6.missing.append("bios_completion_marker")


def _assemble_cycles(events, ppr_lines):
    """Build ResetCycle objects from event stream."""
    cycles = []
    current = None
    cycle_num = 0
    
    TRIGGERS = {"ppr_got_cf9", "rst_tag_triggering", "cold_through",
                "warm_through", "global_through", "agr_event",
                "awr_event", "swr_event"}
    
    for evt_line, evt_type, groups, content, ctx in events:
        if evt_type in TRIGGERS:
            # New trigger: close previous cycle if it has a reset start
            if current and current.hsle_start_reset_line > 0:
                cycles.append(current)
                current = None
            if current is None:
                cycle_num += 1
                current = ResetCycle(cycle_number=cycle_num)
                current.trigger_line = evt_line
                current.trigger_content = content
            _classify_trigger(current, evt_type, groups, content)
        elif current:
            _fill_marker(current, evt_type, groups, evt_line, ctx)
    
    if current:
        cycles.append(current)
    
    # Assign PPR_TEST_DONE to cycles
    _assign_ppr(cycles, ppr_lines)
    
    # Evaluate each cycle
    for c in cycles:
        _evaluate_cycle(c)
        _validate_flow_type(c)
    
    return cycles


def _classify_trigger(cycle, evt_type, groups, content):
    """Classify reset type and trigger source."""
    if evt_type == "ppr_got_cf9":
        val = int(groups[0]) if groups else 0
        cycle.cf9_value = hex(val)
        if val in (14, 0xE):
            cycle.reset_type = "COLD"
        elif val in (6, 0x6):
            if cycle.reset_type == "UNKNOWN":
                cycle.reset_type = "WARM"  # may become GLOBAL if gbl_etr3 set
        cycle.trigger_source = "CF9_WRITE"
    elif evt_type == "cold_through":
        cycle.reset_type = "COLD"
        src = groups[0] if groups else ""
        cycle.trigger_source = "OS_REBOOT" if "OS" in src.upper() or "reboot" in src.lower() else "SOLAR"
    elif evt_type == "warm_through":
        cycle.reset_type = "WARM"
        src = groups[0] if groups else ""
        cycle.trigger_source = "OS_REBOOT" if "OS" in src.upper() or "reboot" in src.lower() else "SOLAR"
    elif evt_type in ("global_through", "global_sequence"):
        cycle.reset_type = "GLOBAL"
        if not cycle.trigger_source or cycle.trigger_source == "CF9_WRITE":
            cycle.trigger_source = "CF9_WRITE"  # CF9 with gbl_etr3 = global
        else:
            cycle.trigger_source = "SOLAR"
    elif evt_type == "agr_event":
        cycle.reset_type = "GLOBAL"
        cycle.trigger_source = "AGR"
    elif evt_type == "awr_event":
        cycle.reset_type = "WARM"
        cycle.trigger_source = "AWR"
    elif evt_type == "swr_event":
        cycle.reset_type = "WARM"
        cycle.trigger_source = "SWR"
    elif evt_type == "rst_tag_triggering":
        name = groups[0].lower() if groups else ""
        if "cold" in name:
            cycle.reset_type = "COLD"; cycle.trigger_source = "OS_REBOOT"
        elif "warm" in name:
            cycle.reset_type = "WARM"; cycle.trigger_source = "OS_REBOOT"
        elif "global" in name:
            cycle.reset_type = "GLOBAL"; cycle.trigger_source = "SOLAR"
        elif "agr" in name:
            cycle.reset_type = "GLOBAL"; cycle.trigger_source = "AGR"
        elif "awr" in name:
            cycle.reset_type = "WARM"; cycle.trigger_source = "AWR"
        elif "swr" in name:
            cycle.reset_type = "WARM"; cycle.trigger_source = "SWR"


def _fill_marker(current, evt_type, groups, evt_line, ctx):
    """Fill stage markers into current cycle."""
    mapping = {
        "hsle_start_reset": "hsle_start_reset_line",
        "cbb_event": "cbb_event_line",
        "pltrst_sync": "pltrst_sync_line",
        "gbl_rst_warn": "gbl_rst_warn_line",
        "global_reset_n": "global_reset_n_line",
                    "global_sequence": None,  # just upgrade type
        "pwrgood_deassert": "pwrgood_deassert_line",
        "slp_assertion": "slp_assertion_line",
        "fuse_reload": "fuse_reload_line",
        "reset_n_assert": "reset_n_assert_line",
        "fake_go_rsp": "fake_go_rsp_line",
        "begin_reset_flow": "begin_reset_flow_line",
        "reset_triggered": "reset_triggered_line",
        "boot_fsm_start": "primecode_start_line",
        "boot_fsm_end": "primecode_end_line",
        "reset_phase_3_2nd": "reset_phase_3_line",
        "reset_phase_6_2nd": "reset_phase_6_line",
        "bios_first_fetch": "bios_first_fetch_line",
        "icecode_reload": "icecode_reload_line",
        "hwrs_complete": "hwrs_complete_line",
        "idi_mux_2nd": "idi_mux_line",
        "bios_aced_2nd": "bios_aced_line",
        "reset_phase_7": "reset_phase_7_line",
        "auto_exit": "auto_exit_line",
    }
    
    attr = mapping.get(evt_type)
    if not attr:
        # Special case: global_sequence upgrades cycle type to GLOBAL
        if evt_type == "global_sequence":
            current.reset_type = "GLOBAL"
        return
    
    # Only fill if not already set (first occurrence per cycle)
    if getattr(current, attr, 0) != 0:
        return
    
    # Stage 10+ markers: only after begin_reset_flow
    post_begin = {"primecode_start_line", "primecode_end_line",
                  "reset_phase_3_line", "reset_phase_6_line",
                  "bios_first_fetch_line", "icecode_reload_line",
                  "hwrs_complete_line", "idi_mux_line",
                  "bios_aced_line", "reset_phase_7_line"}
    if attr in post_begin and current.begin_reset_flow_line == 0:
        return
    
    # Stage 9 markers: only after hsle_start_reset
    stage9 = {"pltrst_sync_line", "gbl_rst_warn_line", "global_reset_n_line",
              "pwrgood_deassert_line", "slp_assertion_line", "fuse_reload_line",
              "reset_n_assert_line", "fake_go_rsp_line"}
    if attr in stage9 and current.hsle_start_reset_line == 0:
        return
    
    setattr(current, attr, evt_line)
    
    # Special: cbb_event also captures type
    if evt_type == "cbb_event" and groups:
        current.cbb_event_type = groups[0].upper()
        if current.reset_type == "UNKNOWN":
            current.reset_type = groups[0].upper()
    
    # Auto_exit: capture context
    if evt_type == "auto_exit":
        current.failure_context = [c[1] for c in ctx]


def _assign_ppr(cycles, ppr_lines):
    """Assign PPR_TEST_DONE occurrences to cycles by position."""
    if not ppr_lines or not cycles:
        return
    ppr_idx = 0
    for cycle in cycles:
        ref_line = cycle.begin_reset_flow_line or cycle.trigger_line
        while ppr_idx < len(ppr_lines) and ppr_lines[ppr_idx][0] <= ref_line:
            ppr_idx += 1
        if ppr_idx < len(ppr_lines):
            cycle.ppr_test_done_line = ppr_lines[ppr_idx][0]
            ppr_idx += 1


def _evaluate_cycle(cycle):
    """Determine pass/fail for a reset cycle."""
    if cycle.ppr_test_done_line > 0:
        cycle.status = "PASS"
        return
    if cycle.auto_exit_line > 0 and cycle.bios_aced_line > 0:
        cycle.status = "PASS"
        return
    
    # Walk through stages to find failure point
    if cycle.hsle_start_reset_line == 0:
        cycle.status = "FAIL"; cycle.failing_stage = 8
        cycle.failure_detail = "Reset trigger detected but HSLE reset never started"
    elif cycle.begin_reset_flow_line == 0:
        cycle.status = "FAIL"; cycle.failing_stage = 9
        if cycle.reset_triggered_line == 0:
            cycle.failure_detail = "HSLE started but RTL Reset triggered never seen"
        else:
            cycle.failure_detail = "RTL Reset triggered but BEGIN_RESET_FLOW missing"
    elif cycle.bios_first_fetch_line == 0:
        cycle.status = "FAIL"; cycle.failing_stage = 10
        if cycle.primecode_start_line == 0:
            cycle.failing_substage = "10.0"
            cycle.failure_detail = "BEGIN_RESET_FLOW but primecode never started"
        elif cycle.reset_phase_6_line == 0:
            cycle.failing_substage = "10.3"
            cycle.failure_detail = "Primecode started but RESET_PHASE_6 never completed"
        else:
            cycle.failing_substage = "10.4"
            cycle.failure_detail = "RESET_PHASE_6 done but BIOS first fetch never reached"
    elif cycle.bios_aced_line == 0:
        cycle.status = "FAIL"; cycle.failing_stage = 11
        cycle.failing_substage = "11.2"
        cycle.failure_detail = "BIOS first fetch wait seen but BIOS never completed (ACED missing)"
    else:
        cycle.status = "FAIL"; cycle.failing_stage = 12
        cycle.failure_detail = "BIOS completed but second PPR_TEST_DONE missing"


def _validate_flow_type(cycle):
    """
    Type-specific flow validation.
    Encodes rules from cold_reset_flow.txt / warm_reset_flow.txt / global_reset_flow.txt.
    """
    checks = {}
    if cycle.reset_type == "COLD":
        checks["pltrst_sync"] = "PASS" if cycle.pltrst_sync_line else "MISSING"
        checks["pwrgood_deassert"] = "PASS" if cycle.pwrgood_deassert_line else "WARN"
        checks["fuse_reload"] = "PASS" if cycle.fuse_reload_line else "WARN"
        if cycle.gbl_rst_warn_line:
            checks["gbl_rst_warn_unexpected"] = "UNEXPECTED"
        if cycle.fake_go_rsp_line:
            checks["fake_go_rsp_unexpected"] = "UNEXPECTED"
    elif cycle.reset_type == "WARM":
        checks["pltrst_sync"] = "PASS" if cycle.pltrst_sync_line else "WARN"
        checks["no_pwrgood_deassert"] = "PASS" if not cycle.pwrgood_deassert_line else "UNEXPECTED"
        checks["no_fuse_reload"] = "PASS" if not cycle.fuse_reload_line else "UNEXPECTED"
        checks["no_slp_assertion"] = "PASS" if not cycle.slp_assertion_line else "UNEXPECTED"
        checks["reset_n_only"] = "PASS" if cycle.reset_n_assert_line else "WARN"
    elif cycle.reset_type == "GLOBAL":
        checks["gbl_rst_warn"] = "PASS" if cycle.gbl_rst_warn_line else "MISSING"
        checks["pwrgood_deassert"] = "PASS" if cycle.pwrgood_deassert_line else "WARN"
        checks["global_reset_n"] = "PASS" if cycle.global_reset_n_line else "WARN"
        checks["fuse_reload"] = "PASS" if cycle.fuse_reload_line else "WARN"
        checks["fake_go_rsp"] = "PASS" if cycle.fake_go_rsp_line else "WARN"
        checks["icecode_reload"] = "PASS" if cycle.icecode_reload_line else "WARN"
    cycle.flow_checks = checks


def _cold_boot_result(stages):
    """Overall result for normal cold boot."""
    # PASS OVERRIDE: PPR_TEST_DONE (Stage 7 PASS) = definitive run completion.
    # Handles fmod runs where Stage 0/1/5 RTL patterns are absent.
    if 7 in stages and stages[7].status == "PASS":
        return "PASS"
    for s in range(8):
        if s in stages and stages[s].status in ("FAIL", "NOT_REACHED"):
            return "FAIL"
        if s in stages and stages[s].status == "PARTIAL" and s < 6:
            return "FAIL"
    if 7 in stages and stages[7].status == "PASS":
        return "PASS"
    if 6 in stages and stages[6].status == "PARTIAL":
        return "FAIL"
    if 7 in stages and stages[7].status == "PARTIAL":
        return "FAIL"
    return "PASS"


def _reset_result(stages, cycles, ppr_lines):
    """Overall result for reset scenario."""
    for c in cycles:
        if c.status == "FAIL":
            return "FAIL"
    if len(ppr_lines) >= 2:
        return "PASS"
    if all(c.status == "PASS" for c in cycles):
        return "PASS"
    return "FAIL"


# ===========================================================================
#  SUMMARY OUTPUT
# ===========================================================================

def _write_summary(analysis, output_path=None):
    """Write structured summary file from template files in templates/ directory."""
    info = analysis["summary"]
    run_dir = info["run_dir"]

    if output_path is None:
        output_path = default_summary_output_path(run_dir)
    else:
        output_path = ensure_parent_dir(output_path)

    if analysis["reset_cycles"]:
        content = _render_reset_template(analysis)
    else:
        content = _render_cold_boot_template(analysis)

    with open(output_path, "w") as f:
        f.write(content)
    return output_path


def _templates_dir():
    return Path(__file__).resolve().parent.parent / "templates"


def _load_template(template_name):
    return (_templates_dir() / template_name).read_text(errors="replace")


def _stage_display(stages, stage_num):
    r = stages.get(stage_num)
    if not r:
        return "NOT REACHED"
    if r.milestones:
        last = r.milestones[-1]
        if r.missing:
            return f"{r.status} @ line {last.line_number} ({last.substage}) [MISSING: {', '.join(r.missing)}]"
        return f"{r.status} @ line {last.line_number} ({last.substage})"
    if r.missing:
        return f"{r.status} [MISSING: {', '.join(r.missing)}]"
    return r.status


def _cold_boot_failure(stages):
    for s in range(8):
        if s in stages and stages[s].status in ("FAIL", "PARTIAL"):
            r = stages[s]
            sub = r.milestones[-1].substage if r.milestones else "N/A"
            return s, sub
    return "NONE", "N/A"


def _extract_serconsole_text(log_path):
    if not log_path:
        return ""
    out = []
    with _open_log(log_path) as fh:
        for line in fh:
            m = re.search(r"serconsole\.con>\s?(.*)", line)
            if m:
                out.append(m.group(1).strip())
    return "\n".join(out)


def _run_bios_issue_analyzer(log_path):
    """Invoke bios-issue-analyzer decoder scripts for automatic BIOS analysis."""
    if not log_path:
        return "Log path unavailable; BIOS analysis not executed."

    serconsole_text = _extract_serconsole_text(log_path)
    if not serconsole_text.strip():
        return "No serconsole.con output found after BIOS stage; possible BIOS fetch/setup failure."

    scripts_dir = Path(__file__).resolve().parents[2] / "bios-issue-analyzer" / "scripts"
    if not scripts_dir.exists():
        return "bios-issue-analyzer scripts directory not found."

    try:
        sys.path.insert(0, str(scripts_dir))
        from decode_ewl import EWLDecoder  # type: ignore

        decoder = EWLDecoder(
            db_path=str(scripts_dir / "ewl_codes_database.json"),
            rc_db_path=str(scripts_dir / "rc_fatal_errors_database.json"),
        )
        codes = decoder.parse_log(serconsole_text)
        summary = decoder.generate_summary(codes).strip()
        if not codes:
            return (
                "Invoked bios-issue-analyzer decoder automatically.\n"
                "No EWL/IPSD/RC_FATAL signatures were found in serconsole output."
            )
        return "Invoked bios-issue-analyzer decoder automatically.\n" + summary
    except Exception as exc:
        return f"Automatic bios-issue-analyzer invocation failed: {str(exc)[:200]}"


def _cold_root_cause(stage):
    mapping = {
        6: "  BIOS boot did not complete to ExitBootServices/BIOS_TAIL_ACED, indicating a BIOS-side stall in Stage 6.",
        7: "  BIOS completed but OS/test workload did not reach PPR_TEST_DONE, indicating post-BIOS boot failure.",
    }
    return mapping.get(stage, "  Failure occurred before run completion; inspect stage markers and surrounding logs.")


def _cold_log_evidence(stages, failing_stage):
    if not isinstance(failing_stage, int):
        return "  - No failing stage identified."
    r = stages.get(failing_stage)
    if not r:
        return "  - Stage data unavailable."
    lines = []
    if r.milestones:
        last = r.milestones[-1]
        lines.append(f"  - Stage {failing_stage} last milestone at line {last.line_number}: {last.substage}")
        lines.append(f"  - Content: {last.content[:150]}")
    if r.missing:
        lines.append(f"  - Missing required markers: {', '.join(r.missing)}")
    if not lines:
        lines.append("  - No milestones captured for failing stage.")
    return "\n".join(lines)


def _read_dut_cfg_text(run_dir):
    plain = Path(run_dir) / "emurun.dut_cfg"
    gz = Path(run_dir) / "emurun.dut_cfg.gz"
    try:
        if plain.exists():
            return plain.read_text(errors="replace")
        if gz.exists():
            with gzip.open(gz, "rt", errors="replace") as fh:
                return fh.read()
    except Exception:
        return ""
    return ""


def _cfg_value_truthy(value):
    v = str(value).strip().strip('"').lower()
    return v in {"1", "true", "enable", "enabled", "yes", "on"}


def _scan_xtor_runtime_presence(log_path):
    if not log_path:
        return False, ""
    pat = re.compile(r"(cxl.*xtor|pcie.*xtor|xtor.*cxl|xtor.*pcie)", re.I)
    try:
        with _open_log(log_path) as fh:
            for lineno, line in enumerate(fh, 1):
                if pat.search(line):
                    return True, f"line {lineno}: {line.strip()[:140]}"
    except Exception:
        return False, ""
    return False, ""


def _analyze_io_xtor_consistency(run_dir, log_path):
    cfg_text = _read_dut_cfg_text(run_dir)
    if not cfg_text:
        return {"has_issue": False, "evidence": "", "recommendations": ""}

    cxl_requested = False
    pcie_requested = False
    cxl_xtor_enabled = False
    pcie_xtor_enabled = False
    pcie_disabled = False

    for raw in cfg_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()

        if "disable_pcie" in low and re.search(r"disable_pcie\s+1", low):
            pcie_disabled = True
        if "non_pcie" in low:
            pcie_disabled = True

        if "=" not in line:
            continue
        key, value = [x.strip() for x in line.split("=", 1)]
        k = key.lower()
        v = value.lower()

        if "io_bifurcation" in k and "cxl" in v:
            cxl_requested = True
        if "io_bifurcation" in k and "pcie" in v:
            pcie_requested = True

        if "cxl_xtor" in k and _cfg_value_truthy(value):
            cxl_xtor_enabled = True
        if "pcie_xtor" in k and _cfg_value_truthy(value):
            pcie_xtor_enabled = True

    xtor_seen, xtor_line = _scan_xtor_runtime_presence(log_path)

    findings = []
    recs = []

    if cxl_requested and pcie_disabled:
        findings.append("  - Config mismatch: CXL endpoint/bifurcation requested while PCIe is disabled (DISABLE_PCIE/non_pcie).")
        recs.append("  - Align config: if CXL device is present in io_bifurcation, do not disable PCIe fabric in run settings.")

    if cxl_requested and not (cxl_xtor_enabled or pcie_xtor_enabled or xtor_seen):
        findings.append("  - Config mismatch: CXL requested but no CXL/PCIe xtor enable detected in config or runtime logs.")
        recs.append("  - Enable required transactors: CXL/PCIe xtor must be enabled when CXL topology is configured.")

    if pcie_requested and pcie_disabled:
        findings.append("  - Config mismatch: PCIe topology requested while PCIe is explicitly disabled.")
        recs.append("  - Remove DISABLE_PCIE/non_pcie override or update topology to match disabled PCIe mode.")

    if findings and xtor_line:
        findings.append(f"  - Runtime xtor evidence present: {xtor_line}")

    return {
        "has_issue": bool(findings),
        "evidence": "\n".join(findings),
        "recommendations": "\n".join(recs),
    }


def _render_cold_boot_template(analysis):
    stages = analysis["stages"]
    info = analysis["summary"]
    run_dir = info["run_dir"]
    log_path = analysis.get("log_path")
    failing_stage, failing_substage = _cold_boot_failure(stages)

    stage8 = "PASS" if info["result"] == "PASS" else "NOT REACHED"

    last_console_line = "N/A"
    signature = "N/A"
    evidence = "  - N/A"
    recommendations = "  - No action required."
    root_cause = "  N/A"
    bios_analysis = "N/A"
    if isinstance(failing_stage, int):
        r = stages[failing_stage]
        if r.milestones:
            last_console_line = r.milestones[-1].content[:150]
        signature = f"Stage {failing_stage} {r.status}"
        evidence = _cold_log_evidence(stages, failing_stage)
        root_cause = _cold_root_cause(failing_stage)
        recommendations = _cold_boot_recommendations(failing_stage)
        if failing_stage in (6, 7):
            bios_analysis = _run_bios_issue_analyzer(log_path)

        cfg_check = _analyze_io_xtor_consistency(run_dir, log_path)
        if cfg_check["has_issue"]:
            evidence = evidence + "\n" + cfg_check["evidence"]
            recommendations = recommendations + "\n" + cfg_check["recommendations"]
            root_cause = root_cause + " Configuration/run-setting mismatch detected for CXL/PCIe xtor enablement."

    placeholders = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_path": run_dir,
        "test_name": Path(run_dir).name,
        "hsle_model": extract_hsle_model(run_dir),
        "os_image": extract_os_image(run_dir),
        "log_file": log_path or "N/A",
        "log_lines": f"{info['total_lines']:,}",
        "bios_version": extract_bios_id(run_dir),
        "result": info["result"],
        "stage0": _stage_display(stages, 0),
        "stage1": _stage_display(stages, 1),
        "stage2": _stage_display(stages, 2),
        "stage3": _stage_display(stages, 3),
        "stage4": _stage_display(stages, 4),
        "stage5": _stage_display(stages, 5),
        "stage6": _stage_display(stages, 6),
        "stage7": _stage_display(stages, 7),
        "stage8": stage8,
        "failing_stage": str(failing_stage),
        "failing_substage": str(failing_substage),
        "last_console_line": last_console_line,
        "last_post_code": "N/A",
        "last_cycle": "N/A",
        "timeout_cycle": "N/A",
        "silence_gap": "N/A",
        "signature": signature,
        "evidence": evidence,
        "root_cause": root_cause,
        "bios_analysis": bios_analysis,
        "recommendations": recommendations,
        "log_evidence": _cold_log_evidence(stages, failing_stage),
    }

    return _load_template("summary_cold_boot.txt").format(**placeholders)


def _reset_failure(cycles):
    for c in cycles:
        if c.status == "FAIL":
            return c
    return None


def _render_additional_reset_cycles(cycles):
    blocks = []
    for c in cycles[1:]:
        blocks.append(
            "\n".join([
                f"  STAGE PROGRESS — RESET CYCLE {c.cycle_number}",
                "  -----------------------------------------------",
                f"  Stage 8  (Reset Trigger)        : {'PASS' if c.trigger_line else 'FAIL'}   Reset Type: {c.reset_type}",
                f"  Stage 9  (Reset Hardware Entry) : {'PASS' if c.begin_reset_flow_line else 'FAIL'}",
                f"  Stage 10 (Nth Boot RTL)         : {'PASS' if c.bios_first_fetch_line else 'FAIL'}",
                f"  Stage 11 (Nth BIOS Boot)        : {'PASS' if c.bios_aced_line else 'FAIL'}",
                f"  Stage 12 (Nth OS Boot)          : {'PASS' if c.ppr_test_done_line else 'FAIL'}",
                f"  Stage 13 (Test Termination)     : {'PASS' if c.auto_exit_line else 'NOT REACHED'}",
            ])
        )
    return "\n\n".join(blocks) if blocks else ""


def _render_reset_template(analysis):
    stages = analysis["stages"]
    cycles = analysis["reset_cycles"]
    info = analysis["summary"]
    run_dir = info["run_dir"]
    log_path = analysis.get("log_path")
    failing = _reset_failure(cycles)
    first_cycle = cycles[0]

    failing_stage = failing.failing_stage if failing else "NONE"
    failing_substage = failing.failing_substage if (failing and failing.failing_substage) else "N/A"
    signature = _cycle_signature(failing) if failing else "N/A"
    evidence = "\n".join(f"    {x}" for x in _cycle_evidence_lines(failing)) if failing else "    N/A"
    root_cause = _cycle_root_cause(failing) if failing else "  All reset cycles passed."
    recommendations = _reset_recommendations(failing) if failing else "  - No failure-specific recommendations."
    log_evidence = "\n".join(f"  - {x}" for x in _cycle_log_evidence(failing)) if failing else "  - No failing cycle."

    bios_analysis = "N/A"
    if failing and failing.failing_stage in (6, 7, 11):
        bios_analysis = _run_bios_issue_analyzer(log_path)

    cfg_check = _analyze_io_xtor_consistency(run_dir, log_path)
    if cfg_check["has_issue"]:
        evidence = evidence + "\n" + cfg_check["evidence"]
        recommendations = recommendations + "\n" + cfg_check["recommendations"]
        root_cause = root_cause + " Configuration/run-setting mismatch detected for CXL/PCIe xtor enablement."

    placeholders = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reset_type": first_cycle.reset_type,
        "run_path": run_dir,
        "test_name": Path(run_dir).name,
        "hsle_model": extract_hsle_model(run_dir),
        "os_image": extract_os_image(run_dir),
        "log_file": log_path or "N/A",
        "log_lines": f"{info['total_lines']:,}",
        "bios_version": extract_bios_id(run_dir),
        "result": info["result"],
        "reset_trigger": first_cycle.trigger_source,
        "post_setup_script": "N/A",
        "num_resets": str(len(cycles)),
        "reset_sequence": " -> ".join(f"{c.reset_type}({c.trigger_source})" for c in cycles),
        "stage0": _stage_display(stages, 0),
        "stage1": _stage_display(stages, 1),
        "stage2": _stage_display(stages, 2),
        "stage3": _stage_display(stages, 3),
        "stage4": _stage_display(stages, 4),
        "stage5": _stage_display(stages, 5),
        "stage6": _stage_display(stages, 6),
        "stage7": _stage_display(stages, 7),
        "reset_cycle_num": str(first_cycle.cycle_number),
        "stage8": "PASS" if first_cycle.trigger_line else "FAIL",
        "stage9": "PASS" if first_cycle.begin_reset_flow_line else "FAIL",
        "stage10": "PASS" if first_cycle.bios_first_fetch_line else "FAIL",
        "stage11": "PASS" if first_cycle.bios_aced_line else "FAIL",
        "stage12": "PASS" if first_cycle.ppr_test_done_line else "FAIL",
        "stage13": "PASS" if first_cycle.auto_exit_line else "NOT REACHED",
        "additional_reset_cycles": _render_additional_reset_cycles(cycles),
        "failing_stage": str(failing_stage),
        "failing_substage": str(failing_substage),
        "reset_origin": info["reset_origin"],
        "first_boot_stage": _stage_display(stages, 6),
        "bios_substage": str(failing_substage),
        "cf9_value": first_cycle.cf9_value or "N/A",
        "gbl_etr3": "N/A",
        "reset_class": first_cycle.reset_type,
        "vp_quiesce": "PASS" if first_cycle.hsle_start_reset_line else "FAIL",
        "cbb_event": "PASS" if first_cycle.cbb_event_line else "FAIL",
        "pltrst_or_gblrst": "PASS" if (first_cycle.pltrst_sync_line or first_cycle.gbl_rst_warn_line) else "FAIL",
        "power_signals": "PASS" if first_cycle.pwrgood_deassert_line else "N/A",
        "fuse_reload": "YES" if first_cycle.fuse_reload_line else "NO",
        "icecode_reload": "YES" if first_cycle.icecode_reload_line else "NO",
        "fake_go_rsp": "YES" if first_cycle.fake_go_rsp_line else "NO",
        "begin_reset_flow": "PASS" if first_cycle.begin_reset_flow_line else "FAIL",
        "expected_ppr_count": str(len(cycles) + 1),
        "actual_ppr_count": str(info["ppr_total_count"]),
        "last_console_line": _last_console_output(failing) if failing else "N/A",
        "last_post_code": "N/A",
        "last_cycle": str(first_cycle.trigger_line),
        "timeout_cycle": "N/A",
        "silence_gap": "N/A",
        "signature": signature,
        "evidence": evidence,
        "root_cause": root_cause,
        "bios_analysis": bios_analysis,
        "recommendations": recommendations,
        "log_evidence": log_evidence,
    }

    return _load_template("summary_reset_scenario.txt").format(**placeholders)


def _append_reset_template_sections(lines, cycles):
    sep = "=" * 80
    failing = next((cycle for cycle in cycles if cycle.status == "FAIL"), None)

    lines.append(sep)
    lines.append("  FAILURE ANALYSIS")
    lines.append(sep)
    lines.append("")
    if failing is None:
        lines.append("  No reset-cycle failure detected.")
        lines.append("")
    else:
        last_marker_label, last_marker_line = _last_reached_reset_marker(failing)
        lines.append("  Last Activity")
        lines.append("  -------------")
        lines.append(f"  Last console output  : {_last_console_output(failing)}")
        lines.append("  Last debug_port PC   : N/A")
        lines.append(f"  Emu cycle at last    : {last_marker_label} @ line {last_marker_line}")
        lines.append("  Timeout at           : N/A")
        lines.append("  Silence gap          : N/A")
        lines.append("")
        lines.append("  Failure Signature")
        lines.append("  -----------------")
        lines.append(f"  Signature    : {_cycle_signature(failing)}")
        lines.append("  Evidence     :")
        for evidence_line in _cycle_evidence_lines(failing):
            lines.append(f"    {evidence_line}")
        lines.append("")
        lines.append("  Root Cause")
        lines.append("  ----------")
        for root_line in _cycle_root_cause(failing).splitlines():
            lines.append(root_line)
        lines.append("")
        lines.append("  BIOS Issue Analysis (Stage 6/7/11 failures only)")
        lines.append("  -------------------------------------------------")
        if failing.failing_stage == 11:
            lines.append("  BIOS boot reached second-boot fetch but did not complete. BIOS decoder is auto-invoked for this scenario; review decoded output in this section.")
        else:
            lines.append("  N/A")
        lines.append("")

    lines.append(sep)
    lines.append("  RECOMMENDATIONS")
    lines.append(sep)
    lines.append("")
    if failing is None:
        lines.append("  - No failure-specific recommendations. All detected reset cycles passed.")
    else:
        for rec_line in _reset_recommendations(failing).splitlines():
            lines.append(rec_line)
    lines.append("")

    lines.append(sep)
    lines.append("  KEY LOG EVIDENCE")
    lines.append(sep)
    lines.append("")
    if failing is None:
        for cycle in cycles:
            lines.append(f"  - Cycle {cycle.cycle_number} trigger: line {cycle.trigger_line} | {cycle.trigger_content[:120]}")
    else:
        for evidence_line in _cycle_log_evidence(failing):
            lines.append(f"  - {evidence_line}")
    lines.append("")


def _last_console_output(cycle):
    if cycle.failure_context:
        return cycle.failure_context[-1]
    if cycle.trigger_content:
        return cycle.trigger_content[:150]
    return cycle.failure_detail or "N/A"


def _last_reached_reset_marker(cycle):
    markers = [
        ("AUTO_EXIT", cycle.auto_exit_line),
        ("PPR_TEST_DONE", cycle.ppr_test_done_line),
        ("BIOS_ACED", cycle.bios_aced_line),
        ("BIOS_FIRST_FETCH", cycle.bios_first_fetch_line),
        ("RESET_PHASE_6", cycle.reset_phase_6_line),
        ("PRIMECODE_START", cycle.primecode_start_line),
        ("BEGIN_RESET_FLOW", cycle.begin_reset_flow_line),
        ("RESET_TRIGGERED", cycle.reset_triggered_line),
        ("HSLE_START_RESET", cycle.hsle_start_reset_line),
        ("RESET_TRIGGER", cycle.trigger_line),
    ]
    for label, line in markers:
        if line:
            return label, line
    return "N/A", 0


def _cycle_signature(cycle):
    stage_text = f"Stage {cycle.failing_stage}"
    if cycle.failing_substage:
        stage_text += f".{cycle.failing_substage}"
    if cycle.failure_detail:
        return f"{stage_text}: {cycle.failure_detail}"
    return stage_text


def _cycle_root_cause(cycle):
    root_causes = {
        (10, "10.0"): "  Reset hardware entry reached BEGIN_RESET_FLOW, but second-boot primecode never started. This points to a stall in early reset-phase bring-up before BIOS first fetch.",
        (10, "10.3"): "  Primecode started but RESET_PHASE_6 never completed, which indicates the second-boot RTL reset sequence stalled before handoff to BIOS.",
        (10, "10.4"): "  RESET_PHASE_6 completed, but BIOS first fetch never occurred. The handoff from reset flow into BIOS execution did not complete.",
        (11, "11.2"): "  BIOS first fetch wait was reached, but the second BIOS boot never completed to ACED. This indicates a BIOS-side failure or hang during the second boot.",
        (12, ""): "  BIOS completed on the second boot, but the workload never reached the next PPR_TEST_DONE marker. The failure is after BIOS completion in OS or workload execution.",
    }
    return root_causes.get((cycle.failing_stage, cycle.failing_substage), f"  {cycle.failure_detail or 'Reset cycle failed before completion.'}")


def _cycle_evidence_lines(cycle):
    evidence = [f"Trigger line {cycle.trigger_line}: {cycle.trigger_content[:120]}"]
    label, line = _last_reached_reset_marker(cycle)
    if line:
        evidence.append(f"Last reached marker: {label} at line {line}")
    if cycle.failure_detail:
        evidence.append(f"Failure detail: {cycle.failure_detail}")
    for key, value in cycle.flow_checks.items():
        if value != "PASS":
            evidence.append(f"Flow check {key}: {value}")
    if cycle.failure_context:
        evidence.extend(cycle.failure_context[-3:])
    return evidence


def _cycle_log_evidence(cycle):
    evidence = [
        f"Cycle {cycle.cycle_number} trigger: line {cycle.trigger_line} | {cycle.trigger_content[:120]}",
        f"HSLE start reset: {'line ' + str(cycle.hsle_start_reset_line) if cycle.hsle_start_reset_line else 'NOT REACHED'}",
        f"BEGIN_RESET_FLOW: {'line ' + str(cycle.begin_reset_flow_line) if cycle.begin_reset_flow_line else 'NOT REACHED'}",
        f"Primecode start: {'line ' + str(cycle.primecode_start_line) if cycle.primecode_start_line else 'NOT REACHED'}",
        f"BIOS first fetch: {'line ' + str(cycle.bios_first_fetch_line) if cycle.bios_first_fetch_line else 'NOT REACHED'}",
        f"BIOS ACED: {'line ' + str(cycle.bios_aced_line) if cycle.bios_aced_line else 'NOT REACHED'}",
        f"PPR_TEST_DONE: {'line ' + str(cycle.ppr_test_done_line) if cycle.ppr_test_done_line else 'NOT REACHED'}",
    ]
    for key, value in cycle.flow_checks.items():
        evidence.append(f"Flow validation {key}: {'OK' if value == 'PASS' else value}")
    return evidence


def _cold_boot_recommendations(stage):
    recs = {
        0: "  - Check spark_session.log for launch errors\n  - Verify ZeBu hardware allocation",
        1: "  - Check emu_log for ZSE5 init errors\n  - Verify ZeBu board connectivity",
        2: "  - Check IDI link errors\n  - Verify model version compatibility",
        3: "  - Check ZeBu compilation log\n  - Verify RTL model path accessible",
        4: "  - Check Simics launch/license errors\n  - Verify VP creation logs",
        5: "  - See reset_phase_flow.txt for sub-phase analysis\n  - Check RESET_PHASE_1-6 progression\n  - Verify both imh8/imh9 symmetry",
        6: "  - Check for BIOS-initiated reset (CF9 write during BIOS)\n  - See bios_flow.txt for sub-phases 6.0-6.6\n  - Review auto-generated BIOS Issue Analysis section above",
        7: "  - Check serconsole for OS boot errors\n  - Verify PPR test workload config",
    }
    return recs.get(stage, "  - Inspect testbench.log around last milestone")


def _reset_recommendations(cycle):
    stage = cycle.failing_stage
    rtype = cycle.reset_type
    recs = {
        8: "  - Verify post-setup script loaded\n  - Check os_reset_triggers.simics dispatch table",
        9: f"  - Check serconsole for errors before hardware entry\n  - Verify {'PLTRST_SYNC' if rtype != 'GLOBAL' else 'GBL_RST_WARN'} completion\n  - Check if VP quiesce succeeded",
        10: "  - See reset_phase_flow.txt for HWRS sub-phase detail\n  - Check BOOT_FSM stuck state (which 0x?? value)\n  - Verify both imh8/imh9 primecode symmetry",
        11: "  - Check if IDI Mux was enabled after RESET_PHASE_6\n  - See bios_flow.txt for BIOS sub-phases\n  - Review auto-generated BIOS Issue Analysis section above\n  - Check for BIOS hang vs VP/RTL handoff issue",
        12: "  - Check serconsole for second OS boot errors\n  - Verify PPR workload starts on second boot\n  - Check for kernel panic or OOM",
    }
    return recs.get(stage, "  - Inspect testbench.log at failure point")


# ===========================================================================
#  CLI ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="HSLE Run Analyzer - Single-pass debug")
    parser.add_argument("run_dir", help="Path to HSLE run directory")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--summary", action="store_true", help="Generate summary file")
    parser.add_argument("--output", "-o", help="Override output file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print(f"ERROR: {run_dir} not found", file=sys.stderr)
        sys.exit(1)
    
    start = time.time()
    
    analysis = analyze_run(run_dir, generate_summary=args.summary or not args.json,
                          output_path=args.output)
    
    elapsed = time.time() - start
    
    if args.json:
        # Machine-readable output for agent
        out = {
            "run_dir": run_dir,
            "elapsed_seconds": round(elapsed, 1),
            **analysis["summary"],
            "stages": {},
            "cycles": [],
        }
        for s in sorted(analysis["stages"]):
            r = analysis["stages"][s]
            out["stages"][str(s)] = {
                "status": r.status,
                "last_line": r.milestones[-1].line_number if r.milestones else 0,
                "missing": r.missing,
            }
        for c in analysis["reset_cycles"]:
            out["cycles"].append({
                "number": c.cycle_number,
                "type": c.reset_type,
                "source": c.trigger_source,
                "status": c.status,
                "trigger_line": c.trigger_line,
                "failing_stage": c.failing_stage if c.status == "FAIL" else None,
                "failure_detail": c.failure_detail if c.status == "FAIL" else None,
                "flow_checks": c.flow_checks,
            })
        # Remove non-serializable items
        out.pop("ppr_lines", None)
        print(json.dumps(out, indent=2))
    else:
        info = analysis["summary"]
        print(f"HSLE Run Analyzer")
        print(f"{'=' * 60}")
        print(f"Run: {run_dir}")
        print(f"Log: {info['total_lines']:,} lines | Elapsed: {elapsed:.1f}s")
        print(f"Result: {info['result']} | Scenario: {info['scenario']}")
        if analysis["reset_cycles"]:
            types = " -> ".join(f"{c.reset_type}({c.trigger_source})" for c in analysis["reset_cycles"])
            print(f"Resets: {len(analysis['reset_cycles'])} cycle(s): {types}")
            print(f"Origin: {info['reset_origin']}")
            for c in analysis["reset_cycles"]:
                status_mark = "PASS" if c.status == "PASS" else f"FAIL@Stage{c.failing_stage}"
                print(f"  Cycle {c.cycle_number}: {c.reset_type}({c.trigger_source}) -> {status_mark}")
                if c.status == "FAIL":
                    print(f"    {c.failure_detail}")
        print(f"PPR count: {info['ppr_total_count']} | results.log: {info['results_log'] or 'N/A'}")
        if "summary_file" in info:
            print(f"\nSummary: {info['summary_file']}")
