#!/usr/bin/env python3
"""
HSLE Debug Summary Generator

Generates the structured debug summary file (hsle_debug_agent_summary.txt)
from milestone extraction and reset detection results.

Usage:
    from summary_generator import generate_summary
    path = generate_summary(run_dir, stage_results, cycles, summary_info)
"""

import os
from datetime import datetime


def generate_summary(run_dir, stage_results, reset_cycles, reset_summary,
                     output_path=None):
    """
    Generate the HSLE debug summary file.

    Args:
        run_dir: Path to the HSLE run directory
        stage_results: dict from milestone_extractor.extract_milestones()
        reset_cycles: list from reset_detector.detect_resets()
        reset_summary: dict from reset_detector.detect_resets()
        output_path: Override output file path

    Returns:
        str: Path where the summary was written
    """
    if output_path is None:
        output_path = os.path.join(run_dir, "hsle_debug_agent_summary.txt")

    # Determine scenario and overall result
    if not reset_cycles:
        overall_result = _determine_cold_boot_result(stage_results)
        content = _gen_cold_boot(run_dir, stage_results, reset_summary,
                                 overall_result)
    else:
        overall_result = _determine_reset_result(stage_results, reset_cycles,
                                                  reset_summary)
        content = _gen_reset(run_dir, stage_results, reset_cycles,
                             reset_summary, overall_result)

    # Write
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


# --------------------------------------------------------------------------
# Result determination
# --------------------------------------------------------------------------

def _determine_cold_boot_result(stage_results):
    for stage in range(8):
        if stage in stage_results:
            if stage_results[stage].status in ("FAIL", "NOT_REACHED"):
                return "FAIL"
            if stage_results[stage].status == "PARTIAL":
                return "FAIL"
    return "PASS"


def _determine_reset_result(stage_results, reset_cycles, reset_summary):
    for cycle in reset_cycles:
        if cycle.status == "FAIL":
            return "FAIL"
    if reset_summary["ppr_total_count"] >= 2:
        return "PASS"
    # Single PPR with all cycles passing (auto_exit case)
    if all(c.status == "PASS" for c in reset_cycles):
        return "PASS"
    return "FAIL"


# --------------------------------------------------------------------------
# Cold boot summary
# --------------------------------------------------------------------------

