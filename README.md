# hsle-debug-agent

A GitHub Copilot custom agent (`@hsle_debug`) for debugging DMR MCP ICI HSLE (ZeBu ZSE5)
emulation run failures. Supports normal cold boot and reset scenarios (cold/warm/global
resets, including back-to-back resets).

## What It Does

Given an HSLE run directory path, the agent:

1. Locates `testbench.log` (plain or gzipped)
2. Runs stage-by-stage milestone checks against the golden flow (Stages 0-8)
3. Detects reset scenarios and classifies the reset type (cold/warm/global)
4. For reset runs, analyzes the reset-specific stages (Stages 8-13)
5. For back-to-back resets, analyzes each cycle sequentially
6. Identifies the exact failing stage and matches against known failure signatures
7. Decodes BIOS error codes (EWL, IPSD, RC Fatal, assertions, POST codes) for Stage 6/7 failures
8. Writes a structured debug summary to `result/<run_name>_hsle_debug_agent_summary.txt`

## Requirements

- VS Code with **GitHub Copilot Chat** extension (v0.26+)
- Read access to the HSLE run directory on NFS
- Python 3 (for BIOS error code decoding scripts)

## Setup

1. Clone or copy this repo to your machine
2. Open the repo root as a VS Code workspace: `code /path/to/hsle_debug_agent`
3. The `@hsle_debug` agent will be available in Copilot Chat automatically

## Usage

### Debug a run (normal cold boot or reset):
```
@hsle_debug debug run at /nfs/site/disks/.../crt/40584/mcp_ici_hsle_centos.0
@hsle_debug why did this HSLE run hang? /nfs/.../mcp_ici_hsle_svos.0
@hsle_debug /nfs/site/disks/.../max_base_template.32.11
```

### Standalone BIOS error decoding:
```
@hsle_debug what does EWL 0x0A/0x05 mean?
@hsle_debug decode post code 0x73
@hsle_debug analyze BIOS errors from /nfs/.../uart.log
```

### Output
The agent writes analysis to `result/<run_name>_hsle_debug_agent_summary.txt`.
The summary is **not** displayed in the chat window -- only the file path is confirmed.

## Repo Structure

```
.github/
  agents/
    hsle_debug_agent.agent.md            # Agent definition, workflow, guardrails
  copilot-instructions.md                # Workspace instructions
  skills/
    hsle-run-debugger/
      SKILL.md                           # 6-step debug procedure
      flow.txt                           # Golden flow (STAGE 0-8, first cold boot)
      bios_flow.txt                      # BIOS sub-phases (6.0-6.6)
      reset_phase_flow.txt               # RTL reset sub-events (Stage 5, 3 streams)
      cold_reset_flow.txt                # Cold reset flow (Stages 8-13)
      warm_reset_flow.txt                # Warm reset flow (Stages 8-13)
      global_reset_flow.txt              # Global reset flow (Stages 8-13)
      templates/
        summary_cold_boot.txt            # Output template: normal cold boot
        summary_reset_scenario.txt       # Output template: reset scenario
    bios-issue-analyzer/
      SKILL.md                           # BIOS error decoder skill
      scripts/                           # Decoder scripts + databases
      references/                        # BIOS assertion reference
README.md                                # This file
.gitignore
```

## Reset Scenario Support

| Reset Type | Flow File | Trigger | Key Behavior |
|------------|-----------|---------|--------------|
| Cold | `cold_reset_flow.txt` | CF9=0xE, SOLAR cold | Full power cycle, fuse reload |
| Warm | `warm_reset_flow.txt` | CF9=0x6, AWR, SWR | No PWRGOOD cycle, no fuse reload, RESET_N only |
| Global | `global_reset_flow.txt` | gbl_etr3=1, ACPI global | GBL_RST_WARN, IceCode reload, Fake GO RSP W/A |

Back-to-back resets (e.g., Global Reset followed by Cold Reset) are supported.

## Debug Flow

```
User provides run path
        |
        v
  Phase 1: Normal Cold Boot (Stages 0-8)
  Load flow.txt, run stage-by-stage checks
        |
        v
  All Stages 0-7 pass?
   /            \
  NO             YES
  |               |
  v               v
Identify      Phase 2: Reset Detection
failing       Check for RST_TAG markers
stage         Classify reset type
  |               |
  v               v
Drill down    Load reset flow file
Match sigs    Analyze Stages 8-13
  |           (repeat for back-to-back)
  v               |
Phase 3: Write Summary
Select template (cold boot / reset)
Fill in analysis results
Write to result/<run_name>_hsle_debug_agent_summary.txt
```

## Glossary

| Term | Meaning |
|------|---------|
| DMR | Diamond Rapids -- Intel server CPU program |
| MCP | Multi-Chip Package |
| ICI | Inter-Chip Interconnect |
| HSLE | Hybrid System Level Emulation -- Simics VP cores + ZeBu RTL uncore |
| ZeBu ZSE5 | Synopsys ZeBu Server 5 emulation hardware |
| IMH | I/O and Memory Hub die (2 per socket: imh8, imh9) |
| CBB | Compute Building Block die (4 per socket) |
| ACED | Test pass exit code (EBX = 0xACED) |
| CF9 | I/O port 0xCF9 -- reset control register (0xE=cold, 0x6=warm) |
| RST_TAG | Log tag prefix for all reset-related events in testbench.log |
| PPR_TEST_DONE | Pass marker for SVOS/CentOS PPR test runs |
