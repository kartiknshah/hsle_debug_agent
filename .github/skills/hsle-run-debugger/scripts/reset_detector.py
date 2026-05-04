#!/usr/bin/env python3
"""
HSLE Reset Cycle Detector

Detects and classifies reset cycles in testbench.log.
Identifies cold, warm, and global resets (OS-initiated, BIOS-initiated,
platform-injected AGR/AWR/SWR). Supports back-to-back reset detection.

Usage:
    from reset_detector import detect_resets
    cycles, summary = detect_resets("/path/to/run_dir")

    # Or standalone:
    python reset_detector.py /path/to/run_dir
"""

import re
import gzip
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict


@dataclass
class ResetCycle:
    """Represents one detected reset cycle"""
    cycle_number: int
    reset_type: str  # COLD, WARM, GLOBAL
    trigger_source: str  # OS_REBOOT, BIOS_INITIATED, SOLAR, AGR, AWR, SWR

    # Stage 8 markers
    trigger_line: int = 0
    trigger_content: str = ""
    cf9_value: str = ""

    # Stage 9 markers
    hsle_start_reset_line: int = 0
    cbb_event_line: int = 0
    begin_reset_flow_line: int = 0
    reset_triggered_line: int = 0

    # Stage 10 markers
    primecode_start_line: int = 0
    reset_phase_6_line: int = 0
    bios_first_fetch_line: int = 0

    # Stage 11 markers
    idi_mux_line: int = 0
    bios_aced_line: int = 0

    # Stage 12 markers
    ppr_test_done_line: int = 0

    # Stage 13 markers
    auto_exit_line: int = 0

    # Status
    status: str = "UNKNOWN"  # PASS, FAIL
    failing_stage: int = 0
    failure_detail: str = ""


# Patterns for reset detection
RESET_PATTERNS = {
    "rst_tag_triggering": re.compile(
        r"RST_TAG:\s*triggering\s+(\S+)", re.IGNORECASE),
    "cold_reset_through": re.compile(
        r"(COLD_RESET)\s+through\s+(.*?)\s*(triggered|$)", re.IGNORECASE),
    "warm_reset_through": re.compile(
        r"(WARM_RESET)\s+through\s+(.*?)\s*(triggered|$)", re.IGNORECASE),
    "global_reset_through": re.compile(
        r"(GLOBAL_RESET)\s+through\s+(.*?)\s*(triggered|$)", re.IGNORECASE),
    "ppr_got_reset_cf9": re.compile(
        r"PPR check: GOT RESET CF9\s+(\d+)", re.IGNORECASE),
    "hsle_starting_reset": re.compile(
        r"RST_TAG\s+HSLE\s+starting\s+reset", re.IGNORECASE),
    "cbb_event": re.compile(
        r"RST_TAG\s+Creating\s+a\s+CBB\s+event\s+for\s+reset:\s*(\w+)", re.IGNORECASE),
    "inform_rst_tag": re.compile(
        r"Inform\s+RST_TAG:\s+Running\s+(\w+)\s+reset.*Event_ID", re.IGNORECASE),
    "begin_reset_flow": re.compile(
        r"BEGIN_RESET_FLOW", re.IGNORECASE),
    "rst_tag_reset_triggered": re.compile(
        r"RST_TAG\s+Reset\s+triggered", re.IGNORECASE),
    "boot_fsm_0x01": re.compile(
        r"BOOT_FSM\s+state\s+0x0?1\b", re.IGNORECASE),
    "reset_phase_6": re.compile(
        r"RESET_PHASE_6", re.IGNORECASE),
    "bios_first_fetch": re.compile(
        r"RST_TAG\s+waiting\s+for\s+BIOS\s+first\s+fetch", re.IGNORECASE),
    "idi_mux_enable": re.compile(
        r"IDI.*[Mm]ux.*enabl", re.IGNORECASE),
    "bios_aced": re.compile(
        r"BIOS_TAIL_ACED_FFFFFF00", re.IGNORECASE),
    "ppr_test_done": re.compile(
        r"PPR_TEST_DONE", re.IGNORECASE),
    "agr_event": re.compile(
        r"RST_TAG\s+AGR\s+event\s+triggered", re.IGNORECASE),
    "awr_event": re.compile(
        r"RST_TAG\s+AWR\s+event\s+triggered", re.IGNORECASE),
    "swr_event": re.compile(
        r"RST_TAG\s+Reset_BTN\s+event\s+triggered", re.IGNORECASE),
    "global_rst_warn": re.compile(
        r"GBL_RST_WARN", re.IGNORECASE),
    "auto_exit": re.compile(
        r"Auto exit triggered|RESET_TEST_COMPLETE", re.IGNORECASE),
}

