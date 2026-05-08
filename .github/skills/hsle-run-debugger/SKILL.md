---
name: hsle-run-debugger
description: Debug DMR HSLE emulation runs (MCP and IMH models) by analyzing testbench.log against the known-good execution flow. Identifies which stage failed, extracts failure signatures, and provides root-cause triage with debug recommendations. Supports MCP ICI (ZSE5) and IMH (ZSE4) model variants. Use when a user provides an HSLE run directory path and asks to debug, diagnose, or analyze a failing or hanging HSLE run.
---

# HSLE Run Debugger

**Purpose**: Diagnose failures in DMR HSLE (ZeBu) emulation runs by systematically
comparing `testbench.log` milestones against the golden execution flow.

**Supported models**:
- **MCP** (`mcp_1s_ici`): ZSE5 — uses `flow.txt`
- **IMH** (`1imh_1s_4cbb`, `2imh_1s_4cbb`, etc.): ZSE4 — uses `flow_imh.txt`

## When to Use

Load this skill when:
- The user provides an HSLE run directory path and asks to debug, diagnose, or triage the run
- The user reports an HSLE run that hung, timed out, failed, or produced no `results.log`
- The user asks why an HSLE run did not reach ACED / OS boot / BIOS completion
- The user wants a quick health check of an HSLE run

---

## Input

The user provides a **run directory path**, e.g.:
```
/nfs/site/disks/.../crt/<id>/<testname>.0
```

The primary artifact is `testbench.log` (or `testbench.log.gz`) inside that directory.

---

## Model Detection (MUST DO FIRST)

Before analyzing milestones, determine which model the run uses:

```bash
# Check emurun.dut_cfg for model_name
grep "model_name\|model_build_cfg" <run_dir>/test/emurun.dut_cfg 2>/dev/null \
  || zgrep "model_name\|model_build_cfg" <run_dir>/emurun.dut_cfg.gz 2>/dev/null

# Fallback: check determine_model.py output in testbench.log
grep "determine_model.py.*Read the model" <run_dir>/testbench.log | head -2
```

**Classification rules:**
| model_name | Model family | Golden flow | ZeBu gen |
|---|---|---|---|
| `mcp_1s_ici` | MCP | `flow.txt` | ZSE5 |
| `*imh_*` (any IMH variant) | IMH | `flow_imh.txt` | ZSE4 |

If model_build_cfg contains `"IMH"` → IMH model; if it contains `"MCP"` → MCP model.

> **CRITICAL**: "IMH2" in path/folder/script names (e.g., `IMH2_M4C_SideModel`,
> `imh2_svos_post.simics`, `26ww6.5_IMH2_4CBB`) means the **IMH Gen2 silicon
> variant** — it does NOT describe die topology. Do not try to determine die
> count from path names, script names, or folder names. The only thing the agent
> needs to determine is **MCP vs IMH** to select the correct flow file.

If model_build_cfg contains `"IMH"` → IMH model; if it contains `"MCP"` → MCP model.

---

## Golden Flow Reference

**For MCP runs**, the golden execution flow is documented in:
```
.github/skills/hsle-run-debugger/flow.txt
```

**For IMH runs**, use:
```
.github/skills/hsle-run-debugger/flow_imh.txt
```

Both define **9 stages** (STAGE 0–8) of the HSLE execution, milestone markers for each
stage, and the expected chronological order. **Read the appropriate file at the start of
every debug session** to have the full reference before running any greps.


For Stage 6 (BIOS boot) sub-phase analysis, additionally read:
```
.github/skills/hsle-run-debugger/bios_flow.txt
```

This file documents **7 BIOS sub-stages** (6.0--6.6) with serconsole + debug_port POST code
milestones for each:

| Sub-stage | Phase | Key marker |
|-----------|-------|-----------|
| 6.0 | SEC | debug_port 0x0001--0x007f (no serconsole) |
| 6.1 | Early PEI pre-memory | `EarlyPlatformPchInit`, `BIOS ID:` |
| 6.2 | FSP-M / MRC (DDR5 training) | `START_MRC_RUN`, `PeiInstallPeiMemory` |
| 6.3 | Post-memory PEI | `CEDT ACPI Table`, `DXE IPL Entry` |
| 6.4 | DXE phase | `Loading DXE CORE`, `NvmExpressDriverBindingStart` |
| 6.5 | BDS / boot device selection | `[Bds]Booting`, `Valid efi partition table` |
| 6.6 | ExitBootServices -> OS handoff | `Decompressing Linux`, `Linux version` |

**Read `bios_flow.txt` whenever Stage 6 is the failing stage**, before drilling down, to
pinpoint the exact sub-phase where BIOS stopped.

For Stage 5 (RTL reset phases) sub-event analysis, additionally read:
```
.github/skills/hsle-run-debugger/reset_phase_flow.txt       (MCP — 3 streams: BOOT_FSM + HWRS + Primecode)
.github/skills/hsle-run-debugger/reset_phase_flow_imh.txt   (IMH — 2 streams: HWRS + Primecode only)
```

**For MCP**, this file documents **three parallel log streams** inside `testbench.log` during Stage 5:

| Stream | Log prefix | Symmetry rule |
|--------|-----------|---------------|
| CBB BOOT_FSM / Phase markers | `Inform COLD\|WARM` | N/A -- single sequencer |
| HWRS events | `sequencer_log: HWRS -` | Both **imh0 AND imh1** must emit each event |
| IMH Primecode states | `imh primecode state` | Both **die8 AND die9** must complete each state |

**For IMH**, this file documents **two parallel log streams** (NO BOOT_FSM):

| Stream | Log prefix | Symmetry rule |
|--------|-----------|---------------|
| HWRS events | `sequencer_log: HWRS -` | 1imh: single stream; 2imh: imh0 + imh1 |
| IMH Primecode states | `imh primecode state` | 1imh: single stream ("socket 0"); 2imh: both |

**Read the appropriate `reset_phase_flow*.txt` whenever Stage 5 is the failing stage**, to identify the exact
sub-event and die/IMH instance where the hang occurred.


For reset scenario analysis (Stage 8-13: reset trigger, hardware entry, second boot), additionally read:
```
.github/skills/hsle-run-debugger/cold_reset_flow.txt
```

This file documents **6 reset-specific stages** (8-13) covering all reset types (cold/warm/global, reboot/solar, AGR/AWR/SWR):

| Stage | Phase | Key marker |
|-------|-------|------------|
| 8 | Reset trigger (post-first-boot) | `RST_TAG: triggering`, `COLD_RESET through OS reboot triggered` |
| 9 | Reset hardware entry | `HSLE starting reset procedures`, `BEGIN_RESET_FLOW` |
| 10 | Second boot RTL phases | `BOOT_FSM state 0x01`, `RESET_PHASE_6`, `BIOS first fetch` |
| 11 | Second BIOS boot | `IDI Mux enabled`, `BIOS_TAIL_ACED_FFFFFF00` |
| 12 | Second OS boot | `PPR_TEST_DONE` (second occurrence) |
| 13 | Test termination | `RESET_TEST_COMPLETE`, `results.log` |

