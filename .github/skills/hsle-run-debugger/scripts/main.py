#!/usr/bin/env python3
"""
HSLE Run Debugger - Main CLI Entry Point

Analyzes HSLE emulation runs by extracting milestones, detecting reset cycles,
and generating a structured debug summary.

Usage:
    python main.py <run_directory>
    python main.py /path/to/hsle_run.0 --verbose
    python main.py /path/to/hsle_run.0 --output /tmp/summary.txt

Output:
    Writes hsle_debug_agent_summary.txt to the run directory (or current
    directory if write permission is denied).
"""

import sys
import os
import argparse

# Add script directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from milestone_extractor import (extract_milestones, check_results_log,
                                  get_log_line_count)
from reset_detector import detect_resets, classify_reset_origin
from summary_generator import generate_summary


def main():
    parser = argparse.ArgumentParser(
        description="HSLE Run Debugger - Analyze and debug HSLE emulation runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py /path/to/hsle_run.0
  python main.py /path/to/hsle_run.0 --output /tmp/summary.txt
  python main.py /path/to/hsle_run.0 --verbose
  python main.py /path/to/hsle_run.0 --milestones-only
  python main.py /path/to/hsle_run.0 --resets-only
        """
    )
    parser.add_argument("run_dir", help="Path to the HSLE run directory")
    parser.add_argument("--output", "-o",
                        help="Override output file path for summary")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed milestone/reset info to stdout")
    parser.add_argument("--milestones-only", action="store_true",
                        help="Only extract milestones (skip reset detection)")
    parser.add_argument("--resets-only", action="store_true",
                        help="Only detect resets (skip milestone extraction)")

    args = parser.parse_args()
    run_dir = os.path.abspath(args.run_dir)

    # Validate
    if not os.path.isdir(run_dir):
        print(f"ERROR: Run directory not found: {run_dir}", file=sys.stderr)
        sys.exit(1)

    log_plain = os.path.join(run_dir, "testbench.log")
    log_gz = os.path.join(run_dir, "testbench.log.gz")
    if not os.path.exists(log_plain) and not os.path.exists(log_gz):
        print(f"ERROR: No testbench.log found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    print("HSLE Run Debugger")
    print(f"Run: {run_dir}")
    print("=" * 70)

    # Step 1: Extract milestones
    if not args.resets_only:
        print("\n[Step 1] Extracting stage milestones...")
        stage_results, total_lines = extract_milestones(run_dir)
        if args.verbose:
            for stage in sorted(stage_results.keys()):
                r = stage_results[stage]
                print(f"  Stage {stage}: {r.status}")
                for m in r.milestones:
                    print(f"    [{m.substage}] line {m.line_number}: "
                          f"{m.line_content[:80]}")
                if r.missing:
                    print(f"    MISSING: {', '.join(r.missing)}")
    else:
        stage_results = {}
        total_lines = get_log_line_count(run_dir)

    # Step 2: Detect resets
    if not args.milestones_only:
        print("[Step 2] Detecting reset cycles...")
        reset_cycles, reset_summary = detect_resets(run_dir)
        if reset_cycles:
            origin = classify_reset_origin(
                reset_cycles, reset_summary["first_boot_ppr"])
            print(f"  Reset cycles found: {len(reset_cycles)}")
            print(f"  Reset origin: {origin}")
            for c in reset_cycles:
                icon = "PASS" if c.status == "PASS" else "FAIL"
                print(f"    Cycle {c.cycle_number}: {c.reset_type} "
                      f"({c.trigger_source}) -> {icon}")
                if args.verbose and c.status == "FAIL":
                    print(f"      Failing stage: {c.failing_stage}")
                    print(f"      Detail: {c.failure_detail}")
        else:
            print("  No reset cycles detected (normal cold boot run)")
    else:
        reset_cycles = []
        reset_summary = {
            "total_lines": total_lines, "ppr_total_count": 0,
            "ppr_lines": [], "reset_cycle_count": 0, "first_boot_ppr": None,
        }

    # Step 3: Check results.log
    results_content = check_results_log(run_dir)
    if results_content:
        print(f"\n[Info] results.log: {results_content}")
    else:
        print("\n[Info] results.log: NOT FOUND")

    # Step 4: Generate summary
    if not args.milestones_only and not args.resets_only:
        print("\n[Step 3] Generating debug summary...")
        if "total_lines" not in reset_summary:
            reset_summary["total_lines"] = total_lines
        output_path = generate_summary(
            run_dir, stage_results, reset_cycles, reset_summary, args.output)
        print("\n" + "=" * 70)
        print(f"Debug summary written to: {output_path}")
        if not reset_cycles:
            from summary_generator import _determine_cold_boot_result
            result = _determine_cold_boot_result(stage_results)
        else:
            from summary_generator import _determine_reset_result
            result = _determine_reset_result(
                stage_results, reset_cycles, reset_summary)
        print(f"Overall Result: {result}")
    print()


if __name__ == "__main__":
    main()
