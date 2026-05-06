#!/usr/bin/env python3
"""
HSLE Reset Cycle Detector - Optimized single-pass detection with flow validation.

Detects cold/warm/global resets, classifies trigger source (OS reboot, BIOS,
SOLAR, AGR, AWR, SWR), validates reset-type-specific flow milestones,
supports back-to-back cycles, and extracts failure context.

Encodes knowledge from cold_reset_flow.txt, warm_reset_flow.txt, and
global_reset_flow.txt for automated validation without manual grep.

Performance: ~45s for 1M-line log on NFS (single-pass, I/O bound).
"""

import re
import gzip
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple


@dataclass
class ResetCycle:
    """One detected reset cycle with all stage markers."""
    cycle_number: int
    reset_type: str = "UNKNOWN"      # COLD, WARM, GLOBAL
    trigger_source: str = "UNKNOWN"  # OS_REBOOT, BIOS_INITIATED, SOLAR, AGR, AWR, SWR, CF9_WRITE
    cf9_value: str = ""

    # Stage 8: Reset trigger
    trigger_line: int = 0
    trigger_content: str = ""

    # Stage 9: Hardware entry
    hsle_start_reset_line: int = 0
    cbb_event_line: int = 0
    cbb_event_type: str = ""
    pltrst_sync_line: int = 0         # Cold/warm only
    gbl_rst_warn_line: int = 0        # Global only
    global_reset_n_line: int = 0      # Global only
    pwrgood_deassert_line: int = 0    # Cold/global only (NOT warm)
    slp_assertion_line: int = 0       # Cold/global only (NOT warm)
    fuse_reload_line: int = 0         # Cold/global only (NOT warm)
    reset_n_assert_line: int = 0      # Warm: RESET_N only
    fake_go_rsp_line: int = 0         # Global only
    begin_reset_flow_line: int = 0
    reset_triggered_line: int = 0

    # Stage 10: Second boot RTL
    primecode_start_line: int = 0
    primecode_end_line: int = 0       # 0x41 or 0x3c
    reset_phase_3_line: int = 0
    reset_phase_6_line: int = 0
    bios_first_fetch_line: int = 0
    icecode_reload_line: int = 0      # Global only

    # Stage 11: Second BIOS
    idi_mux_line: int = 0
    bios_aced_line: int = 0
    reset_phase_7_line: int = 0

    # Stage 12: Second OS/PPR
    ppr_test_done_line: int = 0

    # Stage 13: Termination
    auto_exit_line: int = 0

    # Evaluation
    status: str = "UNKNOWN"  # PASS, FAIL
    failing_stage: int = 0
    failing_substage: str = ""
    failure_detail: str = ""

    # Context around failure point
    failure_context: List[str] = field(default_factory=list)

    # Flow validation results (type-specific checks)
    flow_checks: Dict[str, str] = field(default_factory=dict)