**Read `cold_reset_flow.txt` whenever a reset cycle is detected** (i.e., `RST_TAG: triggering` in testbench.log), to identify the exact failing stage in the reset or second boot sequence.

For warm reset scenario analysis specifically, additionally read:
```
.github/skills/hsle-run-debugger/warm_reset_flow.txt
```

This file documents the warm-reset-specific hardware entry sequence (Stage 9 differences):
no PWRGOOD de-assertion, no fuse reload, no SLP signals, RESET_N asserted directly.
Use when the reset type is WARM (CF9=0x6, AWR, or SWR) to verify correct warm-specific
behavior and detect cold/warm misclassification errors.


For global reset scenario analysis specifically, additionally read:
```
.github/skills/hsle-run-debugger/global_reset_flow.txt
```

This file documents the global-reset-specific hardware entry sequence (Stage 9 differences):
GBL_RST_WARN wait (not PLTRST_SYNC), GLOBAL_RESET_N assertion, full PWRGOOD de-assertion,
SLP_S5/S4/S3 assertion, fuse + IceCode reload, and Fake GO RSP W/A activation.
Use when the reset type is GLOBAL (CF9=0x06 + gbl_etr3=1, or ACPI global reset)
to verify correct global-specific behavior.

---

## Standalone Python Scripts

The debug analysis is also available as standalone Python scripts in:
```
.github/skills/hsle-run-debugger/scripts/
```

These scripts implement the same logic as the agent's step-by-step procedure and
can be run directly from the command line:

```bash
# Full analysis (milestone extraction + reset detection + summary generation)
python3 .github/skills/hsle-run-debugger/scripts/main.py /path/to/hsle_run.0

# With output path override
python3 .github/skills/hsle-run-debugger/scripts/main.py /path/to/run --output ./result/custom_summary.txt

# Verbose mode (print milestone details to stdout)
python3 .github/skills/hsle-run-debugger/scripts/main.py /path/to/run --verbose
```

Modules:
- `main.py` — CLI entry point; orchestrates milestone extraction → reset detection → summary
- `milestone_extractor.py` — Stage 0-7 milestone extraction from testbench.log
- `reset_detector.py` — Reset cycle detection and classification (cold/warm/global, OS/BIOS/platform)
- `summary_generator.py` — Structured debug summary file generation

Requirements: Python 3.8+, no external dependencies.

---

## Procedure

### Step 0 — Run the Automated Analyzer FIRST (Token-Efficient)

Before any manual grep, **always run the Python analyzer script first**:

```bash
python3 .github/skills/hsle-run-debugger/scripts/hsle_analyzer.py <run_dir> --summary
```

This produces:
- Console output: result, scenario type, reset cycles, PPR count
- File output: `result/<run_name>_hsle_debug_agent_summary.txt`

**Performance**: ~4-5 seconds for 400K-line logs (single-pass, keyword pre-filter).

**If the script identifies the failure clearly** (e.g., `FAIL@Stage11: BIOS first fetch wait
seen but IDI Mux never enabled`), use that result directly. Only proceed to manual
grep analysis (Steps 1-6) when:
- The script reports an unexpected result that needs verification
- The failure context needs more detail than the script provides
- The script encounters an error or unrecognized pattern

---

### Step 1 — Locate and Validate testbench.log

```bash
# Check if testbench.log exists (plain or gzipped)
ls <run_dir>/testbench.log*

# Check line count to assess log completeness
wc -l <run_dir>/testbench.log
# or for gzipped: zcat <run_dir>/testbench.log.gz | wc -l

# Check for results.log — indicates run reached an ACED/DEAD/SHUTDOWN exit handler
cat <run_dir>/test/results.log 2>/dev/null || echo "NO results.log"

# Check for PPR_TEST_DONE — the pass marker for SVOS/CentOS PPR test runs
# PPR_TEST_DONE appears in the emu.devices log stream (NOT serconsole)
grep "PPR_TEST_DONE" <run_dir>/testbench.log | head -1
# or for gzipped: zgrep "PPR_TEST_DONE" <run_dir>/testbench.log.gz | head -1
```


```bash
# Detect reset runs: non-zero count = run includes post-boot reset cycles
grep -c "RST_TAG HSLE starting reset procedures" <run_dir>/testbench.log
```

If resets are present, also load `the appropriate reset flow file (cold_reset_flow.txt / warm_reset_flow.txt / global_reset_flow.txt)` alongside this skill for Stage 8.5 guidance.

If testbench.log is gzipped, use `zgrep` and `zcat` instead of `grep` and `cat` in all
subsequent steps.

**Expected line counts** for a complete SVOS/CentOS boot run: 500K–700K lines.
- < 10K lines → failed during setup (Stage 0–1)
- 10K–300K lines → failed during ZeBu connect or pre-emulation (Stage 1)
- 300K–350K lines → failed during reset phases (Stage 5)
- 350K–650K lines → failed during BIOS/OS boot (Stage 6–7)
- 650K–700K lines → likely reached OS boot + PPR test execution (check PPR_TEST_DONE)

> **IMPORTANT**: Missing `results.log` does NOT always mean failure. For SVOS and CentOS
> PPR test runs, the pass condition is `PPR_TEST_DONE` appearing in the `emu.devices` log
> stream of `testbench.log`, followed by the "Auto exit script" trigger. These runs
> typically have `test_result: -1` and no `results.log` even when they PASS.

---

### Step 2 — Extract Stage Milestones

Run the master milestone grep. This single command captures markers for all 9 stages:

```bash
TBLOG="<run_dir>/testbench.log"

grep -n "RTI:\|RESET_PHASE\|hsle.simics\|IDI Mux\|Hybrid Core\|UCLK\|Waiting for RTL\|FW_BYPASS\|pdisable\|penable\|fuse_load\|primecode\.py\|mem_load\|Mounted\|Pre Mount\|sle.simics.*Project\|sle.simics.*determine_model\|sle.simics.*Running\|ACED\|DEAD\|HANG\|SHUTDOWN\|bootstrap_timeout\|reached cycle limit\|end_of_run\|Linux version\|Kernel command line\|ExitBootServices\|serconsole.*GRUB\|centos_post\|svos_post\|PPR_auto_exit\|PPR_TEST_DONE\|Auto exit script\|Error\|ERROR\|FATAL\|Exception\|Traceback\|quit.*Simics\|RST_TAG\|PPR check: GOT RESET\|Inform RST_TAG: Running" "$TBLOG" | head -150
```

---

### Step 3 — Stage-by-Stage Checklist

Compare extracted milestones against this checklist. For each stage, verify the **required
markers** appear. The first stage with missing markers is the failure point.

