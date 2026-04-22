# hsle-debug-agent — Workspace Instructions

This repo provides the **hsle_debug** GitHub Copilot custom agent for debugging
DMR MCP ICI HSLE (ZeBu ZSE5) emulation runs.

## Repo Layout

```
.github/
  agents/
    hsle_debug_agent.agent.md          # Agent definition and guardrails
  skills/
    hsle-run-debugger/
      SKILL.md                         # 6-step debug procedure (loaded by agent)
      flow.txt                         # Golden execution flow reference (STAGE 0–8)
```

## Key Agent

| Agent | Trigger | Purpose |
|-------|---------|---------|
| `hsle_debug` | `@hsle_debug` | Debug HSLE run failures — analyzes `testbench.log`, identifies failing stage, matches known signatures, produces structured debug summary |

## How It Works

When you type `@hsle_debug debug run at /nfs/...`, the agent:
1. Loads `SKILL.md` (the step-by-step debug procedure)
2. Reads `flow.txt` (the 9-stage golden execution flow)
3. Locates and validates `testbench.log` in the provided run directory
4. Runs stage-by-stage milestone grep checks
5. Drills into the failure zone and matches against the known signature catalog
6. Produces a structured **HSLE Run Debug Summary**

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
| UCIe | Universal Chiplet Interconnect Express — D2D link between IMH↔CBB |
| fmod | Functional Model Override — replaces RTL firmware with Simics model for faster boot |
| IDI | In-Die Interconnect — bridges VP x86 cores to RTL uncore |
| RESET_PHASE | RTL hardware reset sequencing phases 1–6 |
| ACED | Test pass exit code (EBX = 0xACED) |
| HANG | Bootstrap timeout — run did not complete within cycle limit |
| DEAD | Fatal hardware error detected during run |
| Hybrid Switch | The moment at RESET_PHASE_6 when VP cores take over from RTL for instruction execution |