# All reset-related patterns compiled once
PATTERNS = {
    # Stage 8: Trigger detection
    "ppr_test_done":        re.compile(r"PPR_TEST_DONE"),
    "ppr_got_cf9":          re.compile(r"PPR check: GOT RESET CF9\s+(\d+)"),
    "rst_tag_triggering":   re.compile(r"RST_TAG:\s*triggering\s+(\S+)"),
    "cold_through":         re.compile(r"COLD_RESET\s+through\s+(.+?)(?:\s+triggered|$)"),
    "warm_through":         re.compile(r"WARM_RESET\s+through\s+(.+?)(?:\s+triggered|$)"),
    "global_through":       re.compile(r"GLOBAL_RESET\s+through\s+(.+?)(?:\s+triggered|$)"),
    "agr_event":            re.compile(r"RST_TAG\s+AGR\s+event\s+triggered"),
    "awr_event":            re.compile(r"RST_TAG\s+AWR\s+event\s+triggered"),
    "swr_event":            re.compile(r"RST_TAG\s+Reset_BTN\s+event\s+triggered"),

    # Stage 9: Hardware entry
    "hsle_start_reset":     re.compile(r"RST_TAG\s+HSLE\s+starting\s+reset"),
    "cbb_event":            re.compile(r"RST_TAG\s+Creating\s+a\s+CBB\s+event\s+for\s+reset:\s*(\w+)"),
    "inform_rst":           re.compile(r"Inform\s+RST_TAG:\s+Running\s+(\w+)\s+reset"),
    "pltrst_sync":          re.compile(r"RST_TAG\s+waiting\s+for\s+PLTRST_SYNC"),
    "gbl_rst_warn":         re.compile(r"GBL_RST_WARN|RST_TAG.*[Gg]lobal.*[Rr]eset.*[Ww]arn"),
    "global_reset_n":       re.compile(r"GLOBAL_RESET_N.*(?:forced|assert)|RST_TAG.*GLOBAL_RESET_N"),
    "pwrgood_deassert":     re.compile(r"RST_TAG\s+Forced.*PWRGOOD.*to\s+0|PWRGOOD.*de-?assert"),
    "slp_assertion":        re.compile(r"SLP_S[345].*assert|RST_TAG.*SLP_S[345]"),
    "fuse_reload":          re.compile(r"fuse.*reload|FUSE.*RELOAD|reload_fuse"),
    "reset_n_assert":       re.compile(r"RST_TAG.*XX_RESET_N.*assert|RESET_N.*(?:forced|assert)"),
    "fake_go_rsp":          re.compile(r"Fake\s+GO\s+RSP|fake.*go.*rsp", re.I),
    "begin_reset_flow":     re.compile(r"BEGIN_RESET_FLOW"),
    "reset_triggered":      re.compile(r"RST_TAG\s+Reset\s+triggered"),

    # Stage 10: Second boot RTL
    "boot_fsm_01":          re.compile(r"BOOT_FSM\s+state\s+0x0?1\b"),
    "boot_fsm_end":         re.compile(r"BOOT_FSM\s+state\s+0x(?:41|3c)\b"),
    "reset_phase_3":        re.compile(r"RESET_PHASE_3"),
    "reset_phase_6":        re.compile(r"RESET_PHASE_6"),
    "bios_first_fetch":     re.compile(r"RST_TAG\s+waiting\s+for\s+BIOS\s+first\s+fetch"),
    "icecode_reload":       re.compile(r"icecode_load|IceCode.*reload|ICECODE.*RELOAD", re.I),
    "hwrs_complete":        re.compile(r"HWRS_RESET_COMPLETE|HWRS.*reset.*complete", re.I),

    # Stage 11: Second BIOS
    "idi_mux":              re.compile(r"IDI.*[Mm]ux.*enabl"),
    "bios_aced":            re.compile(r"BIOS_TAIL_ACED"),
    "reset_phase_7":        re.compile(r"RESET_PHASE_7"),

    # Stage 13: Termination
    "auto_exit":            re.compile(r"Auto exit triggered|RESET_TEST_COMPLETE"),
    "rca_check":            re.compile(r"rca_check_found:\s*(\d+)"),
}

# Skip noise lines
NOISE_RE = re.compile(r"wait-for-|Watching for|Expected.*pattern|hap_callback")


def open_log(run_dir):
    """Open testbench.log."""
    plain = os.path.join(run_dir, "testbench.log")
    gz = os.path.join(run_dir, "testbench.log.gz")
    if os.path.exists(plain):
        return open(plain, "r", errors="replace"), False
    elif os.path.exists(gz):
        return gzip.open(gz, "rt", errors="replace"), True
    raise FileNotFoundError(f"No testbench.log in {run_dir}")