# Patterns that indicate wait-for-log registrations (not actual events)
NOISE_PATTERNS = re.compile(
    r"wait-for-log|waiting for.*log|register.*callback|"
    r"Expected.*pattern|Watching for", re.IGNORECASE)


def open_log(run_dir):
    """Open testbench.log (plain or gzipped)."""
    plain = os.path.join(run_dir, "testbench.log")
    gzipped = os.path.join(run_dir, "testbench.log.gz")

    if os.path.exists(plain):
        return open(plain, "r", errors="replace"), False
    elif os.path.exists(gzipped):
        return gzip.open(gzipped, "rt", errors="replace"), True
    else:
        raise FileNotFoundError(f"No testbench.log found in {run_dir}")


def detect_resets(run_dir):
    """
    Detect all reset cycles in testbench.log.

    Scans the log once, collecting all reset-related events with their line
    numbers, then assembles them into ordered ResetCycle objects.

    Args:
        run_dir: Path to the HSLE run directory

    Returns:
        tuple: (list of ResetCycle, dict of summary info)
    """
    log_file, is_gzipped = open_log(run_dir)

    # Collect events
    events = []
    ppr_lines = []
    line_num = 0

    with log_file:
        for line in log_file:
            line_num += 1

            # Skip noise/registration lines
            if NOISE_PATTERNS.search(line):
                continue

            # Check PPR_TEST_DONE (actual occurrences only)
            if RESET_PATTERNS["ppr_test_done"].search(line):
                ppr_lines.append((line_num, line.strip()[:200]))

            # Check all reset-related patterns
            for pat_name in [
                "rst_tag_triggering", "ppr_got_reset_cf9",
                "hsle_starting_reset", "cbb_event", "begin_reset_flow",
                "rst_tag_reset_triggered", "bios_first_fetch",
                "bios_aced", "agr_event", "awr_event", "swr_event",
                "cold_reset_through", "warm_reset_through",
                "global_reset_through", "auto_exit",
                "idi_mux_enable", "reset_phase_6",
            ]:
                m = RESET_PATTERNS[pat_name].search(line)
                if m:
                    events.append((line_num, pat_name, m.groups(),
                                   line.strip()[:200]))

    total_lines = line_num

    # ---- Assemble events into reset cycles ----
    cycles = _assemble_cycles(events)

    # Assign PPR_TEST_DONE to cycles
    _assign_ppr(cycles, ppr_lines)

    # Determine pass/fail for each cycle
    _evaluate_cycles(cycles)

    # First boot PPR (before any reset)
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
    }

    return cycles, summary