> **IMPORTANT -- BIOS-initiated reset exception**: If Stage 6 is PARTIAL (some sub-stage
> milestones present but not all) and Stage 7 is completely missing, check for reset markers
> BEFORE declaring Stage 6 as the failure point:
> ```bash
> grep -n "PPR check: GOT RESET CF9\|RST_TAG HSLE starting reset" > ```
> If reset markers exist with line numbers NEAR or AFTER the last Stage 6 milestone,
> BIOS triggered a reset during boot. This is EXPECTED behavior (e.g., MRC training
> cold reset, fuse mismatch). Mark Stage 6 as PARTIAL (not FAIL), Stage 7 as NOT REACHED,
> and proceed to Step 5b for reset cycle analysis. The partial Stage 6 + missing Stage 7
> is the entry to the reset flow, not a failure.

#### STAGE 0: SPARK Bootstrap
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Bootstrap start | `[bootstrap info] running` | SPARK framework started |
| sle.simics entry | `Running sle.simics setup_script` | Handoff to main script |

#### STAGE 1: sle.simics — Main Script Setup
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Script load | `[sle.simics] Project Simics script loading` | sle.simics executing |
| Model detection | `model we are running on is` or `Read the model from emurun.dut_cfg` | determine_model.py OK |
| RTI Pre Cycle 0 | `RTI: Pre Cycle 0` | Cycle tracking started |
| RTI Hit cycle_0 | `RTI: Hit cycle_0` | Engine initialized |
| Pre Mount | `RTI: Pre Mount` | About to connect to ZeBu |
| Mounted | `RTI: Mounted` | ZeBu PCIe connected |
| Fuse loading | `fuse_load.py.*STARTING` | Fuse images loading |
| Primecode loading | `primecode` (in emu.engine info) | Primecode images loading |
| S3M loading | `mem_load` or `load_s3m` | S3M firmware loading |

**Common Stage 1 failures**:
- Never reaches "Mounted" → ZeBu hardware connection issue (board allocation, PCIe timeout)
- Fuse load fails → Missing fuse image files in `test/fuse_image_imh8/` or `imh9/`
- Python exception during setup → Check for `Traceback` or `Exception` lines

#### STAGE 2: vp.simics — VP Platform Setup
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| VP script | `vp.simics` in early log | VP config running |
| Target load | `load_target` or `oakstream` | Platform model instantiated |
| UART capture | `serconsole.con.capture-start` | UART logging enabled |

**Common Stage 2 failures**:
- BIOS image not found → Check IFWI path resolution
- Disk image not found → Check `os_image` / `disk_image` parameter resolution

#### STAGE 3: hsle.simics — Hybrid Core Setup

**MCP markers:**
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Hybrid Xtor Setup | `[hsle.simics] Hybrid Xtor Setup` | IDI xtor configuration |
| FW BYPASS | `[hsle.simics]  FW BYPASS Override S3M` | S3M fmod bypass set |
| Waiting for RTL Core Reset | `[hsle.simics]  Waiting for RTL Core Reset` | Waiting for reset vector |
| UCLK Ungating | `[hsle.simics] UCLK Ungating fix` | Post-phase-6 UCLK fix |
| Waiting for IDI flush | `[hsle.simics] Waiting for IDI to flush` | 5M cycle IDI drain |
| Enabling Hybrid Cores | `[hsle.simics] Enabling Hybrid Core` | About to enable VP cores |
| IDI Mux enabled | `[hsle.simics] IDI Mux enabled` | **HYBRID SWITCH COMPLETE** |

**IMH markers** (different hybrid switch sequence — NO IDI Mux, NO UCLK ungating):
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| FW BYPASS | `[hsle.simics]  FW BYPASS Override S3M` | S3M fmod bypass set |
| Waiting for RTL Core Reset | `[hsle.simics]  Waiting for RTL Core Reset` | Waiting for reset vector |
| Reached end of phase 5 | `Reached end of phase 5` | RTL reset phases done |
| penable -all | `penable -all` | VP cores re-enabled |
| Enabling simics cores | `[hsle.simics] Enabling simics cores` | **HYBRID SWITCH COMPLETE** |

> **KEY DIFFERENCE**: MCP uses `IDI Mux enabled` as the hybrid switch completion marker.
> IMH uses `Enabling simics cores` (no IDI Mux exists in IMH topology).

**Common Stage 3 failures**:
- Stuck at "Waiting for RTL Core Reset" → RTL never reached reset vector; check reset phase progression (Stage 5)
- (MCP) IDI Mux never enabled → Hybrid switch failed; check IDI xtor credits, UCLK ungating
- (IMH) "Enabling simics cores" never appears → Phase 5 end not detected; check reset phases

#### STAGE 4: CBB Reset Flow (parallel branch)
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| CBB reset branch | `script-branch.*cbb_reset` or CBB-specific logs | CBB `sle.simics` running |

#### STAGE 5: RTL Reset Phases

**MCP markers:**
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Phase 1 start | `Start of RESET_PHASE_1` | S3M/IBL booting |
| Phase 2 end | `End of RESET_PHASE_2` | Early infra done |
| Phase 3 INFRA | `RESET_PHASE_3_INFRA is complete` | Infra init done (check both IMH dies) |
| Phase 3 D2D | `RESET_PHASE_3_D2D is complete` | UCIe D2D link trained |
| Phase 3 INFRA_CFG | `RESET_PHASE_3_INFRA_CFG is complete` | Infra config done |
| Phase 4 end | `End of RESET_PHASE_4` | Memory init done |
| Phase 5 end | `End of RESET_PHASE_5` | Late init done |
| Phase 6 start | `Start of RESET_PHASE_6` | Core release → triggers hybrid switch |

**IMH markers** (different phase end markers, NO BOOT_FSM stream):
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Phase 3 INFRA | `RESET_PHASE_3_INFRA is complete` | Infra init done |
| Phase 3 D2D | `RESET_PHASE_3_D2D is complete` | UCIe D2D link trained |
| Phase 3 INFRA_CFG | `RESET_PHASE_3_INFRA_CFG is complete` | Infra config done |
| Phase 4 end | `RESET_SEQ_MARK_PHASE4_COMPLETE` | Memory init done |
| Phase 5 end | `RESET_SEQ_PREPARE_FOR_LOOP` | Late init done → triggers hybrid switch |

> **KEY IMH DIFFERENCES for Stage 5:**
> - NO `Start of RESET_PHASE_1`, `End of RESET_PHASE_2`, `Start of RESET_PHASE_6` markers
> - Phase 4 end = `RESET_SEQ_MARK_PHASE4_COMPLETE` (not `End of RESET_PHASE_4`)
> - Phase 5 end = `RESET_SEQ_PREPARE_FOR_LOOP` (not `End of RESET_PHASE_5`)
> - NO BOOT_FSM stream — only 2 log streams (HWRS + Primecode)
> - For 1imh models: NO die symmetry check needed (single IMH die)
> - For 2imh models: symmetry check on imh0/imh1 (same concept as MCP)
> - Use `reset_phase_flow_imh.txt` instead of `reset_phase_flow.txt` for drill-down

