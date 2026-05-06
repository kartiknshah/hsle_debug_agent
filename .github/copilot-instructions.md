# hsle-debug-agent — Workspace Instructions

This repo provides the **hsle_debug** GitHub Copilot custom agent for debugging
DMR MCP ICI HSLE (ZeBu ZSE5) emulation runs.

## Repo Layout

```
.github/
  agents/
    hsle_debug_agent.agent.md          # Agent definition, workflow, and guardrails
  skills/
    hsle-run-debugger/
      SKILL.md                         # 6-step debug procedure (loaded by agent)
      flow.txt                         # Golden execution flow reference (STAGE 0-8)
      bios_flow.txt                    # BIOS boot sub-phase flow reference (Stage 6.0-6.6)
      reset_phase_flow.txt             # RTL reset sub-event reference (Stage 5, 3 streams)
      cold_reset_flow.txt              # Cold reset flow reference (Stages 8-13)
      warm_reset_flow.txt              # Warm reset flow reference (Stages 8-13, warm-specific)
      global_reset_flow.txt            # Global reset flow reference (Stages 8-13, global-specific)
      templates/
        summary_cold_boot.txt          # Output template for normal cold boot runs
        summary_reset_scenario.txt     # Output template for reset scenario runs
    bios-issue-analyzer/
      SKILL.md                         # BIOS error decoder skill (EWL/IPSD/RC Fatal/Assert/POST)
      scripts/
        decode_ewl.py                  # EWL + IPSD + RC Fatal log parser & decoder
        decode_post_code.py            # BIOS POST code / ACPI debug code decoder
        decoder_utils.py               # Shared JSON loader / hex normalizer
        main.py                        # CLI entry for EWL decoder
        ewl_codes_database.json        # 124 EWL major + 412 minor codes
        rc_fatal_errors_database.json  # 49 RC Fatal major + 449 minor codes
        ipsd_codes_database.json       # 23 IPSD error codes
        post_codes_database.json       # DMR BIOS POST + ACPI debug codes
      references/
        assertion-reference.md         # EFI_STATUS string -> failure category mapping
```

## Key Agent

| Agent | Trigger | Purpose |
|-------|---------|---------|
| `hsle_debug` | `@hsle_debug` | Debug HSLE run failures — analyzes `testbench.log`, identifies failing stage, matches known signatures, writes structured debug summary into the repo-local `result/` directory. Supports normal cold boot and reset scenarios (cold/warm/global, including back-to-back resets). |

## Skills

| Skill | Trigger | Purpose |
|-------|---------|---------|
| `hsle-run-debugger` | Stage-by-stage HSLE run analysis | 9-stage golden flow comparison, reset detection, milestone grep, failure signature matching |
| `bios-issue-analyzer` | Stage 6/7 BIOS failure, or standalone BIOS log analysis | Decodes EWL/IPSD/RC Fatal/ASSERT/POST codes from serconsole output or raw BIOS logs |

## How It Works

When you type `@hsle_debug debug run at /nfs/...`, the agent:
1. Loads `SKILL.md` (the step-by-step debug procedure)
2. Reads `flow.txt` (the 9-stage golden execution flow)
3. Locates and validates `testbench.log` in the provided run directory
4. Runs stage-by-stage milestone grep checks (Stages 0-7)
5. Detects reset scenarios and loads the appropriate reset flow file
   (`cold_reset_flow.txt`, `warm_reset_flow.txt`, or `global_reset_flow.txt`)
6. For back-to-back resets, analyzes each cycle sequentially
7. Drills into the failure zone and matches against the known signature catalog
8. Writes a structured **HSLE Run Debug Summary** to `result/<run_name>_hsle_debug_agent_summary.txt`

## Reset Scenario Support

The agent supports three reset types, each with a dedicated flow reference:

| Reset Type | Flow File | Trigger | Key Differences |
|------------|-----------|---------|-----------------|
| Cold | `cold_reset_flow.txt` | CF9=0xE, SOLAR cold | Full power cycle, fuse reload |
| Warm | `warm_reset_flow.txt` | CF9=0x6, AWR, SWR | No PWRGOOD cycle, no fuse reload, RESET_N only |
| Global | `global_reset_flow.txt` | gbl_etr3=1, ACPI global | GBL_RST_WARN, IceCode reload, Fake GO RSP W/A |

Back-to-back resets (e.g., Global Reset -> Cold Reset) are supported by analyzing
each cycle's Stages 8-13 sequentially.

## Output

The agent writes its analysis to `result/<run_name>_hsle_debug_agent_summary.txt`.
Two templates are used:
- **Normal cold boot**: `templates/summary_cold_boot.txt` — Stages 0-8
- **Reset scenario**: `templates/summary_reset_scenario.txt` — Stages 0-8 + reset Stages 8-13

## Temporary Artifacts

Any temporary or scratch artifacts created by the agent while debugging must be
written under the repo-local `result/` directory, preferably `result/tmp/`.
Do not create temporary Python scripts, logs, or intermediate files under `/tmp`
or outside the repository.

## Glossary

| Term | Meaning |
|------|---------|
| DMR | Diamond Rapids — Intel server CPU program |
| MCP | Multi-Chip Package |
| ICI | Inter-Chip Interconnect |
| HSLE | Hybrid System Level Emulation — Simics VP cores + ZeBu RTL uncore |
| ZeBu ZSE5 | Synopsys ZeBu Server 5 emulation hardware |
| SPARK | Test framework that bootstraps HSLE runs |
| IMH | I/O and Memory Hub die (2 per socket: imh8, imh9) |
| CBB | Compute Building Block die (4 per socket) |
| UCIe | Universal Chiplet Interconnect Express — D2D link between IMH<->CBB |
| fmod | Functional Model Override — replaces RTL firmware with Simics model for faster boot |
| IDI | In-Die Interconnect — bridges VP x86 cores to RTL uncore |
| RESET_PHASE | RTL hardware reset sequencing phases 1-6 |
| ACED | Test pass exit code (EBX = 0xACED) |
| HANG | Bootstrap timeout — run did not complete within cycle limit |
| DEAD | Fatal hardware error detected during run |
| Hybrid Switch | The moment at RESET_PHASE_6 when VP cores take over from RTL for instruction execution |
| CF9 | I/O port 0xCF9 — reset control register (0xE=cold, 0x6=warm) |
| gbl_etr3 | Global ETR3 register — when set, CF9 write triggers global reset |
| GBL_RST_WARN | Global Reset Warning signal — used in global reset sequencing |
| RST_TAG | Log tag prefix for all reset-related events in testbench.log |