def detect_resets(run_dir):
    """
    Single-pass reset detection with type-specific flow validation.

    Returns:
        (list[ResetCycle], dict summary_info)
    """
    fh, _ = open_log(run_dir)

    events = []
    ppr_lines = []
    rca_found = 0
    line_num = 0
    context_buf = []

    with fh:
        for line in fh:
            line_num += 1
            if NOISE_RE.search(line):
                continue
            stripped = line.strip()
            if not stripped or len(stripped) < 5:
                continue

            # Context buffer
            if len(stripped) < 300 and not stripped.startswith("//"):
                context_buf.append((line_num, stripped[:200]))
                if len(context_buf) > 15:
                    context_buf.pop(0)

            # PPR_TEST_DONE
            if PATTERNS["ppr_test_done"].search(line):
                ppr_lines.append((line_num, stripped[:200]))

            # RCA
            m = PATTERNS["rca_check"].search(line)
            if m:
                rca_found = int(m.group(1))

            # Triggers first
            matched = False
            for pname in ("ppr_got_cf9", "rst_tag_triggering", "cold_through",
                          "warm_through", "global_through", "agr_event",
                          "awr_event", "swr_event"):
                m = PATTERNS[pname].search(line)
                if m:
                    events.append((line_num, pname, m.groups(), stripped[:200],
                                   list(context_buf[-5:])))
                    matched = True
                    break

            if not matched:
                for pname in ("hsle_start_reset", "cbb_event", "inform_rst",
                              "pltrst_sync", "gbl_rst_warn", "global_reset_n",
                              "pwrgood_deassert", "slp_assertion", "fuse_reload",
                              "reset_n_assert", "fake_go_rsp",
                              "begin_reset_flow", "reset_triggered",
                              "boot_fsm_01", "boot_fsm_end",
                              "reset_phase_3", "reset_phase_6",
                              "bios_first_fetch", "icecode_reload", "hwrs_complete",
                              "idi_mux", "bios_aced", "reset_phase_7",
                              "auto_exit"):
                    m = PATTERNS[pname].search(line)
                    if m:
                        events.append((line_num, pname, m.groups(),
                                       stripped[:200], list(context_buf[-5:])))
                        break

    total_lines = line_num
    cycles = _assemble_cycles(events, ppr_lines)

    first_boot_ppr = None
    if ppr_lines and cycles:
        if ppr_lines[0][0] < cycles[0].trigger_line:
            first_boot_ppr = ppr_lines[0]
    elif ppr_lines and not cycles:
        first_boot_ppr = ppr_lines[0]

    summary = {
        "total_lines": total_lines,
        "ppr_total_count": len(ppr_lines),
        "ppr_lines": ppr_lines,
        "reset_cycle_count": len(cycles),
        "first_boot_ppr": first_boot_ppr,
        "rca_check_found": rca_found,
    }
    return cycles, summary


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
            if current and current.hsle_start_reset_line > 0:
                cycles.append(current)
                current = None
            if current is None:
                cycle_num += 1
                current = ResetCycle(cycle_number=cycle_num)
                current.trigger_line = evt_line
                current.trigger_content = content
            _classify(current, evt_type, groups, content)
        elif current:
            _fill_marker(current, evt_type, groups, evt_line, ctx)

    if current:
        cycles.append(current)

    _assign_ppr(cycles, ppr_lines)
    for c in cycles:
        _evaluate(c)
        _validate_flow(c)
    return cycles


def _fill_marker(current, evt_type, groups, evt_line, ctx):
    """Fill stage marker into current cycle."""
    if evt_type == "hsle_start_reset" and not current.hsle_start_reset_line:
        current.hsle_start_reset_line = evt_line
    elif evt_type == "cbb_event" and not current.cbb_event_line:
        current.cbb_event_line = evt_line
        if groups:
            current.cbb_event_type = groups[0].upper()
            if current.reset_type == "UNKNOWN":
                current.reset_type = groups[0].upper()
    elif evt_type == "pltrst_sync" and not current.pltrst_sync_line:
        if current.hsle_start_reset_line > 0:
            current.pltrst_sync_line = evt_line
    elif evt_type == "gbl_rst_warn" and not current.gbl_rst_warn_line:
        if current.hsle_start_reset_line > 0:
            current.gbl_rst_warn_line = evt_line
    elif evt_type == "global_reset_n" and not current.global_reset_n_line:
        if current.hsle_start_reset_line > 0:
            current.global_reset_n_line = evt_line
    elif evt_type == "pwrgood_deassert" and not current.pwrgood_deassert_line:
        if current.hsle_start_reset_line > 0:
            current.pwrgood_deassert_line = evt_line
    elif evt_type == "slp_assertion" and not current.slp_assertion_line:
        if current.hsle_start_reset_line > 0:
            current.slp_assertion_line = evt_line
    elif evt_type == "fuse_reload" and not current.fuse_reload_line:
        if current.hsle_start_reset_line > 0:
            current.fuse_reload_line = evt_line
    elif evt_type == "reset_n_assert" and not current.reset_n_assert_line:
        if current.hsle_start_reset_line > 0:
            current.reset_n_assert_line = evt_line
    elif evt_type == "fake_go_rsp" and not current.fake_go_rsp_line:
        if current.hsle_start_reset_line > 0:
            current.fake_go_rsp_line = evt_line
    elif evt_type == "begin_reset_flow" and not current.begin_reset_flow_line:
        current.begin_reset_flow_line = evt_line
    elif evt_type == "reset_triggered" and not current.reset_triggered_line:
        current.reset_triggered_line = evt_line
    elif evt_type == "boot_fsm_01" and not current.primecode_start_line:
        if current.begin_reset_flow_line > 0:
            current.primecode_start_line = evt_line
    elif evt_type == "boot_fsm_end" and not current.primecode_end_line:
        if current.begin_reset_flow_line > 0:
            current.primecode_end_line = evt_line
    elif evt_type == "reset_phase_3" and not current.reset_phase_3_line:
        if current.begin_reset_flow_line > 0:
            current.reset_phase_3_line = evt_line
    elif evt_type == "reset_phase_6" and not current.reset_phase_6_line:
        if current.begin_reset_flow_line > 0:
            current.reset_phase_6_line = evt_line
    elif evt_type == "bios_first_fetch" and not current.bios_first_fetch_line:
        current.bios_first_fetch_line = evt_line
    elif evt_type == "icecode_reload" and not current.icecode_reload_line:
        if current.begin_reset_flow_line > 0:
            current.icecode_reload_line = evt_line
    elif evt_type == "idi_mux" and not current.idi_mux_line:
        if current.begin_reset_flow_line > 0:
            current.idi_mux_line = evt_line
    elif evt_type == "bios_aced" and not current.bios_aced_line:
        if current.begin_reset_flow_line > 0:
            current.bios_aced_line = evt_line
    elif evt_type == "reset_phase_7" and not current.reset_phase_7_line:
        if current.begin_reset_flow_line > 0:
            current.reset_phase_7_line = evt_line
    elif evt_type == "auto_exit" and not current.auto_exit_line:
        current.auto_exit_line = evt_line
        current.failure_context = [c[1] for c in ctx]