**Common Stage 5 failures** (high-level — see `reset_phase_flow.txt` for detailed sub-event drill-down):
- Stuck before Phase 1 → S3M boot failure; check S3M fmod bypass, IBL loading
- Phase 2 never ends → PUnit primecode hang; check primecode image version
- Phase 3 INFRA incomplete → Infrastructure init stall; check both IMH dies (imh8 and imh9 must both report)
- Phase 3 D2D never completes → UCIe link train failure between IMH↔CBB; check UCIe ignite scripts
- Phase 4 hang → DDR memory training failure; check DFI xtors, DIMM config
- Phase 5 hang → Late coherency setup failure; check UPI/fabric init

> **Stage 5 Deep Dive**: When any Phase 3--5 milestone is missing, load the appropriate
> reset phase flow file (`reset_phase_flow.txt` for MCP, `reset_phase_flow_imh.txt` for IMH)
> and run the stream drill-down (Step 4 below). Key checks:
> - **BOOT_FSM last sub-event** (MCP only) -- pinpoints exactly where phase1 stalled within S3M boot
> - **HWRS symmetry** (MCP: imh0/imh1; 2imh: imh0/imh1; 1imh: N/A) --
>   `grep "sequencer_log: HWRS" | grep -oP "imh\\s*\\d+" | sort | uniq -c`
>   Unequal counts = one IMH die stalled
> - **Primecode symmetry** (MCP: die8/die9; 2imh: check both; 1imh: single stream) --
>   `grep "imh primecode state" | grep -oP "die\\d+" | sort | uniq -c`
>   Unequal counts = one IMH die stalled; last state = stall point
>
> **Warm Reset (SWR)**: If run includes a warm reset cycle, `Inform WARM` lines appear in the
> BOOT_FSM stream. Warm path omits `BOOT_FSM_DFX_AGG_FUSE_PULL` and requires `BOOT_FSM_IS_DOWN`
> before phase1. Missing `BOOT_FSM_IS_DOWN` = HW did not enter reset cleanly.

#### STAGE 6: BIOS Boot

> **Reference**: `bios_flow.txt` defines 7 sub-stages (6.0--6.6) with full serconsole +
> debug_port POST code milestones. **Read it now** to compare the checklist below against the
> log. Use the combined milestone grep in Step 4 to locate the exact sub-stage boundary.

Run the Stage 6 sub-phase milestone grep first:
```bash
grep -n "IDI Mux enabled\|Start of uBIOS\|End of RESET_PHASE_7\|END_OF_BIOS\|EarlyPlatformPchInit\|BIOS ID:\|SiliconPolicyUpdatePreMem.*End.*Pre-Memory\|START_MRC_RUN\|Initialize clocks for all MemSs\|JEDEC_DATA\|IpMcMemInitComplete\|PeiInstallPeiMemory\|CEDT ACPI Table\|DXE IPL Entry\|Loading DXE CORE\|NvmExpressDriverBindingStart\|OnReadyToBoot\|PROGRESS CODE: V03051001\|\[Bds\]Booting\|Valid efi partition table\|Booting in blind mode\|IioSecureOnExitBootServices\|ExitBootServiceSmmCallback\|Decompressing Linux\|Linux version" "$TBLOG"
```

Also check debug_port POST code progress (shows SEC and MRC sub-phases not visible in serconsole):
```bash
grep "debug_port.bank.backport" "$TBLOG" | head -120
```

**Sub-phase checklist** (all markers must appear for a healthy Stage 6):

| Sub-stage | Marker | Grep pattern | Indicates |
|-----------|--------|-------------|----------| 
| **6.0 SEC** | Hybrid switch done | `IDI Mux enabled` (MCP) or `Enabling simics cores` (IMH) | VP cores start fetching BIOS |
| 6.0 SEC | BIOS exec started | `Start of uBIOS` | Simics confirms VP instruction fetch began |
| 6.0 SEC | BIOS-only ACED | `End of RESET_PHASE_7` / `END_OF_BIOS` | BIOS-only test passed; absent in SVOS runs |
| 6.0 SEC | SEC alive | debug_port `0x0001` | SEC ROM execution started |
| 6.0 SEC | SEC complete | debug_port `0x007f` | SEC->PEI handoff imminent |
| **6.1 Early PEI** | UART online | `EarlyPlatformPchInit` | First serconsole output |
| 6.1 Early PEI | BIOS ID verified | `BIOS ID: OKSDCRB1` | Correct IFWI loaded |
| 6.1 Early PEI | Pre-mem done | `SiliconPolicyUpdatePreMem.*End.*Pre-Memory` | FSP-M entry approaching |
| **6.2 MRC** | MRC start | `START_MRC_RUN` | FSP-M / MRC entered |
| 6.2 MRC | DDR clocks | `Initialize clocks for all MemSs` | Clock init running |
| 6.2 MRC | DIMM detect | `JEDEC_DATA` (x16) | All 16 DIMMs detected |
| 6.2 MRC | MRC bypass | `IpMcMemInitComplete.*bypassed` | fmod MRC bypass confirmed |
| 6.2 MRC | Memory installed | `PeiInstallPeiMemory` | MRC done; stack on DRAM |
| **6.3 Post-Mem PEI** | CXL ACPI | `CEDT ACPI Table In CXL PEI` | CXL init passed |
| 6.3 Post-Mem PEI | DXE ready | `DXE IPL Entry` | PEI->DXE handoff |
| **6.4 DXE** | DXE loaded | `Loading DXE CORE at` | DXE Core running |
| 6.4 DXE | NVMe enumerated | `NvmExpressDriverBindingStart` | NVMe storage found |
| 6.4 DXE | ReadyToBoot | `OnReadyToBoot` | DXE drivers done |
| 6.4 DXE | RTB code | `PROGRESS CODE: V03051001` | EFI_SW_DXE_BS_PC_READY_TO_BOOT |
| **6.5 BDS** | BDS boot | `[Bds]Booting UEFI 1` | Boot device selection |
| 6.5 BDS | NVMe GPT | `Valid efi partition table header` | NVMe partitions readable |
| 6.5 BDS | Blind mode | `Booting in blind mode` | OS loader starting |
| **6.6 ExitBootServices** | IIO lock | `IioSecureOnExitBootServices` | IIO security locked |
| 6.6 ExitBootServices | ExitBoot SMM | `ExitBootServiceSmmCallback` | SMM ExitBoot called |
| 6.6 ExitBootServices | Linux decompress | `Decompressing Linux` | Kernel image decompressing |
| 6.6 ExitBootServices | Stage 7 start | `Linux version` | OS boot begins |

