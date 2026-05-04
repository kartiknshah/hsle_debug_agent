#!/usr/bin/env python3
"""
HSLE Debug Summary Generator - Produces structured summary file.

Generates hsle_debug_agent_summary.txt from milestone and reset detection
results. Uses templates matching the agent's output format.
"""

import os
from datetime import datetime


def generate_summary(run_dir, stage_results, reset_cycles, reset_summary,
                     output_path=None):
    """
    Generate structured debug summary file.
    
    Returns: path where summary was written.
    """
    if output_path is None:
        output_path = os.path.join(run_dir, "hsle_debug_agent_summary.txt")
    
    if not reset_cycles:
        result = _cold_boot_result(stage_results)
        content = _fmt_cold_boot(run_dir, stage_results, reset_summary, result)
    else:
        result = _reset_result(stage_results, reset_cycles, reset_summary)
        content = _fmt_reset(run_dir, stage_results, reset_cycles,
                             reset_summary, result)
    
    try:
        with open(output_path, "w") as f:
            f.write(content)
        return output_path
    except PermissionError:
        fallback = os.path.join(
            os.getcwd(),
            os.path.basename(run_dir) + "_hsle_debug_agent_summary.txt")
        with open(fallback, "w") as f:
            f.write(content)
        return fallback


def _cold_boot_result(stages):
    """Determine overall cold boot result."""
    for s in range(8):
        if s in stages and stages[s].status in ("FAIL", "NOT_REACHED"):
            return "FAIL"
        if s in stages and stages[s].status == "PARTIAL" and s < 6:
            return "FAIL"
    # Stage 6 PARTIAL with Stage 7 PASS is still PASS (BIOS completed enough)
    if 7 in stages and stages[7].status == "PASS":
        return "PASS"
    if 6 in stages and stages[6].status == "PARTIAL":
        return "FAIL"
    return "PASS"


def _reset_result(stages, cycles, summary):
    """Determine overall reset scenario result."""
    for c in cycles:
        if c.status == "FAIL":
            return "FAIL"
    if summary.get("ppr_total_count", 0) >= 2:
        return "PASS"
    if all(c.status == "PASS" for c in cycles):
        return "PASS"
    return "FAIL"


# Expose for main.py
_determine_cold_boot_result = _cold_boot_result
_determine_reset_result = _reset_result


def _sep():
    return "=" * 80