def _classify(cycle, evt_type, groups, content):
    """Classify reset type and source."""
    if evt_type == "ppr_got_cf9":
        val = int(groups[0]) if groups else 0
        cycle.cf9_value = hex(val)
        if val in (14, 0xE):
            cycle.reset_type = "COLD"
        elif val in (6, 0x6):
            if cycle.reset_type == "UNKNOWN":
                cycle.reset_type = "WARM"
        cycle.trigger_source = "CF9_WRITE"
    elif evt_type == "cold_through":
        cycle.reset_type = "COLD"
        src = groups[0] if groups else ""
        cycle.trigger_source = "OS_REBOOT" if "OS" in src.upper() or "reboot" in src.lower() else "SOLAR"
    elif evt_type == "warm_through":
        cycle.reset_type = "WARM"
        src = groups[0] if groups else ""
        cycle.trigger_source = "OS_REBOOT" if "OS" in src.upper() or "reboot" in src.lower() else "SOLAR"
    elif evt_type == "global_through":
        cycle.reset_type = "GLOBAL"
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
            cycle.reset_type = "COLD"
            cycle.trigger_source = "OS_REBOOT"
        elif "warm" in name:
            cycle.reset_type = "WARM"
            cycle.trigger_source = "OS_REBOOT"
        elif "global" in name:
            cycle.reset_type = "GLOBAL"
            cycle.trigger_source = "SOLAR"
        elif "agr" in name:
            cycle.reset_type = "GLOBAL"
            cycle.trigger_source = "AGR"
        elif "awr" in name:
            cycle.reset_type = "WARM"
            cycle.trigger_source = "AWR"
        elif "swr" in name:
            cycle.reset_type = "WARM"
            cycle.trigger_source = "SWR"


def _assign_ppr(cycles, ppr_lines):
    """Assign PPR_TEST_DONE to cycles by position."""
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


