HSLE Run Debugger Scripts
=========================

These Python scripts implement the automated debug analysis for HSLE emulation
runs. They can be used standalone (CLI) or imported as modules.

Requirements:
  - Python 3.8+
  - No external dependencies (stdlib only)

Usage:
  python3 main.py /path/to/hsle_run_directory
  python3 main.py /path/to/hsle_run_directory --output /tmp/summary.txt
  python3 main.py /path/to/hsle_run_directory --verbose
  python3 main.py /path/to/hsle_run_directory --milestones-only
  python3 main.py /path/to/hsle_run_directory --resets-only

Modules:
  main.py                - CLI entry point; orchestrates the 3-step analysis
  milestone_extractor.py - Stage 0-7 milestone extraction from testbench.log
  reset_detector.py      - Reset cycle detection and classification
  summary_generator.py   - Structured debug summary file generation

How It Works:
  1. milestone_extractor scans testbench.log once, matching regex patterns for
     each of the 9 stages (0-7) defined in flow.txt. Returns pass/fail/partial
     status for each stage.

  2. reset_detector scans testbench.log for reset markers (RST_TAG, CF9 writes,
     AGR/AWR/SWR events). Assembles events into ordered ResetCycle objects,
     classifying each by type (COLD/WARM/GLOBAL) and trigger source
     (OS_REBOOT, BIOS_INITIATED, SOLAR, AGR, AWR, SWR).

  3. summary_generator combines milestone and reset results into a structured
     text summary. Handles normal cold boot, single resets, and back-to-back
     resets. Writes to hsle_debug_agent_summary.txt in the run directory
     (falls back to current directory if write permission is denied).

Supported Scenarios:
  - Normal cold boot (no reset)
  - Single cold reset (OS-initiated or BIOS-initiated)
  - Single warm reset (OS reboot CF9=0x6, SOLAR, AWR, SWR)
  - Single global reset (SOLAR, AGR, CF9 with gbl_etr3=1)
  - Back-to-back resets (e.g., Cold then Global, Global then Cold)
  - BIOS-initiated resets (CF9 write during Stage 6 before OS boot)

Output:
  The summary file (hsle_debug_agent_summary.txt) includes:
  - Run metadata (directory, log size, analysis date)
  - Stage-by-stage checklist (Stages 0-7)
  - Reset cycle analysis (for each cycle: type, trigger, stage markers)
  - Failure analysis (failing stage, detail, recommendations)
  - Overall result (PASS/FAIL)