**Common Stage 6 failures by sub-stage** (see `bios_flow.txt` for full details):
- **6.0**: `IDI Mux enabled` but no debug_port `0x0001` -> BIOS fetch failure; check `bios_fetchor_control`
- **6.0**: debug_port `0x0001` present but hangs before `0x007f` -> SEC stuck (LLC/CAR/ACM issue)
- **6.1**: No serconsole after `0x007f` -> BIOS fetch failure or UART init hang
- **6.1**: `BIOS ID` mismatch -> Wrong IFWI image loaded
- **6.2**: `START_MRC_RUN` present but no `JEDEC_DATA` -> DIMM detection failure (DDR5 SPD error)
- **6.2**: `IpMcMemInitComplete` absent -> MC channel never responded (IMH reset incomplete)
- **6.2**: No `PeiInstallPeiMemory` -> MRC fatal; check EWL entries with bios-issue-analyzer
- **6.3**: `DXE IPL Entry` absent -> Post-memory PEI dispatcher hung; check CxlInitPei
- **6.4**: `NvmExpressDriverBindingStart` absent -> NVMe not enumerated (PCIe issue)
- **6.4**: `PROGRESS CODE: V03051001` absent -> Hang before ReadyToBoot (DXE driver stall)
- **6.5**: `Valid efi partition table` absent -> NVMe not accessible or wrong disk image
- **6.6**: `Decompressing Linux` absent after `Booting in blind mode` -> Kernel image corrupt
- **6.6**: `Linux version` absent after `Decompressing Linux` -> Early kernel crash

> **When Stage 6 is the failing stage**: After completing the Stage checklist and drill-down
> (Steps 3--4), load **`#skill:bios-issue-analyzer`** to perform deep BIOS error analysis.
> The bios-issue-analyzer will decode EWL / IPSD / RC Fatal errors, BIOS assertions, and
> POST code hangs from the `serconsole.con>` output in `testbench.log`, then produce a
> **BIOS Issue Analysis Summary** to include in the final HSLE Run Debug Summary.

#### STAGE 7: OS Boot
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| GRUB menu | `serconsole.con.*GRUB` | Bootloader displayed |
| Kernel start | `Linux version` | Linux kernel executing |
| Kernel command line | `Command line:` | Check for broken parameters |
| Zone ranges | `Zone ranges:` | Memory zones initializing |
| Login prompt (SVOS) | `root@sut:` | SVOS OS boot complete, shell reached |
| Login prompt (CentOS) | `dmr-bkc login:` | CentOS login prompt reached |
| PPR root detected | `[PPR] Got root@` (in emu.devices log) | PPR framework detected OS login |
| PPR test done | `PPR_TEST_DONE` (in emu.devices log, NOT serconsole) | **PPR test PASS** |
| Auto exit | `Auto exit script: exiting` | PPR auto-exit triggered after PPR_TEST_DONE |
| CentOS Timings | `CentOS Timings` | CentOS-specific timing completion marker |

**Common Stage 7 failures**:
- **Kernel `mem=` truncated/empty** → Kernel command line ends with `mem=\r\n` (no value); causes hang during zone init. Fix: set `mem=2G` or remove `mem=`
- **Stuck in zone DMA32 init** → Hundreds of "pages in unavailable ranges" messages then silence; caused by broken `mem=` or oversized memory map
- **Kernel panic** → Check for `Kernel panic` in serconsole output
- **GRUB selects wrong entry** → Wrong kernel may not support this platform
- **Never reaches GRUB** → ExitBootServices failed or BIOS did not find boot device

> **When Stage 7 fails before GRUB** (ExitBootServices reached but no GRUB/kernel output):
> Load **`#skill:bios-issue-analyzer`** to check for BIOS-side errors (late DXE failures,
> boot device enumeration errors) in the serconsole output between ExitBootServices and the
> hang point.

#### STAGE 8: Test Termination

There are **two distinct pass paths** depending on the test type:

**Path A — ACED exit (bare-metal / non-OS tests)**:
The test writes `EBX = ACED`, which triggers `test_end_checker` → writes `results.log`.

**Path B — PPR auto-exit (SVOS / CentOS OS boot tests)**:
The OS boots, the PPR framework runs diagnostics, emits `PPR_TEST_DONE` to the
`emu.devices` log (NOT serconsole), and then `PPR_auto_exit_svos.simics` or
`PPR_auto_exit_centos.simics` triggers "Auto exit script: exiting". These runs
typically have `test_result: -1` and **no `results.log`** — this is normal and expected.

| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| ACED | `Self check EBX = ACED` or `ACED` in results.log | **PASS** (Path A) |
| PPR_TEST_DONE | `PPR_TEST_DONE` (in emu.devices log) | **PASS** (Path B — SVOS/CentOS PPR) |
| Auto exit after PPR | `Auto exit script: exiting` after `PPR_TEST_DONE` | Confirms Path B pass |
| HANG | `reached cycle limit` | Bootstrap timeout — test did not complete in time |
| DEAD | `DEAD` in results.log | Fatal error detected |
| SHUTDOWN | `SHUTDOWN` in results.log | Controlled shutdown |

> **CRITICAL**: When checking Stage 8, always grep for `PPR_TEST_DONE` in the full
> `testbench.log` (not just serconsole lines). It appears in the `emu.devices` info stream:
> ```
> [emu.devices info] {emu.engine 0x... <cycle>} [HH:MM:SS] PPR_TEST_DONE
> ```
> If `PPR_TEST_DONE` is present AND `Auto exit script: exiting` follows it, the run is a
> **PASS** regardless of `test_result: -1` or missing `results.log`.

#### STAGE 8.5: Reset Cycle (runs with one or more post-OS resets)

Some runs perform one or more reset cycles AFTER the initial OS boot, to test reset
handling or IP-disable code paths. When resets are present, Stage 8.5 repeats Stages
5-7 for each reset cycle before reaching a final Stage 8 pass/fail.

**Quick detection**:
```bash
grep -c "RST_TAG HSLE starting reset procedures" "$TBLOG"
# Non-zero -> reset run. Then:
grep -e RST_TAG -e "PPR check: GOT RESET" "$TBLOG"
```

| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Reset triggered (CF9-based) | `PPR check: GOT RESET CF9 6` (WR) / `CF9 14` (CR/GR) | OS wrote CF9 reset register |
| Reset triggered (async) | `RST_TAG AGR event detected` / `RST_TAG AWR event detected` | Platform AGR/AWR signal fired |
| HSLE started reset | `RST_TAG HSLE starting reset procedures` | Simics reset handler took control |
| Reset type chosen | `RST_TAG Warm/Cold/Global reset sequence will be called` | Reset classification done |
| CBB event logged | `Inform RST_TAG: Running WARM/COLD/GLOBAL reset` | CBB event marker (shows cycle+time) |
| HW reset pulsed | `RST_TAG Reset triggered` | Physical XX_RESET_N/PLTRST asserted |
| RTL received reset | `RST_TAG begin reset flow received` (imh8/imh9) | RTL reset in progress |
| Post-reset Stage 5 | `Inform WARM|COLD \| N` (N=1,2...) | Second boot cycle in RTL reset phases |
| Post-reset pass | `PPR_TEST_DONE` (second occurrence) | Reset cycle passed |

