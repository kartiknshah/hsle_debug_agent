# hsle-debug-agent

A GitHub Copilot custom agent (`@hsle_debug`) for debugging DMR MCP ICI HSLE (ZeBu ZSE5)
emulation run failures.

## What It Does

Given an HSLE run directory path, the agent:
- Locates `testbench.log` (plain or gzipped)
- Extracts execution milestones for all 9 stages (STAGE 0–8)
- Identifies the exact stage where the run failed
- Matches failure signatures against a known-issue catalog
- Produces a structured **HSLE Run Debug Summary** with root cause and recommendations

## Requirements

- VS Code with **GitHub Copilot Chat** extension (v0.26+)
- Read access to the HSLE run directory on NFS
- No additional Python packages or tools required

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

## Example Output

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
  Stage 7 (OS boot)           : *** FAIL — kernel hung in zone DMA32 init ***
  Stage 8 (Test termination)  : NOT REACHED

Last Activity:
  Last console output : line 472,831 @ 23:08:16
  Silence gap         : ~34 minutes before bootstrap timeout

Failure Signature : mem=\r\n (empty mem= in kernel cmdline)
Root Cause        : Kernel command line ends with "mem=" and no value. Kernel
                    calls free_area_init with an invalid memory map and hangs.
Recommendations   : Set mem=2G in GRUB kernel args, or reduce DIMM count in vp.simics
```

## Repo Layout

```
.github/
  copilot-instructions.md              # Workspace-level Copilot context
  agents/
    hsle_debug_agent.agent.md          # Agent definition, routing, guardrails
  skills/
    hsle-run-debugger/
      SKILL.md                         # 6-step debug procedure
      flow.txt                         # Golden execution flow (STAGE 0–8, ~740 lines)
README.md
```

## Platform Details

| Item | Value |
|------|-------|
| Target platform | DMR (Diamond Rapids) MCP ICI 1-Socket |
| Emulator | Synopsys ZeBu ZSE5 |
| Simics version | 6.0.256 |
| SPARK version | 1.12.11 |
| Dies | 2 IMH (imh8+imh9) + 4 CBB |
| Reference run | `mcp_ici_hsle_svos_fmod.0` (26ww12_2) |

## Extending the Agent

To add a new failure signature:
1. Open `.github/skills/hsle-run-debugger/SKILL.md`
2. Add a row to the **Step 5 Known Failure Signatures** table with:
   - Signature (log text pattern)
   - Stage number
   - Root cause description
   - Fix / action

To update the golden flow (e.g., after a Simics or SPARK version change):
1. Replace `.github/skills/hsle-run-debugger/flow.txt` with the updated flow document
2. Update the Stage checklist in `SKILL.md` if new milestone markers were added
