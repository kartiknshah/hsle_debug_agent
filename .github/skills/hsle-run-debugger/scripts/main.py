#!/usr/bin/env python3
"""
HSLE Run Debugger - Main Entry Point

Runs the complete debug pipeline: milestone extraction -> reset detection ->
summary generation. Produces the summary file in the repo-local result directory.

Usage:
    python3 main.py <run_directory>
    python3 main.py <run_directory> --output /path/to/output.txt
    python3 main.py <run_directory> --verbose
    python3 main.py <run_directory> --json  # machine-readable output
"""

import sys
import os
import json
import argparse
import time

# Add script dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from milestone_extractor import extract_milestones, check_results_log
from reset_detector import detect_resets, classify_reset_origin
from summary_generator import generate_summary, _determine_cold_boot_result, _determine_reset_result


def main():
    parser = argparse.ArgumentParser(
        description="HSLE Run Debugger - Automated debug analysis")
    parser.add_argument("run_dir", help="Path to HSLE run directory")
    parser.add_argument("--output", "-o", help="Override output file path")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed info to stdout")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON summary to stdout")
    args = parser.parse_args()
    
    run_dir = os.path.abspath(args.run_dir)
    
    # Validate
    if not os.path.isdir(run_dir):
        print(f"ERROR: Directory not found: {run_dir}", file=sys.stderr)
        sys.exit(1)
    
    log_plain = os.path.join(run_dir, "testbench.log")
    log_gz = os.path.join(run_dir, "testbench.log.gz")
    if not os.path.exists(log_plain) and not os.path.exists(log_gz):
        print(f"ERROR: No testbench.log in {run_dir}", file=sys.stderr)
        sys.exit(1)
    
    start = time.time()
    
    if not args.json:
        print(f"HSLE Run Debugger")
        print(f"Run: {run_dir}")
        print("=" * 70)
    
    # Step 1: Milestones
    if not args.json:
        print("\n[1/3] Extracting milestones...")
    stage_results, total_lines = extract_milestones(run_dir)
    
    if args.verbose:
        for s in sorted(stage_results):
            r = stage_results[s]
            print(f"  Stage {s}: {r.status}", end="")
            if r.milestones:
                print(f" (last: {r.milestones[-1].substage} @ {r.milestones[-1].line_number})", end="")
            if r.missing:
                print(f" MISSING: {r.missing}", end="")
            print()
    
    # Step 2: Reset detection
    if not args.json:
        print("[2/3] Detecting resets...")
    reset_cycles, reset_summary = detect_resets(run_dir)
    
    if not args.json:
        if reset_cycles:
            origin = classify_reset_origin(reset_cycles, reset_summary.get("first_boot_ppr"))
            print(f"  Found {len(reset_cycles)} reset cycle(s), origin: {origin}")
            for c in reset_cycles:
                print(f"    Cycle {c.cycle_number}: {c.reset_type}({c.trigger_source}) -> {c.status}")
        else:
            print("  No reset cycles (normal cold boot)")
    
    # Step 3: Generate summary
    if not args.json:
        print("[3/3] Writing summary...")
    
    if "total_lines" not in reset_summary:
        reset_summary["total_lines"] = total_lines
    
    output_path = generate_summary(run_dir, stage_results, reset_cycles,
                                   reset_summary, args.output)
    
    # Determine result
    if not reset_cycles:
        result = _determine_cold_boot_result(stage_results)
    else:
        result = _determine_reset_result(stage_results, reset_cycles, reset_summary)
    
    elapsed = time.time() - start
    
    if args.json:
        # Machine-readable output
        out = {
            "run_dir": run_dir,
            "total_lines": total_lines,
            "result": result,
            "scenario": "reset" if reset_cycles else "cold_boot",
            "reset_cycles": len(reset_cycles),
            "ppr_count": reset_summary.get("ppr_total_count", 0),
            "output_file": output_path,
            "elapsed_seconds": round(elapsed, 1),
            "stages": {},
            "cycles": [],
        }
        for s in sorted(stage_results):
            r = stage_results[s]
            out["stages"][str(s)] = {
                "status": r.status,
                "last_line": r.milestones[-1].line_number if r.milestones else 0,
                "missing": r.missing,
            }
        for c in reset_cycles:
            out["cycles"].append({
                "number": c.cycle_number,
                "type": c.reset_type,
                "source": c.trigger_source,
                "status": c.status,
                "trigger_line": c.trigger_line,
                "failing_stage": c.failing_stage if c.status == "FAIL" else None,
                "failure_detail": c.failure_detail if c.status == "FAIL" else None,
            })
        print(json.dumps(out, indent=2))
    else:
        print(f"\n{'=' * 70}")
        print(f"Summary written to: {output_path}")
        print(f"Overall Result: {result}")
        print(f"Time: {elapsed:.1f}s")
        results_log = check_results_log(run_dir)
        if results_log:
            print(f"results.log: {results_log}")
    
    # Exit code: 0=pass, 1=fail, 2=error
    sys.exit(0 if result == "PASS" else 1)


if __name__ == "__main__":
    main()