> **When Stage 8.5 is the failing stage** (reset triggered but run hangs/dies afterward):
> Load **`the appropriate reset flow file (cold_reset_flow.txt / warm_reset_flow.txt / global_reset_flow.txt)`** for the complete reset type catalog, log signatures, and
> failure analysis table. Then drill into the specific post-reset stage that failed
> (Stage 5/6/7 for the second boot cycle), using the same checklists above.


---

### Step 4 — Drill Down into Failure Zone

Once the failing stage is identified, drill deeper:

#### For timeout / hang (no results.log):
```bash
# Find the last console output line and timestamp
grep -n "serconsole.con>" "$TBLOG" | tail -5

# Find when timeout hit
grep "reached cycle limit" "$TBLOG"

# Calculate silence gap: wall clock between last output and timeout line
```

#### For BIOS/OS boot failures (Stage 6–7):
```bash
# Get kernel command line — check for broken params like empty mem=
grep "Command line:" "$TBLOG"

# Get last 20 UART console messages
grep "serconsole.con>" "$TBLOG" | tail -20

# Check for errors or panics
grep -i "error\|panic\|assert\|fatal" "$TBLOG" | grep "serconsole" | tail -20
```

#### For Stage 6 BIOS sub-phase drill-down (use after sub-phase checklist above):
```bash
# Get all Stage 6 sub-phase milestones with line numbers (serconsole stream)
grep -n "EarlyPlatformPchInit\|BIOS ID:\|START_MRC_RUN\|PeiInstallPeiMemory\|DXE IPL Entry\|Loading DXE CORE\|NvmExpressDriverBindingStart\|OnReadyToBoot\|\[Bds\]Booting\|IioSecureOnExitBootServices\|Decompressing Linux\|Linux version" "$TBLOG"

# Get full debug_port POST code timeline (SEC + MRC progress visible here)
grep "debug_port.bank.backport" "$TBLOG" | head -120

# Decode last POST code -> BIOS sub-phase at hang:
#   0x0001-0x007f  -> 6.0 SEC    0x00a0-0x00af  -> 6.1 Early PEI
#   0x00e0-0x00ef  -> 6.2 FSP-M/MRC entry   0x007e -> 6.2 MRC separator
#   0x00b0-0x00df  -> 6.2 MRC training   0x0051 -> 6.3 Post-mem PEI
#   0x0052-0x0056  -> 6.4 DXE   0x0090 -> 6.4 SMM   0x0057 -> 6.5 BDS
#   0x0058         -> 6.6 ExitBootServices entered

# MRC-specific: verify JEDEC_DATA count (expect 16 for 16-DIMM config)
grep "JEDEC_DATA" "$TBLOG" | wc -l

# MRC-specific: check IpMcMemInitComplete appeared and was bypassed (fmod run)
grep "IpMcMemInitComplete" "$TBLOG"
```

#### For reset phase failures (Stage 5):

> **Read `reset_phase_flow.txt` first** to understand all three log streams before running the greps below.  > **Emurun options affect cycle timing validity**: The cycle-timing hints in `reset_phase_flow.txt` > are derived from a specific golden run. If the run under analysis uses different emurun options, > those timings may not apply. Before treating any timing delta as a hang indicator, check: > - `-ver` / `--ver` (RTL model version) — different model = different timing baseline > - `*_tracker_en` flags — extra trackers add significant cycle overhead per phase > - `-xtor` / `-xtors` options — transactor changes alter simulation speed > - `-override_input_dir` — different input binaries may produce different phase durations > > These are the four fields compared by `EmurunOptChecker.py` in the reset_checker_tools repo. > If any differ from the golden, use relative ordering of events (not absolute cycle counts) > to determine whether a stream is stalled.  ```bash
# Show all high-level reset phase markers
grep -n "RESET_PHASE" "$TBLOG"

# Stall locator: shows exactly where script is stuck waiting (D2D/Phase4/Phase5 only;
# "Waiting for End of RESET_PHASE_3_INFRA marker" is commented out in script -- not logged)
grep "Waiting for End of RESET_PHASE" "$TBLOG"

# --- STREAM 1: CBB BOOT_FSM + phase trigger assertions ---
grep -n "Inform COLD\|Inform WARM\|BOOT_FSM\|Starting phase\|RstCore\|xxWarmBootTrigger\|lip_trigger\|BIOS Start\|BIOS Done\|Begin WARM Reset\|BOOT_FSM_IS_DOWN" "$TBLOG"

# Find exactly where BOOT_FSM stalled (last sub-event in phase1)
grep "BOOT_FSM" "$TBLOG" | tail -5

# Check whether phase triggers were asserted
grep "RstCore\|lip_trigger\|xxWarmBootTrigger" "$TBLOG"

# --- STREAM 2: HWRS events + symmetry check ---
grep -n "sequencer_log: HWRS" "$TBLOG"

# Symmetry check: both imh0 and imh1 must have equal event counts
grep "sequencer_log: HWRS" "$TBLOG" | grep -oP "imh\s*\d+" | sort | uniq -c

# --- STREAM 3: IMH Primecode states + symmetry check ---
grep -n "imh primecode state" "$TBLOG"

# Find last primecode state reached (stall point)
grep "imh primecode state" "$TBLOG" | tail -10

# Symmetry check: both die8 and die9 must have equal state counts
grep "imh primecode state" "$TBLOG" | grep -oP "die\d+" | sort | uniq -c

# Check for D2D link train issues
grep "D2D\|UCIe\|link train" "$TBLOG" | tail -20

# Warm reset: check BOOT_FSM_IS_DOWN appeared after warm reset triggered
grep "Inform WARM\|BOOT_FSM_IS_DOWN" "$TBLOG" | head -10
```

#### For OS boot PPR test completion check (Stage 7–8):
```bash
# Check for PPR_TEST_DONE (appears in emu.devices log, NOT in serconsole)
grep -n "PPR_TEST_DONE" "$TBLOG"

# Check for PPR auto-exit trigger
grep -n "Auto exit script" "$TBLOG"

# Check PPR framework root detection
grep -n "\[PPR\] Got root@" "$TBLOG"

# Verify the test type (SVOS vs CentOS) from post-setup scripts
grep "simics_post_setup_script" "$TBLOG" | head -5

# Check login prompt reached (SVOS: root@sut, CentOS: dmr-bkc login:)
grep -n "root@sut\|dmr-bkc login:" "$TBLOG" | head -5
```

#### For setup failures (Stage 0–1):
```bash
# Check for Python exceptions
grep -n "Traceback\|Exception\|Error" "$TBLOG" | head -20

# Check for missing files
grep -i "not found\|No such file\|does not exist\|FileNotFoundError" "$TBLOG" | head -10
```