def _fmt_cold_boot(run_dir, stages, info, result):
    """Format normal cold boot summary."""
    L = []
    L.append(_sep())
    L.append("  HSLE RUN DEBUG SUMMARY")
    L.append(_sep())
    L.append("")
    L.append(f"  Run Directory  : {run_dir}")
    L.append(f"  Analysis Date  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"  Log Lines      : {info.get('total_lines', 'N/A')}")
    L.append(f"  Scenario       : Normal Cold Boot (no reset cycles detected)")
    L.append(f"  Overall Result : {result}")
    L.append("")
    
    # Results.log
    from milestone_extractor import check_results_log
    res = check_results_log(run_dir)
    L.append(f"  results.log    : {res or 'NOT FOUND'}")
    L.append(f"  PPR_TEST_DONE  : {info.get('ppr_total_count', 0)} occurrence(s)")
    L.append("")
    
    # Stage checklist
    L.append(_sep())
    L.append("  STAGE-BY-STAGE CHECKLIST")
    L.append(_sep())
    L.append("")
    
    for s in range(8):
        if s in stages:
            r = stages[s]
            line_info = ""
            if r.milestones:
                last = r.milestones[-1]
                line_info = f" @ line {last.line_number} ({last.substage})"
            miss = f"  [MISSING: {', '.join(r.missing)}]" if r.missing else ""
            L.append(f"  Stage {s}: {r.status}{line_info}{miss}")
    
    L.append("")
    
    # Failure analysis
    if result == "FAIL":
        L.append(_sep())
        L.append("  FAILURE ANALYSIS")
        L.append(_sep())
        L.append("")
        for s in range(8):
            if s in stages and stages[s].status in ("FAIL", "PARTIAL"):
                r = stages[s]
                L.append(f"  Failing Stage  : {s}")
                L.append(f"  Status         : {r.status}")
                if r.missing:
                    L.append(f"  Missing Markers: {', '.join(r.missing)}")
                if r.milestones:
                    last = r.milestones[-1]
                    L.append(f"  Last Milestone : {last.substage} at line {last.line_number}")
                    L.append(f"  Last Content   : {last.line_content[:120]}")
                L.append("")
                L.append("  Recommended Actions:")
                L.append(_stage_recs(s))
                break
        L.append("")
    
    L.append(_sep())
    L.append("  END OF SUMMARY")
    L.append(_sep())
    return "\n".join(L) + "\n"


def _fmt_reset(run_dir, stages, cycles, info, result):
    """Format reset scenario summary."""
    from reset_detector import classify_reset_origin
    origin = classify_reset_origin(cycles, info.get("first_boot_ppr"))
    
    L = []
    L.append(_sep())
    L.append("  HSLE RUN DEBUG SUMMARY")
    L.append(_sep())
    L.append("")
    L.append(f"  Run Directory  : {run_dir}")
    L.append(f"  Analysis Date  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"  Log Lines      : {info.get('total_lines', 'N/A')}")
    L.append(f"  PPR_TEST_DONE  : {info.get('ppr_total_count', 0)} occurrence(s)")
    L.append(f"  Reset Cycles   : {len(cycles)}")
    
    types = " -> ".join(f"{c.reset_type}({c.trigger_source})" for c in cycles)
    L.append(f"  Reset Chain    : {types}")
    L.append(f"  Reset Origin   : {origin}")
    
    scenario = "Back-to-back Reset" if len(cycles) > 1 else "Single Reset"
    L.append(f"  Scenario       : {scenario}")
    L.append(f"  Overall Result : {result}")
    L.append("")
    
    # Results.log
    from milestone_extractor import check_results_log
    res = check_results_log(run_dir)
    L.append(f"  results.log    : {res or 'NOT FOUND'}")
    if info.get("rca_check_found"):
        L.append(f"  rca_check      : {info['rca_check_found']}")
    L.append("")
    
    # First boot stages
    L.append(_sep())
    L.append("  FIRST BOOT STAGES (0-7)")
    L.append(_sep())
    L.append("")
    
    for s in range(8):
        if s in stages:
            r = stages[s]
            status = r.status
            if origin == "BIOS_INITIATED":
                if s == 6 and r.status == "PARTIAL":
                    status = "PARTIAL (BIOS-initiated reset during this stage)"
                elif s == 7 and r.status in ("NOT_REACHED", "FAIL"):
                    status = "NOT REACHED (expected: reset before OS boot)"
            line_info = ""
            if r.milestones:
                last = r.milestones[-1]
                line_info = f" @ line {last.line_number} ({last.substage})"
            L.append(f"  Stage {s}: {status}{line_info}")
    
    L.append("")
    
    # Reset cycles
    L.append(_sep())
    L.append("  RESET CYCLE ANALYSIS")
    L.append(_sep())
    
    for c in cycles:
        L.append("")
        L.append(f"  --- Cycle {c.cycle_number}: {c.reset_type} ({c.trigger_source}) ---")
        L.append(f"  Status         : {c.status}")
        L.append(f"  CF9 Value      : {c.cf9_value or 'N/A (platform-injected)'}")
        L.append(f"  Trigger        : line {c.trigger_line} | {c.trigger_content[:100]}")
        L.append("")
        L.append(f"    Stage 8  Trigger         : {'line ' + str(c.trigger_line) if c.trigger_line else 'N/A'}")
        L.append(f"    Stage 9  HW Entry        : {'line ' + str(c.hsle_start_reset_line) if c.hsle_start_reset_line else 'NOT REACHED'}")
        L.append(f"             BEGIN_RESET_FLOW : {'line ' + str(c.begin_reset_flow_line) if c.begin_reset_flow_line else 'NOT REACHED'}")
        L.append(f"    Stage 10 RTL Phases      : {'line ' + str(c.bios_first_fetch_line) if c.bios_first_fetch_line else 'NOT REACHED'}")
        L.append(f"    Stage 11 BIOS            : {'ACED @ line ' + str(c.bios_aced_line) if c.bios_aced_line else 'NOT REACHED'}")
        L.append(f"    Stage 12 OS/PPR          : {'line ' + str(c.ppr_test_done_line) if c.ppr_test_done_line else 'NOT REACHED'}")
        
        if c.status == "FAIL":
            L.append("")
            L.append(f"  *** FAILURE at Stage {c.failing_stage} ***")
            L.append(f"  Detail: {c.failure_detail}")
            L.append("")
            L.append("  Recommended Actions:")
            L.append(_reset_recs(c.failing_stage, c.reset_type))
    
    L.append("")
    
    # Overall failure summary
    if result == "FAIL":
        L.append(_sep())
        L.append("  FAILURE SUMMARY")
        L.append(_sep())
        L.append("")
        failing = [c for c in cycles if c.status == "FAIL"]
        if failing:
            fc = failing[0]
            L.append(f"  Failing Cycle  : {fc.cycle_number} ({fc.reset_type} / {fc.trigger_source})")
            L.append(f"  Failing Stage  : {fc.failing_stage}")
            L.append(f"  Detail         : {fc.failure_detail}")
            L.append("")
            L.append("  Debug Pointers:")
            L.append(f"  - Last reached marker line: {_last_marker_line(fc)}")
            L.append(f"  - grep -n around that line for context")
            if fc.failing_stage == 11:
                L.append("  - Check serconsole for BIOS errors after BEGIN_RESET_FLOW")
                L.append("  - Run bios-issue-analyzer on second boot segment")
            elif fc.failing_stage == 10:
                L.append("  - Check BOOT_FSM states for stuck primecode")
                L.append("  - Verify both imh8/imh9 symmetry")
        L.append("")
    
    L.append(_sep())
    L.append("  END OF SUMMARY")
    L.append(_sep())
    return "\n".join(L) + "\n"


def _last_marker_line(cycle):
    """Find last non-zero marker line in a cycle."""
    markers = [cycle.auto_exit_line, cycle.ppr_test_done_line,
               cycle.bios_aced_line, cycle.idi_mux_line,
               cycle.bios_first_fetch_line, cycle.reset_phase_6_line,
               cycle.primecode_start_line, cycle.begin_reset_flow_line,
               cycle.reset_triggered_line, cycle.hsle_start_reset_line,
               cycle.trigger_line]
    for m in markers:
        if m > 0:
            return m
    return 0


def _stage_recs(stage):
    """Cold boot stage recommendations."""
    R = {
        0: "  - Check spark_session.log for launch errors\n  - Verify ZeBu hardware allocation",
        1: "  - Check emu_log for ZSE5 init errors\n  - Verify ZeBu board connectivity",
        2: "  - Check IDI link errors\n  - Verify model version compatibility",
        3: "  - Check ZeBu compilation log\n  - Verify RTL model path accessible",
        4: "  - Check Simics launch/license errors\n  - Verify VP creation logs",
        5: "  - See reset_phase_flow.txt for sub-phase analysis\n  - Check RESET_PHASE_1-6 progression",
        6: "  - See bios_flow.txt for sub-phases 6.0-6.6\n  - Check for BIOS-initiated reset (CF9 write)\n  - Run bios-issue-analyzer",
        7: "  - Check serconsole for OS errors\n  - Verify PPR test workload config",
    }
    return R.get(stage, "  - Inspect testbench.log around last milestone")


def _reset_recs(stage, rtype):
    """Reset stage recommendations."""
    R = {
        8: "  - Verify post-setup script loaded\n  - Check os_reset_triggers.simics dispatch",
        9: f"  - Check serconsole for errors before hardware entry\n  - Verify {'PLTRST_SYNC' if rtype != 'GLOBAL' else 'GBL_RST_WARN'} completion",
        10: "  - See reset_phase_flow.txt for HWRS sub-phases\n  - Check BOOT_FSM stuck state\n  - Verify imh8/imh9 symmetry",
        11: "  - Check if IDI Mux was enabled after RESET_PHASE_6\n  - See bios_flow.txt for BIOS sub-phases\n  - Run bios-issue-analyzer on second boot segment",
        12: "  - Check serconsole for second OS boot errors\n  - Verify PPR workload starts on second boot",
    }
    return R.get(stage, "  - Inspect testbench.log context")