def _evaluate(cycle):
    """Determine pass/fail for a cycle."""
    if cycle.ppr_test_done_line > 0:
        cycle.status = "PASS"
    elif cycle.auto_exit_line > 0 and cycle.bios_aced_line > 0:
        cycle.status = "PASS"
    elif cycle.hsle_start_reset_line == 0:
        cycle.status = "FAIL"
        cycle.failing_stage = 8
        cycle.failure_detail = "Reset trigger detected but HSLE reset procedures never started"
    elif cycle.begin_reset_flow_line == 0:
        cycle.status = "FAIL"
        cycle.failing_stage = 9
        cycle.failure_detail = "HSLE reset started but BEGIN_RESET_FLOW never reached"
    elif cycle.primecode_start_line == 0:
        cycle.status = "FAIL"
        cycle.failing_stage = 10
        cycle.failing_substage = "10.0"
        cycle.failure_detail = "BEGIN_RESET_FLOW reached but primecode never started"
    elif cycle.bios_first_fetch_line == 0:
        cycle.status = "FAIL"
        cycle.failing_stage = 10
        if cycle.reset_phase_6_line == 0:
            cycle.failing_substage = "10.3"
            cycle.failure_detail = "Primecode started but RESET_PHASE_6 never completed"
        else:
            cycle.failing_substage = "10.4"
            cycle.failure_detail = "RESET_PHASE_6 done but BIOS first fetch never reached"
    elif cycle.idi_mux_line == 0 and cycle.bios_aced_line == 0:
        cycle.status = "FAIL"
        cycle.failing_stage = 11
        cycle.failing_substage = "11.0"
        cycle.failure_detail = "BIOS first fetch reached but VP never started (IDI Mux not enabled)"
    elif cycle.bios_aced_line == 0:
        cycle.status = "FAIL"
        cycle.failing_stage = 11
        cycle.failing_substage = "11.2"
        cycle.failure_detail = "VP started but BIOS did not complete (BIOS_TAIL_ACED missing)"
    else:
        cycle.status = "FAIL"
        cycle.failing_stage = 12
        cycle.failure_detail = "BIOS completed but PPR_TEST_DONE missing"


def _validate_flow(cycle):
    """
    Type-specific flow validation.
    Encodes cold_reset_flow.txt / warm_reset_flow.txt / global_reset_flow.txt.
    """
    checks = {}
    if cycle.reset_type == "COLD":
        checks["pltrst_sync"] = "PASS" if cycle.pltrst_sync_line else "MISSING"
        checks["pwrgood_deassert"] = "PASS" if cycle.pwrgood_deassert_line else "MISSING"
        checks["fuse_reload"] = "PASS" if cycle.fuse_reload_line else "WARN"
        if cycle.gbl_rst_warn_line:
            checks["gbl_rst_warn"] = "UNEXPECTED"
        if cycle.fake_go_rsp_line:
            checks["fake_go_rsp"] = "UNEXPECTED"
    elif cycle.reset_type == "WARM":
        checks["pltrst_sync"] = "PASS" if cycle.pltrst_sync_line else "MISSING"
        checks["no_pwrgood"] = "PASS" if not cycle.pwrgood_deassert_line else "UNEXPECTED"
        checks["no_fuse_reload"] = "PASS" if not cycle.fuse_reload_line else "UNEXPECTED"
        checks["no_slp"] = "PASS" if not cycle.slp_assertion_line else "UNEXPECTED"
    elif cycle.reset_type == "GLOBAL":
        checks["gbl_rst_warn"] = "PASS" if cycle.gbl_rst_warn_line else "MISSING"
        checks["pwrgood_deassert"] = "PASS" if cycle.pwrgood_deassert_line else "MISSING"
        checks["fuse_reload"] = "PASS" if cycle.fuse_reload_line else "WARN"
        checks["fake_go_rsp"] = "PASS" if cycle.fake_go_rsp_line else "WARN"
        checks["icecode_reload"] = "PASS" if cycle.icecode_reload_line else "WARN"
    cycle.flow_checks = checks


def classify_reset_origin(cycles, first_boot_ppr):
    """Determine if first reset was BIOS-initiated or post-OS."""
    if not cycles:
        return "NONE"
    if first_boot_ppr and first_boot_ppr[0] < cycles[0].trigger_line:
        return "POST_OS_BOOT"
    return "BIOS_INITIATED"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python reset_detector.py <run_dir>")
        sys.exit(1)
    cycles, summary = detect_resets(sys.argv[1])
    print(f"Total lines: {summary['total_lines']}")
    print(f"PPR count: {summary['ppr_total_count']}")
    print(f"Reset cycles: {len(cycles)}")
    origin = classify_reset_origin(cycles, summary.get('first_boot_ppr'))
    print(f"Origin: {origin}")
    for c in cycles:
        print(f"\n  Cycle {c.cycle_number}: {c.reset_type} ({c.trigger_source}) -> {c.status}")
        print(f"    Trigger: line {c.trigger_line} | {c.trigger_content[:80]}")
        if c.flow_checks:
            print(f"    Flow validation ({c.reset_type}):")
            for k, v in c.flow_checks.items():
                flag = "  " if v == "PASS" else " *"
                print(f"     {flag} {k}: {v}")
        if c.status == "FAIL":
            print(f"    FAIL Stage {c.failing_stage}: {c.failure_detail}")