#### For reset cycle failures (Stage 8.5):

> **Read `the appropriate reset flow file (cold_reset_flow.txt / warm_reset_flow.txt / global_reset_flow.txt)` first** for the complete reset type catalog and HW signal reference.

```bash
# Step A: Confirm reset detected and classify type
grep -n "RST_TAG HSLE starting\|RST_TAG.*reset sequence\|PPR check: GOT RESET" "$TBLOG"

# Step B: Check the CBB event markers (cycle + timestamp for each reset)
grep "Inform RST_TAG: Running" "$TBLOG"

# Step C: Verify HW reset signaling happened
grep "RST_TAG Reset triggered\|RST_TAG XX_RESET_N\|RST_TAG PLTRST\|RST_TAG begin reset flow" "$TBLOG"

# Step D: Check Global Reset SLP signals (GR only)
grep "Forced GLOBAL_RESET_N\|SLP_S5 Asserted\|GLB_RST" "$TBLOG"

# Step E: Check post-reset Stage 5 started (WARM vs COLD path)
grep "Inform WARM\|Inform COLD\|BOOT_FSM_IS_DOWN" "$TBLOG" | head -20

# Step F: IP disable check -- did reset happen before first PPR_TEST_DONE?
grep -n "PPR_TEST_DONE" "$TBLOG" | head -3
grep -n "RST_TAG HSLE starting reset procedures" "$TBLOG" | head -3
# If RST_TAG line number < first PPR_TEST_DONE line number -> IP disable scenario
```

For post-reset BIOS/OS failures apply the Stage 5, 6, or 7 checklists to the second boot cycle.

---

### Step 5 — Check Known Failure Signatures

Match findings against this catalog of known failure signatures:

| Signature | Stage | Root Cause | Fix / Action |
|-----------|-------|------------|-------------|
| `mem=\r\n` (empty mem= in kernel cmdline) | 7 | Truncated `mem=` parameter in GRUB/BIOS boot args | Set `mem=2G` or remove `mem=` from kernel args |
| Stuck on "On node 0, zone DMA32: N pages in unavailable ranges" then silence | 7 | Kernel hung in `free_area_init` due to broken `mem=` or oversized memory map | Fix `mem=`, or reduce DIMM count in `vp.simics` |
| Never reaches `RTI: Mounted` | 1 | ZeBu board connection failure (PCIe timeout, board not allocated) | Check ZeBu reservation, board health, pre_mount errors |
| "Waiting for RTL Core Reset" with no UCLK message following | 3/5 | RTL never reached reset vector — reset phase hang | Check which reset phase stalled (Step 3 Stage 5 checklist) |
| Only one IMH die reports `RESET_PHASE_3_INFRA` | 5 | One IMH die stalled during infrastructure init | Check IMH forces, fuse loading for the stalled die |
| `RESET_PHASE_3_D2D` never completes | 5 | UCIe die-to-die link training failure | Check UCIe ignite scripts, D2D PHY init |
| No serconsole output after "IDI Mux enabled" | 6.0 | BIOS fetch failure -- VP core not fetching from IFWI | Check `bios_fetchor_control`, BIOS image path, hybrid mapping |
| debug_port `0x0001` absent after "IDI Mux enabled" | 6.0 | BIOS instruction fetch never started | Check `bios_fetchor_control` and BIOS flash mapping |
| debug_port stuck between `0x0001` and `0x007f` | 6.0 | SEC phase hang -- LLC/CAR or ACM stall | Check LLC config, ACM image, CAR init |
| No serconsole after debug_port `0x007f` | 6.1 | PEI Core failed to start after SEC | Check IFWI integrity; re-flash with correct image |
| `BIOS ID` mismatch in serconsole | 6.1 | Wrong IFWI image loaded | Check `bios_image` / `ifwi1.bin` path in `vp.simics` |
| `START_MRC_RUN` present but `JEDEC_DATA` absent | 6.2 | DIMM detection failure -- DDR5 SPD error | Check DFI xtors, DIMM config in SPD file |
| `JEDEC_DATA` count < 16 | 6.2 | Not all DIMMs detected | Verify 16-DIMM config in `vp.simics` SPD |
| `IpMcMemInitComplete` absent | 6.2 | MC channel never responded | IMH reset incomplete; check Reset Phase 4 |
| `IpMcMemInitComplete` NOT `bypassed` in fmod run | 6.2 | fmod not applied correctly | Verify `cbbpunit_imhpunit_s3m_fmod=True` |
| `START_MRC_RUN` present but no `PeiInstallPeiMemory` | 6.2 | MRC fatal | Decode EWL entries with `bios-issue-analyzer` |
| `DXE IPL Entry` absent after `PeiInstallPeiMemory` | 6.3 | Post-memory PEI hung | Check CxlInitPei.efi, CpuMpPei.efi |
| `NvmExpressDriverBindingStart` absent | 6.4 | NVMe not enumerated -- PCIe issue | Check PCIe config, HIOP xtor |
| `PROGRESS CODE: V03051001` absent | 6.4 | Hang before ReadyToBoot | Check last DXE driver; look for ASSERT_EFI_ERROR |
| `[Bds]Booting` absent after `OnReadyToBoot` | 6.5 | BDS boot failed | Check NVMe image path |
| `Valid efi partition table header` absent | 6.5 | NVMe GPT unreadable | Verify `disk_image` path in `vp.simics` |
| `Decompressing Linux` absent after `Booting in blind mode` | 6.6 | Kernel image corrupt | Check OS image integrity |
| `Linux version` absent after `Decompressing Linux` | 6.6 | Early kernel crash | Check kernel image compatibility |
| `Traceback` or `Exception` before "Mounted" | 1 | Python script error during setup | Read the full traceback; usually a missing file or bad config |
| `reached cycle limit: N` with no results.log | 8 | Bootstrap timeout — run did not finish in time | Identify last stage reached (Step 3), debug that stage |
| `StreamPCIe instance ... is not configured` (many repeated) | 5–6 | PCIe xtors not configured | Check `pcie_en` flag; ignore if PCIe is not under test |
| DXE hangs after CXL DXE entry | 6 | CXL driver hang during PCIe/CXL enumeration | Check CXL config, HIOP xtor status |
| Kernel panic in serconsole | 7 | Linux kernel crash — driver or memory issue | Read panic message for specific cause |
| `PPR_TEST_DONE` present + `Auto exit script: exiting` + no `results.log` | 8 | **PASS** — PPR test completed successfully (SVOS/CentOS) | This is the normal pass path for OS boot PPR runs. Report as PPR_PASS. |
| `PPR_TEST_DONE` absent + `Auto exit script: exiting` + no `results.log` | 8 | PPR auto-exit fired before test completion | Check PPR_auto_exit timeout; verify PPR test scripts are present in OS image |
| `dmr-bkc login:` present but no `PPR_TEST_DONE` | 7–8 | CentOS booted to login but PPR test did not run | Check PPR test scripts on the CentOS disk image |
| `root@sut:` present but no `PPR_TEST_DONE` | 7–8 | SVOS booted to shell but PPR test did not run | Check PPR test scripts on the SVOS disk image |
| CBB stuck at `BOOT_FSM_SA_PLL_FUSE_PULL`, no `BOOT_FSM_DOWNLOAD_UC_FW` | 5 | S3M not ready to serve UC FW download | Check S3M FW load, fuse image availability |
| CBB stuck at `BOOT_FSM_DOWNLOAD_UC_FW`, no `xxWarmBootTrigger` received | 5 | UC FW download timeout (expect ~363M cold / ~408M warm cycles) | Check S3M FW version; verify `-enable_s3m_loading` and S3M fmod |
| `xxWarmBootTrigger` never received after `BOOT_FSM_DOWNLOAD_UC_FW` | 5 | S3M FW failed to assert WarmBootTrigger | Check S3M FW version and FW_BYPASS_0 value |
| `RstCore assertion` never received (stuck at phase2 wait) | 5 | PUnit primecode never asserted RstCore | Check primecode image; look at last primecode state reached |
| `lip_trigger (bsp)` never received (stuck at phase3 wait) | 5 | HWRS/PUnit never released lip_trigger | Coherency/fabric stall; check last HWRS event and primecode state |
| HWRS event count unequal for imh0 vs imh1 | 5 | One IMH die stalled in reset sequencer | Run HWRS imh symmetry check; inspect primecode state for stalled die |
| Primecode state count unequal for die8 vs die9 | 5 | One IMH die hung at a primecode sync point | Run primecode die symmetry check; last state on lagging die = stall point |
| Primecode stuck at `WAIT_FOR_CBB_D2D_READY` (0x13) | 5 | CBB D2D link never came up | Check UCIe ignite scripts; verify CBB sle.simics ran; check D2D trackers |
| Primecode stuck at `HPM_CREDIT_INIT_CBB_SYNC` (0x14) | 5 | CBB-to-IMH HPM credit deadlock | Verify UCIe D2D link up in both directions; check CBB primecode state |
| Primecode stuck at `D2D_MB_BASIC_TRAINING` (0x28) | 5 | D2D mailbox training failure | Check UCIe protocol trackers; D2D link quality |
| `BOOT_FSM_IS_DOWN` missing after `Begin WARM Reset` | 5 | HW did not enter warm reset IS_DOWN state | Warm reset sequencing error; check SVID/VR, CPLD state |
| `PCODE2_COMPLETE` missing after `HWRS_RESET_COMPLETE` | 5 | Phase5 primecode stall after HWRS declares done | Check last primecode state on both dies (die8, die9) |

