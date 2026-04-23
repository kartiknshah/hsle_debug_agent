# hsle-debug-agent

A GitHub Copilot custom agent (`@hsle_debug`) for debugging DMR MCP ICI HSLE (ZeBu ZSE5)
emulation run failures.

## What It Does

Given an HSLE run directory path, the agent:

- Locates `testbench.log` (plain or gzipped)
- Extracts execution milestones for all 9 stages (STAGE 0--8)
- Identifies the exact stage where the run failed
- For Stage 6 (BIOS boot) failures: pinpoints the exact BIOS sub-phase (6.0 SEC through 6.6 ExitBootServices) using both serconsole output and debug_port POST code stream
- Decodes BIOS error codes (EWL, IPSD, RC Fatal, assertions, POST codes) automatically when Stage 6/7 fails
- Matches failure signatures against a known-issue catalog
- Produces a structured **HSLE Run Debug Summary** with root cause and recommendations

## Requirements

- VS Code with **GitHub Copilot Chat** extension (v0.26+)
- Read access to the HSLE run directory on NFS
- Python 3 (for BIOS error code decoding scripts)

## Setup

1. Clone or copy this repo to your machine
2. Open the repo root as a VS Code workspace: `code /path/to/hsle_debug_agent`
3. The `@hsle_debug` agent will be available in Copilot Chat automatically

## Usage

```
@hsle_debug debug run at /nfs/site/disks/.../crt/40584/mcp_ici_hsle_centos.0
@hsle_debug why did this HSLE run hang? /nfs/.../mcp_ici_hsle_svos.0
@hsle_debug analyze testbench.log at /nfs/.../mcp_ici_hsle_centos.0
```

A bare path ending in `.0` is also accepted:
```
@hsle_debug /nfs/site/disks/.../crt/40584/mcp_ici_hsle_centos.0
```

Standalone BIOS error decoding (no run path needed):
```
@hsle_debug what does EWL 0x0A/0x05 mean?
@hsle_debug decode post code 0x73
@hsle_debug analyze BIOS errors from /nfs/.../uart.log
```

## Example Output

### Stage 7 (OS boot) failure:
```
HSLE Run Debug Summary
======================
Run Path    : /nfs/.../crt/40584/mcp_ici_hsle_centos.0
Test Name   : mcp_ici_hsle_centos
OS Image    : CentOS 6.18.0-dmr.bkc
Result      : TIMEOUT (no results.log)

Stage Progress:
  Stage 0 (Bootstrap)         : PASS
  Stage 1 (sle.simics setup)  : PASS
  Stage 2 (VP platform)       : PASS
  Stage 3 (HSLE core setup)   : PASS
  Stage 4 (CBB reset)         : PASS
  Stage 5 (Reset phases)      : PASS (all 6 phases completed)
  Stage 6 (BIOS boot)         : PASS (ExitBootServices reached)
  Stage 7 (OS boot)           : *** FAIL -- kernel hung in zone DMA32 init ***
  Stage 8 (Test termination)  : NOT REACHED

Last Activity:
  Last console output : line 472,831 @ 23:08:16
  Last debug_port PC  : 0x0058 -> 6.6 ExitBootServices entered
  Silence gap         : ~34 minutes before bootstrap timeout

Failure Signature : mem=\r\n (empty mem= in kernel cmdline)
Root Cause        : Kernel command line ends with "mem=" and no value.
Recommendations   : Set mem=2G in GRUB kernel args, or reduce DIMM count in vp.simics
```

### Stage 6 (BIOS / MRC) failure:
```
Stage 6 (BIOS boot)         : *** FAIL -- sub-phase 6.2 MRC ***

Last Activity:
  Last console output : line 398,210 -- START_MRC_RUN present, JEDEC_DATA absent
  Last debug_port PC  : 0x00e0 -> 6.2 FSP-M/MRC entry (DIMM detect never completed)

Failure Signature : START_MRC_RUN present but JEDEC_DATA absent
Root Cause        : DIMM detection failure -- DDR5 SPD read error

BIOS Issue Analysis Summary
===========================
EWL errors      : 2 unique codes (5 occurrences)
Top Errors:
  #1  0x08/0x11 -- WARN_MEMORY_TRAINING_DIMM_FAILURE -- 3x
      Desc: DDR5 RDIMM slot failed during SPD initialization
      HW:   Socket 0, Channel 2, DIMM 0
```

## Repo Layout

```
.github/
  copilot-instructions.md              # Workspace-level Copilot context
  agents/
    hsle_debug_agent.agent.md          # Agent definition, routing, guardrails
  skills/
    hsle-run-debugger/
      SKILL.md                         # 6-step debug procedure (loaded by agent)
      flow.txt                         # Golden execution flow (STAGE 0-8)
      bios_flow.txt                    # BIOS boot sub-phase flow (Stage 6.0-6.6)
    bios-issue-analyzer/
      SKILL.md                         # BIOS error decoder skill
      scripts/
        decode_ewl.py                  # EWL + IPSD + RC Fatal log parser
        decode_post_code.py            # BIOS POST code / ACPI debug code decoder
        decoder_utils.py               # Shared JSON loader / hex normalizer
        main.py                        # CLI entry for EWL decoder
        ewl_codes_database.json        # 124 EWL major + 412 minor codes
        rc_fatal_errors_database.json  # 49 RC Fatal major + 449 minor codes
        ipsd_codes_database.json       # 23 IPSD error codes
        post_codes_database.json       # DMR BIOS POST + ACPI debug codes
      references/
        assertion-reference.md         # EFI_STATUS string -> failure category map
README.md
```