def _gen_cold_boot(run_dir, stage_results, info, overall_result):
    sep = "=" * 80
    L = []
    L.append(sep)
    L.append("  HSLE RUN DEBUG SUMMARY")
    L.append(sep)
    L.append("")
    L.append(f"  Run Directory : {run_dir}")
    L.append(f"  Analysis Date : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"  Total Lines   : {info.get('total_lines', 'N/A')}")
    L.append(f"  Scenario      : Normal Cold Boot (no reset cycles)")
    L.append(f"  Overall Result: {overall_result}")
    L.append("")

    # Stage checklist
    L.append(sep)
    L.append("  STAGE-BY-STAGE CHECKLIST (Stages 0-7)")
    L.append(sep)
    L.append("")

    for stage in range(8):
        if stage in stage_results:
            r = stage_results[stage]
            detail = ""
            if r.milestones:
                last = r.milestones[-1]
                detail = f"  (last: {last.pattern_name} @ line {last.line_number})"
            if r.missing:
                detail += f"  MISSING: {', '.join(r.missing)}"
            L.append(f"  Stage {stage}: {r.status}{detail}")

    L.append("")

    # Failure analysis
    if overall_result == "FAIL":
        L.append(sep)
        L.append("  FAILURE ANALYSIS")
        L.append(sep)
        L.append("")
        for stage in range(8):
            if stage in stage_results:
                r = stage_results[stage]
                if r.status in ("FAIL", "PARTIAL"):
                    L.append(f"  Failing Stage : {stage}")
                    L.append(f"  Status        : {r.status}")
                    if r.missing:
                        L.append(f"  Missing       : {', '.join(r.missing)}")
                    if r.milestones:
                        last = r.milestones[-1]
                        L.append(f"  Last Milestone: {last.pattern_name} "
                                 f"at line {last.line_number}")
                        L.append(f"  Last Content  : {last.line_content[:120]}")
                    L.append("")
                    L.append("  Recommended Actions:")
                    L.append(_get_stage_recommendations(stage))
                    break
        L.append("")

    L.append(sep)
    L.append("  END OF SUMMARY")
    L.append(sep)

    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Reset scenario summary
# --------------------------------------------------------------------------

def _gen_reset(run_dir, stage_results, reset_cycles, info, overall_result):
    from reset_detector import classify_reset_origin

    sep = "=" * 80
    L = []
    L.append(sep)
    L.append("  HSLE RUN DEBUG SUMMARY")
    L.append(sep)
    L.append("")
    L.append(f"  Run Directory : {run_dir}")
    L.append(f"  Analysis Date : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"  Total Lines   : {info.get('total_lines', 'N/A')}")
    L.append(f"  PPR_TEST_DONE : {info.get('ppr_total_count', 0)} occurrence(s)")
    L.append(f"  Reset Cycles  : {len(reset_cycles)}")

    reset_types = [f"{c.reset_type} ({c.trigger_source})" for c in reset_cycles]
    L.append(f"  Reset Types   : {' -> '.join(reset_types)}")

    scenario = ("Back-to-back " if len(reset_cycles) > 1 else "") + "Reset"
    L.append(f"  Scenario      : {scenario} "
             f"({' -> '.join(c.reset_type for c in reset_cycles)})")
    L.append(f"  Overall Result: {overall_result}")
    L.append("")

    origin = classify_reset_origin(reset_cycles, info.get("first_boot_ppr"))

    # First boot stages
    L.append(sep)
    L.append("  FIRST BOOT STAGE CHECKLIST (Stages 0-7)")
    L.append(sep)
    L.append("")

    for stage in range(8):
        if stage in stage_results:
            r = stage_results[stage]
            status_str = r.status
            if origin == "BIOS_INITIATED":
                if stage == 6 and r.status == "PARTIAL":
                    status_str = "PARTIAL (BIOS-initiated reset during this stage)"
                elif stage == 7 and r.status in ("NOT_REACHED", "FAIL"):
                    status_str = ("NOT REACHED "
                                  "(expected: BIOS triggered reset before OS boot)")
            detail = ""
            if r.milestones:
                last = r.milestones[-1]
                detail = f"  (last: {last.pattern_name} @ line {last.line_number})"
            L.append(f"  Stage {stage}: {status_str}{detail}")

    L.append("")

    # Reset cycles
    L.append(sep)
    L.append("  RESET CYCLE ANALYSIS")
    L.append(sep)
    L.append("")

    for cycle in reset_cycles:
        L.append(f"  --- Reset Cycle {cycle.cycle_number}: {cycle.reset_type} "
                 f"({cycle.trigger_source}) ---")
        L.append(f"  Status    : {cycle.status}")
        L.append(f"  CF9 Value : {cycle.cf9_value or 'N/A (platform-injected)'}")
        L.append("")
        L.append(f"  Stage 8  (Trigger)        : "
                 f"{'line ' + str(cycle.trigger_line) if cycle.trigger_line else 'N/A'}")
        L.append(f"  Stage 9  (HW Entry)       : "
                 f"{'line ' + str(cycle.hsle_start_reset_line) if cycle.hsle_start_reset_line else 'NOT REACHED'}")
        L.append(f"           BEGIN_RESET_FLOW  : "
                 f"{'line ' + str(cycle.begin_reset_flow_line) if cycle.begin_reset_flow_line else 'NOT REACHED'}")
        L.append(f"  Stage 10 (RTL Phases)     : "
                 f"{'BIOS fetch wait @ line ' + str(cycle.bios_first_fetch_line) if cycle.bios_first_fetch_line else 'NOT REACHED'}")
        L.append(f"  Stage 11 (Second BIOS)    : "
                 f"{'BIOS_ACED @ line ' + str(cycle.bios_aced_line) if cycle.bios_aced_line else 'NOT REACHED'}")
        L.append(f"  Stage 12 (Second OS/PPR)  : "
                 f"{'PPR_TEST_DONE @ line ' + str(cycle.ppr_test_done_line) if cycle.ppr_test_done_line else 'NOT REACHED'}")
        L.append("")

        if cycle.status == "FAIL":
            L.append(f"  *** FAILURE in Stage {cycle.failing_stage} ***")
            L.append(f"  Detail: {cycle.failure_detail}")
            L.append("")
            L.append("  Recommended Actions:")
            L.append(_get_reset_stage_recommendations(cycle.failing_stage,
                                                      cycle.reset_type))
            L.append("")

    # Overall failure analysis
    if overall_result == "FAIL":
        L.append(sep)
        L.append("  FAILURE SUMMARY")
        L.append(sep)
        L.append("")
        failing_cycles = [c for c in reset_cycles if c.status == "FAIL"]
        if failing_cycles:
            fc = failing_cycles[0]
            L.append(f"  Failing Cycle  : {fc.cycle_number} "
                     f"({fc.reset_type} / {fc.trigger_source})")
            L.append(f"  Failing Stage  : {fc.failing_stage}")
            L.append(f"  Failure Detail : {fc.failure_detail}")
        else:
            for stage in range(8):
                if stage in stage_results:
                    if stage_results[stage].status in ("FAIL", "PARTIAL"):
                        L.append(f"  Failing Stage: First boot Stage {stage}")
                        break
        L.append("")

    L.append(sep)
    L.append("  END OF SUMMARY")
    L.append(sep)

    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------

def _get_stage_recommendations(stage):
    recs = {
        0: "  - Check spark_session.log for SPARK launch errors\n"
           "  - Verify ZeBu hardware allocation succeeded",
        1: "  - Check emu_log for ZSE5 device initialization errors\n"
           "  - Verify ZeBu board connectivity",
        2: "  - Check IDI link errors in emu.devices stream\n"
           "  - Verify model version compatibility",
        3: "  - Check ZeBu compilation log for RTL build errors\n"
           "  - Verify RTL model path exists and is accessible",
        4: "  - Check Simics launch log for VP creation errors\n"
           "  - Verify Simics license availability",
        5: "  - See reset_phase_flow.txt for detailed sub-phase analysis\n"
           "  - Check which RESET_PHASE is missing (1-6)\n"
           "  - Check both imh0/imh1 and die8/die9 for symmetry failures",
        6: "  - See bios_flow.txt for BIOS sub-phase (6.0-6.6) analysis\n"
           "  - Check serconsole for EWL/IPSD/RC Fatal errors\n"
           "  - Run decode_ewl.py on the serconsole output segment\n"
           "  - Check for BIOS-initiated reset (CF9 write before PPR_TEST_DONE)",
        7: "  - Check serconsole for OS boot errors (kernel panic, GRUB)\n"
           "  - Verify PPR test workload and OS image configuration",
    }
    return recs.get(stage, "  - Check testbench.log around the last seen milestone")


def _get_reset_stage_recommendations(stage, reset_type):
    recs = {
        8: "  - Verify post-setup script loaded (check spark_session.log)\n"
           "  - Check os_reset_triggers.simics dispatch table\n"
           "  - Verify reset_to_trigger parameter matches script name",
        9: (f"  - Check serconsole for OS/BIOS errors before reset entry\n"
            f"  - For {reset_type}: verify "
            + ("PLTRST_SYNC wait" if reset_type != "GLOBAL" else "GBL_RST_WARN")
            + " completes\n"
            "  - Check for VP quiesce failure (stuck instruction)"),
        10: "  - See reset_phase_flow.txt for HWRS sub-phase analysis\n"
            "  - Check BOOT_FSM stuck state (which primecode state?)\n"
            "  - Verify both imh8/imh9 die symmetry in second boot",
        11: "  - See bios_flow.txt for BIOS sub-phase (6.0-6.6) analysis\n"
            "  - Run decode_ewl.py on second boot serconsole segment\n"
            "  - Check for second BIOS-initiated reset (nested reset)",
        12: "  - Check serconsole for second OS boot errors\n"
            "  - Verify PPR test workload starts on second boot\n"
            "  - Check emu.devices for test failure markers",
    }
    return recs.get(stage, "  - Check testbench.log around last activity line")


if __name__ == "__main__":
    print("summary_generator.py - Use via main.py or import directly")
    print("Usage: python main.py <run_dir>")