---

### Step 5b — Reset Cycle Detection and Analysis

After completing Stage 0-7 analysis, check for reset cycles. Do this ALWAYS -- not
just when all stages pass. A BIOS-initiated reset will cause Stage 6 to be PARTIAL and
Stage 7 to be NOT REACHED, which is expected behavior for reset runs:

```bash
# Check if this is a reset run (any RST_TAG: triggering marker)
grep -c "RST_TAG: triggering\|RST_TAG.*HSLE starting reset" "$TBLOG"

# If count > 0, this is a reset run. Determine the reset type:
grep "RST_TAG: triggering\|COLD_RESET through\|WARM_RESET through\|GLOBAL_RESET through\|GOT RESET CF9" "$TBLOG"

# Count PPR_TEST_DONE occurrences (expect 1 per boot cycle)
grep -c "PPR_TEST_DONE" "$TBLOG"

# Count reset cycles (each RST_TAG: triggering = one reset)
grep -c "RST_TAG: triggering\|RST_TAG.*HSLE starting reset" "$TBLOG"
```

**Reset type determination:**
- `COLD_RESET through` → Load `cold_reset_flow.txt`, analyze Stages 8-13
- `WARM_RESET through` or `GOT RESET CF9 6` (without gbl_etr3) → Load `warm_reset_flow.txt`
- `GLOBAL_RESET through` or `GOT RESET CF9 6` (with gbl_etr3=1) → Load `global_reset_flow.txt`

**Back-to-back resets:** If multiple `RST_TAG: triggering` markers exist, analyze each
reset cycle sequentially. Each cycle follows Stages 8-13. The Nth cycle's Stage 10-12
milestones appear after the (N-1)th cycle's completion. For each cycle:
1. Identify the reset type from the RST_TAG markers for that cycle
2. Load the corresponding reset flow reference file
3. Verify all milestones for Stages 8-13 of that cycle
4. The first cycle with a missing milestone is the failure point

---

### Step 6 — Produce Debug Summary

**IMPORTANT**: Do NOT display the summary in the chat window. Write it to a file instead.

#### Output File
Write the summary to: `result/<run_name>_hsle_debug_agent_summary.txt`

#### Template Selection
- **Normal cold boot** (no reset detected): Use template from
  `.github/skills/hsle-run-debugger/templates/summary_cold_boot.txt`
- **Reset scenario** (one or more reset cycles detected): Use template from
  `.github/skills/hsle-run-debugger/templates/summary_reset_scenario.txt`

#### Procedure
1. Read the appropriate template file
2. Fill in ALL placeholders with actual values from the analysis
3. For PASS runs, set "N/A" for failure analysis fields
4. For reset scenarios with back-to-back resets, duplicate the reset cycle block
   for each additional cycle
5. **Write the completed summary using the `create_file` tool** (VS Code file
   system API). Do NOT use terminal commands (`cat >`, heredocs, `echo`,
   `python3 -c`) to write the summary — the default shell is **tcsh**, which
   does not support heredocs and mangles special characters. If terminal-based
   writing is absolutely required, write a `.py` helper script to disk first
   using `create_file`, then execute it with `python3`.
6. Confirm to the user: "Debug summary written to: <path>"

#### Field Guidelines
- `{result}`: Use exactly one of: `PASS (ACED)`, `PASS (PPR_PASS)`, `FAIL (HANG)`,
  `FAIL (DEAD)`, `FAIL (TIMEOUT)`, `FAIL (NO_RESULT)`
- `{stageN}`: Use `PASS`, `FAIL — <brief description>`, or `NOT REACHED`
- `{failing_stage}`: e.g., "Stage 7 — OS Boot" or "Stage 10 — Second Boot RTL (Reset Cycle 1)"
- `{evidence}`: Include 3-5 actual grep lines from testbench.log with line numbers
- `{root_cause}`: 2-5 sentences with specific log evidence
- `{recommendations}`: Numbered list of 1-3 actionable steps
- `{log_evidence}`: 5-10 key grep lines that support the diagnosis

---

## Output

Return the **HSLE Run Debug Summary** (Step 6 format) followed by specific recommendations.
State clearly which stage failed, what log evidence supports it, and which known signature (if any) matches.