## Skills

| Skill | Purpose |
|-------|---------|
| `hsle-run-debugger` | 9-stage golden flow comparison, milestone grep, Stage 6 sub-phase checklist, failure signature matching |
| `bios-issue-analyzer` | Decodes EWL/IPSD/RC Fatal/ASSERT/POST codes from serconsole output or raw BIOS serial logs |

## How It Works

When you type `@hsle_debug debug run at /nfs/...`, the agent:

1. Loads `SKILL.md` (the step-by-step debug procedure)
2. Reads `flow.txt` (the 9-stage golden execution flow) and `bios_flow.txt` (Stage 6 BIOS sub-phase detail)
3. Locates and validates `testbench.log` in the provided run directory
4. Runs stage-by-stage milestone grep checks (Stages 0-8)
5. For Stage 6: runs the BIOS sub-phase checklist (6.0 SEC -> 6.6 ExitBootServices) using both serconsole and debug_port POST code streams
6. Drills into the failure zone and matches against the known signature catalog
7. If Stage 6/7 failed: loads `bios-issue-analyzer` to decode EWL/IPSD/RC Fatal/ASSERT/POST codes
8. Produces a structured **HSLE Run Debug Summary**

## Stage 6 BIOS Sub-Phase Analysis

The `bios_flow.txt` reference enables granular diagnosis of BIOS boot failures by tracking
two parallel log streams in `testbench.log`:

| Sub-stage | Phase | Milestone |
|-----------|-------|---------|
| 6.0 | SEC (silent) | debug_port 0x0001-0x007f |
| 6.1 | Early PEI pre-memory | `EarlyPlatformPchInit`, `BIOS ID:` |
| 6.2 | FSP-M / MRC (DDR5 training) | `START_MRC_RUN`, `JEDEC_DATA` x16, `PeiInstallPeiMemory` |
| 6.3 | Post-memory PEI | `CEDT ACPI Table`, `DXE IPL Entry` |
| 6.4 | DXE phase | `Loading DXE CORE`, `NvmExpressDriverBindingStart` |
| 6.5 | BDS / boot selection | `[Bds]Booting`, `Valid efi partition table` |
| 6.6 | ExitBootServices | `Decompressing Linux`, `Linux version` |

The debug summary reports the **exact sub-stage** and **last debug_port POST code** at the
point of failure, enabling fast triage without reading thousands of log lines manually.

## Platform Details

| Item | Value |
|------|-------|
| Target platform | DMR (Diamond Rapids) MCP ICI 1-Socket |
| Emulator | Synopsys ZeBu ZSE5 |
| Simics version | 6.0.256 |
| SPARK version | 1.12.11 |
| Dies | 2 IMH (imh8+imh9) + 4 CBB |
| Reference run | `mcp_ici_hsle_svos_fmod.0` (26ww12_2) |
| Reference BIOS | OKSDCRB1.86B.0032.D77.2602232255 (DEBUG, OakStreamRp) |
| DDR config | 16x DDR5 RDIMM (8 per IMH die), DDR5-4000 |

## Extending the Agent

### Add a new failure signature
1. Open `.github/skills/hsle-run-debugger/SKILL.md`
2. Add a row to the **Step 5 Known Failure Signatures** table with:
   - Signature (log text pattern)
   - Stage / sub-stage number (e.g., `6.2` for MRC failures)
   - Root cause description
   - Fix / action

### Update the golden execution flow
1. Replace `.github/skills/hsle-run-debugger/flow.txt` with the updated flow document
2. Update the Stage checklist in `SKILL.md` if new milestone markers were added

### Update the BIOS sub-phase reference
1. Replace `.github/skills/hsle-run-debugger/bios_flow.txt` with the updated BIOS flow
2. Update the Stage 6 sub-phase checklist table in `SKILL.md` to reflect new milestones
3. Update the debug_port POST code decode map in Step 4 if new code ranges are added

### Add or update BIOS error codes
1. Edit the relevant JSON database in `.github/skills/bios-issue-analyzer/scripts/`:
   - `ewl_codes_database.json` -- EWL major/minor codes
   - `rc_fatal_errors_database.json` -- RC Fatal codes
   - `ipsd_codes_database.json` -- IPSD codes
   - `post_codes_database.json` -- BIOS POST / ACPI debug codes
2. The decoder scripts pick up changes automatically (no code changes needed)

### Add a new BIOS assertion category
1. Open `.github/skills/bios-issue-analyzer/references/assertion-reference.md`
2. Add a mapping row: `EFI_STATUS string` -> `failure category` -> `recommended action`