def _assemble_cycles(events):
    """Parse event list into ResetCycle objects."""
    cycles = []
    current_cycle = None
    cycle_num = 0

    # Trigger event types that start a new cycle
    TRIGGER_TYPES = {
        "rst_tag_triggering", "ppr_got_reset_cf9",
        "cold_reset_through", "warm_reset_through",
        "global_reset_through", "agr_event", "awr_event", "swr_event",
    }

    for evt_line, evt_type, evt_groups, evt_content in events:
        if evt_type in TRIGGER_TYPES:
            # Finalize previous cycle if it had hardware entry
            if current_cycle and current_cycle.hsle_start_reset_line > 0:
                cycles.append(current_cycle)
                current_cycle = None

            if current_cycle is None:
                cycle_num += 1
                current_cycle = ResetCycle(
                    cycle_number=cycle_num,
                    reset_type="UNKNOWN",
                    trigger_source="UNKNOWN"
                )
                current_cycle.trigger_line = evt_line
                current_cycle.trigger_content = evt_content

            # Classify
            _classify_trigger(current_cycle, evt_type, evt_groups, evt_content)

        elif current_cycle:
            # Populate stage markers on current cycle
            if evt_type == "hsle_starting_reset":
                current_cycle.hsle_start_reset_line = evt_line
            elif evt_type == "cbb_event":
                current_cycle.cbb_event_line = evt_line
                if current_cycle.reset_type == "UNKNOWN" and evt_groups:
                    current_cycle.reset_type = evt_groups[0].upper()
            elif evt_type == "begin_reset_flow":
                current_cycle.begin_reset_flow_line = evt_line
            elif evt_type == "rst_tag_reset_triggered":
                current_cycle.reset_triggered_line = evt_line
            elif evt_type == "reset_phase_6":
                current_cycle.reset_phase_6_line = evt_line
            elif evt_type == "bios_first_fetch":
                current_cycle.bios_first_fetch_line = evt_line
            elif evt_type == "idi_mux_enable":
                current_cycle.idi_mux_line = evt_line
            elif evt_type == "bios_aced":
                current_cycle.bios_aced_line = evt_line
            elif evt_type == "auto_exit":
                current_cycle.auto_exit_line = evt_line

    # Finalize last cycle
    if current_cycle:
        cycles.append(current_cycle)

    return cycles


def _classify_trigger(cycle, evt_type, evt_groups, evt_content):
    """Classify reset type and trigger source from an event."""
    if evt_type == "ppr_got_reset_cf9":
        cf9_val = int(evt_groups[0]) if evt_groups else 0
        cycle.cf9_value = hex(cf9_val)
        if cf9_val in (14, 0xE):
            cycle.reset_type = "COLD"
        elif cf9_val in (6, 0x6):
            cycle.reset_type = "WARM"
        cycle.trigger_source = "CF9_WRITE"

    elif evt_type == "cold_reset_through":
        cycle.reset_type = "COLD"
        source = evt_groups[1] if len(evt_groups) > 1 else ""
        if "OS" in source.upper() or "reboot" in source.lower():
            cycle.trigger_source = "OS_REBOOT"
        elif "SOLAR" in source.upper():
            cycle.trigger_source = "SOLAR"
        else:
            cycle.trigger_source = "BIOS_INITIATED"

    elif evt_type == "warm_reset_through":
        cycle.reset_type = "WARM"
        source = evt_groups[1] if len(evt_groups) > 1 else ""
        if "OS" in source.upper() or "reboot" in source.lower():
            cycle.trigger_source = "OS_REBOOT"
        elif "SOLAR" in source.upper():
            cycle.trigger_source = "SOLAR"
        else:
            cycle.trigger_source = "BIOS_INITIATED"

    elif evt_type == "global_reset_through":
        cycle.reset_type = "GLOBAL"
        source = evt_groups[1] if len(evt_groups) > 1 else ""
        cycle.trigger_source = "SOLAR" if "SOLAR" in source.upper() else "CF9_WRITE"

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
        trigger_name = evt_groups[0] if evt_groups else ""
        tl = trigger_name.lower()
        if "cold" in tl:
            cycle.reset_type = "COLD"
            cycle.trigger_source = "OS_REBOOT"
        elif "warm" in tl:
            cycle.reset_type = "WARM"
            cycle.trigger_source = "OS_REBOOT"
        elif "global" in tl:
            cycle.reset_type = "GLOBAL"
            cycle.trigger_source = "SOLAR"
        elif "AGR" in trigger_name:
            cycle.reset_type = "GLOBAL"
            cycle.trigger_source = "AGR"
        elif "AWR" in trigger_name:
            cycle.reset_type = "WARM"
            cycle.trigger_source = "AWR"
        elif "SWR" in trigger_name:
            cycle.reset_type = "WARM"
            cycle.trigger_source = "SWR"


def _assign_ppr(cycles, ppr_lines):
    """Assign PPR_TEST_DONE occurrences to the appropriate cycle."""
    if not ppr_lines or not cycles:
        return

    ppr_idx = 0
    # Skip PPRs before first reset trigger (those belong to first boot)
    first_trigger = cycles[0].trigger_line
    while ppr_idx < len(ppr_lines) and ppr_lines[ppr_idx][0] < first_trigger:
        ppr_idx += 1

    # Assign remaining PPRs to cycles by finding the next PPR after each
    # cycle's BEGIN_RESET_FLOW
    for cycle in cycles:
        if cycle.begin_reset_flow_line > 0:
            while (ppr_idx < len(ppr_lines) and
                   ppr_lines[ppr_idx][0] < cycle.begin_reset_flow_line):
                ppr_idx += 1
            if ppr_idx < len(ppr_lines):
                cycle.ppr_test_done_line = ppr_lines[ppr_idx][0]
                ppr_idx += 1


def _evaluate_cycles(cycles):
    """Determine pass/fail status for each cycle."""
    for cycle in cycles:
        if cycle.ppr_test_done_line > 0:
            cycle.status = "PASS"
        elif cycle.auto_exit_line > 0 and cycle.bios_aced_line > 0:
            cycle.status = "PASS"
        elif cycle.hsle_start_reset_line == 0:
            cycle.status = "FAIL"
            cycle.failing_stage = 8
            cycle.failure_detail = (
                "Reset trigger detected but HSLE reset procedures never started")
        elif cycle.begin_reset_flow_line == 0:
            cycle.status = "FAIL"
            cycle.failing_stage = 9
            cycle.failure_detail = (
                "HSLE reset started but BEGIN_RESET_FLOW never reached")
        elif cycle.bios_first_fetch_line == 0:
            cycle.status = "FAIL"
            cycle.failing_stage = 10
            cycle.failure_detail = (
                "RTL phases did not complete (no BIOS first fetch wait)")
        elif cycle.bios_aced_line == 0:
            cycle.status = "FAIL"
            cycle.failing_stage = 11
            cycle.failure_detail = (
                "BIOS did not complete during second boot (no BIOS_TAIL_ACED)")
        else:
            cycle.status = "FAIL"
            cycle.failing_stage = 12
            cycle.failure_detail = (
                "BIOS completed but PPR_TEST_DONE not observed")


def classify_reset_origin(cycles, first_boot_ppr):
    """
    Determine if the first reset was BIOS-initiated or OS-initiated.

    If first PPR_TEST_DONE appears BEFORE the first reset trigger -> POST_OS_BOOT
    If first reset trigger appears BEFORE first PPR_TEST_DONE -> BIOS_INITIATED
    If no first PPR at all -> BIOS_INITIATED
    """
    if not cycles:
        return "NONE"

    first_trigger = cycles[0].trigger_line

    if first_boot_ppr and first_boot_ppr[0] < first_trigger:
        return "POST_OS_BOOT"
    else:
        return "BIOS_INITIATED"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: reset_detector.py <run_dir>")
        sys.exit(1)

    run_dir = sys.argv[1]
    cycles, summary = detect_resets(run_dir)

    print(f"\nHSLE Reset Detection: {run_dir}")
    print(f"Total log lines: {summary['total_lines']}")
    print(f"PPR_TEST_DONE count: {summary['ppr_total_count']}")
    print(f"Reset cycles detected: {summary['reset_cycle_count']}")
    print(f"{'='*70}")

    if not cycles:
        print("  No reset cycles detected (normal cold boot run)")
    else:
        origin = classify_reset_origin(cycles, summary["first_boot_ppr"])
        print(f"  Reset origin: {origin}")
        print()
        for c in cycles:
            print(f"  Cycle {c.cycle_number}: {c.reset_type} ({c.trigger_source})")
            print(f"    Status: {c.status}")
            print(f"    Trigger line: {c.trigger_line}")
            print(f"    CF9 value: {c.cf9_value or 'N/A'}")
            print(f"    HSLE start reset: {c.hsle_start_reset_line or 'N/A'}")
            print(f"    BEGIN_RESET_FLOW: {c.begin_reset_flow_line or 'N/A'}")
            print(f"    BIOS first fetch: {c.bios_first_fetch_line or 'N/A'}")
            print(f"    BIOS ACED: {c.bios_aced_line or 'N/A'}")
            print(f"    PPR_TEST_DONE: {c.ppr_test_done_line or 'N/A'}")
            if c.status == "FAIL":
                print(f"    FAILING STAGE: {c.failing_stage}")
                print(f"    FAILURE: {c.failure_detail}")
            print()
