---
name: hsle-run-debugger
description: Debug DMR MCP ICI HSLE emulation runs by analyzing testbench.log against the known-good execution flow (flow.txt). Identifies which stage failed, extracts failure signatures, and provides root-cause triage with debug recommendations. Use when a user provides an HSLE run directory path and asks to debug, diagnose, or analyze a failing or hanging HSLE run.
---

# HSLE Run Debugger

**Purpose**: Diagnose failures in DMR MCP ICI HSLE (ZeBu ZSE5) emulation runs by systematically
comparing `testbench.log` milestones against the golden execution flow defined in
`.github/skills/hsle-run-debugger/flow.txt`.

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

## Golden Flow Reference

The golden execution flow is documented in:
```
.github/skills/hsle-run-debugger/flow.txt
```

This file defines **9 stages** (STAGE 0–8) of the HSLE execution, milestone markers for each
stage, and the expected chronological order. **Read this file at the start of every debug
session** to have the full reference before running any greps.

---

## Procedure

### Step 1 — Locate and Validate testbench.log

```bash
# Check if testbench.log exists (plain or gzipped)
ls <run_dir>/testbench.log*

# Check line count to assess log completeness
wc -l <run_dir>/testbench.log
# or for gzipped: zcat <run_dir>/testbench.log.gz | wc -l

# Check for results.log — indicates run reached an exit handler
cat <run_dir>/test/results.log 2>/dev/null || echo "NO results.log — run likely timed out or crashed"
```

If testbench.log is gzipped, use `zgrep` and `zcat` instead of `grep` and `cat` in all
subsequent steps.

**Expected line counts** for a complete SVOS/CentOS boot run: 500K–700K lines.
- < 10K lines → failed during setup (Stage 0–1)
- 10K–300K lines → failed during ZeBu connect or pre-emulation (Stage 1)
- 300K–350K lines → failed during reset phases (Stage 5)
- 350K–650K lines → failed during BIOS/OS boot (Stage 6–7)

---

### Step 2 — Extract Stage Milestones

Run the master milestone grep. This single command captures markers for all 9 stages:

```bash
TBLOG="<run_dir>/testbench.log"

grep -n "RTI:\|RESET_PHASE\|hsle.simics\|IDI Mux\|Hybrid Core\|UCLK\|Waiting for RTL\|FW_BYPASS\|pdisable\|penable\|fuse_load\|primecode\.py\|mem_load\|Mounted\|Pre Mount\|sle.simics.*Project\|sle.simics.*determine_model\|sle.simics.*Running\|ACED\|DEAD\|HANG\|SHUTDOWN\|bootstrap_timeout\|reached cycle limit\|end_of_run\|Linux version\|Kernel command line\|ExitBootServices\|serconsole.*GRUB\|centos_post\|svos_post\|PPR_auto_exit\|Error\|ERROR\|FATAL\|Exception\|Traceback\|quit.*Simics" "$TBLOG" | head -120
```

---

### Step 3 — Stage-by-Stage Checklist

Compare extracted milestones against this checklist. For each stage, verify the **required
markers** appear. The first stage with missing markers is the failure point.

#### STAGE 0: SPARK Bootstrap
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Bootstrap start | `[bootstrap info] running` | SPARK framework started |
| sle.simics entry | `Running sle.simics setup_script` | Handoff to main script |

#### STAGE 1: sle.simics — Main Script Setup
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Script load | `[sle.simics] Project Simics script loading` | sle.simics executing |
| Model detection | `model we are running on is` | determine_model.py OK |
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
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| Hybrid Xtor Setup | `[hsle.simics] Hybrid Xtor Setup` | IDI xtor configuration |
| FW BYPASS | `[hsle.simics]  FW BYPASS Override S3M` | S3M fmod bypass set |
| Waiting for RTL Core Reset | `[hsle.simics]  Waiting for RTL Core Reset` | Waiting for reset vector |
| UCLK Ungating | `[hsle.simics] UCLK Ungating fix` | Post-phase-6 UCLK fix |
| Waiting for IDI flush | `[hsle.simics] Waiting for IDI to flush` | 5M cycle IDI drain |
| Enabling Hybrid Cores | `[hsle.simics] Enabling Hybrid Core` | About to enable VP cores |
| IDI Mux enabled | `[hsle.simics] IDI Mux enabled` | **HYBRID SWITCH COMPLETE** |

**Common Stage 3 failures**:
- Stuck at "Waiting for RTL Core Reset" → RTL never reached reset vector; check reset phase progression (Stage 5)
- IDI Mux never enabled → Hybrid switch failed; check IDI xtor credits, UCLK ungating

#### STAGE 4: CBB Reset Flow (parallel branch)
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| CBB reset branch | `script-branch.*cbb_reset` or CBB-specific logs | CBB `sle.simics` running |

#### STAGE 5: RTL Reset Phases
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

**Common Stage 5 failures**:
- Stuck before Phase 1 → S3M boot failure; check S3M fmod bypass, IBL loading
- Phase 2 never ends → PUnit primecode hang; check primecode image version
- Phase 3 INFRA incomplete → Infrastructure init stall; check both IMH dies (imh8 and imh9 must both report)
- Phase 3 D2D never completes → UCIe link train failure between IMH↔CBB; check UCIe ignite scripts
- Phase 4 hang → DDR memory training failure; check DFI xtors, DIMM config
- Phase 5 hang → Late coherency setup failure; check UPI/fabric init

#### STAGE 6: BIOS Boot
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| BIOS PEI | `serconsole.con.*PeiServicesInstallPeiMemory` | PEI memory installed |
| DXE entry | `serconsole.con.*DXE IPL Entry` | DXE phase started |
| ExitBootServices | `ExitBootServicesEntry` or `ExitBootServices` | BIOS→OS handoff |

**Common Stage 6 failures**:
- No serconsole output after IDI Mux enabled → BIOS fetch failure; check `bios_fetchor_control`
- Stuck in PEI → Memory Reference Code (MRC) hang; check `mrc_mem_flows` bitmask
- Stuck in DXE → Driver loading issue; check PCIe enumeration, CXL DXE
- Never reaches ExitBootServices → BIOS hang in BDS; check NVMe/boot device

#### STAGE 7: OS Boot
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| GRUB menu | `serconsole.con.*GRUB` | Bootloader displayed |
| Kernel start | `Linux version` | Linux kernel executing |
| Kernel command line | `Command line:` | Check for broken parameters |
| Zone ranges | `Zone ranges:` | Memory zones initializing |
| Login prompt | `login:` or OS-specific marker | OS boot complete |
| CentOS Timings / PPR_TEST_DONE | `CentOS Timings` or `PPR_TEST_DONE` | Test completion marker |

**Common Stage 7 failures**:
- **Kernel `mem=` truncated/empty** → Kernel command line ends with `mem=\r\n` (no value); causes hang during zone init. Fix: set `mem=2G` or remove `mem=`
- **Stuck in zone DMA32 init** → Hundreds of "pages in unavailable ranges" messages then silence; caused by broken `mem=` or oversized memory map
- **Kernel panic** → Check for `Kernel panic` in serconsole output
- **GRUB selects wrong entry** → Wrong kernel may not support this platform
- **Never reaches GRUB** → ExitBootServices failed or BIOS did not find boot device

#### STAGE 8: Test Termination
| Marker | Grep pattern | Indicates |
|--------|-------------|-----------|
| ACED | `Self check EBX = ACED` or `ACED` in results.log | **PASS** |
| HANG | `reached cycle limit` | Bootstrap timeout — test did not complete in time |
| DEAD | `DEAD` in results.log | Fatal error detected |
| SHUTDOWN | `SHUTDOWN` in results.log | Controlled shutdown |

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

#### For reset phase failures (Stage 5):
```bash
# Show all reset phase markers with line numbers
grep -n "RESET_PHASE" "$TBLOG"

# Verify both IMH dies reported (imh8 AND imh9 must appear)
grep "RESET_PHASE_3_INFRA is complete" "$TBLOG"

# Check for D2D link train issues
grep "D2D\|UCIe\|link train" "$TBLOG" | tail -20
```

#### For setup failures (Stage 0–1):
```bash
# Check for Python exceptions
grep -n "Traceback\|Exception\|Error" "$TBLOG" | head -20

# Check for missing files
grep -i "not found\|No such file\|does not exist\|FileNotFoundError" "$TBLOG" | head -10
```

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
| No serconsole output after "IDI Mux enabled" | 6 | BIOS fetch failure — VP core not fetching from IFWI | Check `bios_fetchor_control`, BIOS image path, hybrid mapping |
| `Traceback` or `Exception` before "Mounted" | 1 | Python script error during setup | Read the full traceback; usually a missing file or bad config |
| `reached cycle limit: N` with no results.log | 8 | Bootstrap timeout — run did not finish in time | Identify last stage reached (Step 3), debug that stage |
| `StreamPCIe instance ... is not configured` (many repeated) | 5–6 | PCIe xtors not configured | Check `pcie_en` flag; ignore if PCIe is not under test |
| DXE hangs after CXL DXE entry | 6 | CXL driver hang during PCIe/CXL enumeration | Check CXL config, HIOP xtor status |
| Kernel panic in serconsole | 7 | Linux kernel crash — driver or memory issue | Read panic message for specific cause |

---

### Step 6 — Produce Debug Summary

Output a structured summary in this format:

```
HSLE Run Debug Summary
======================
Run Path    : <run_dir>
Test Name   : <testname>
OS Image    : <os_image from testbench.log>
Result      : <ACED / HANG / DEAD / TIMEOUT / NO_RESULT>

Stage Progress:
  Stage 0 (Bootstrap)         : PASS
  Stage 1 (sle.simics setup)  : PASS
  Stage 2 (VP platform)       : PASS
  Stage 3 (HSLE core setup)   : PASS
  Stage 4 (CBB reset)         : PASS
  Stage 5 (Reset phases)      : PASS (all 6 phases completed)
  Stage 6 (BIOS boot)         : PASS (ExitBootServices reached)
  Stage 7 (OS boot)           : *** FAIL — <description> ***
  Stage 8 (Test termination)  : NOT REACHED

Last Activity:
  Last console output : line <N> @ <wall clock timestamp>
  Emu cycle at last   : <cycle>
  Timeout at          : <cycle> (<wall clock>)
  Silence gap         : <N minutes>

Failure Signature : <matching known signature, or "New — describe">
Root Cause        : <concise analysis with log evidence>
Recommendations   : <specific, actionable steps>
```

---

## Reference Run Comparison

When diagnosing, compare key parameters against the known-good reference run (SVOS,
26ww12_2, `mcp_ici_hsle_svos_fmod.0`):

| Parameter | Reference (SVOS) | How to check in failing run |
|-----------|-----------------|----------------------------|
| OS image | SVOS 26WW09.3 (`sut-diamondrapids-efi.amd64.craff`) | `grep "os_image\|disk_image" testbench.log` |
| Kernel cmdline | Proper `mem=` value present | `grep "Command line:" testbench.log` |
| bootstrap_timeout | 9,000,000,000 cycles | `grep "bootstrap_timeout" testbench.log` |
| DIMM count | 16 DIMMs (8 per IMH die) | Check `vp.simics` DDR config section in log |
| Fmod flags | `cbbpunit_imhpunit_s3m_fmod=True` | `grep "fmod" testbench.log` |
| BIOS version | Check IFWI path | `grep -i "bios\|ifwi" testbench.log \| head -10` |

---

## Runtime Timeline (from reference run)

For quick stage timing comparison:

| Wall Clock | Emu Cycle | Event |
|-----------|-----------|-------|
| ~T+0m | 0 | RTI: Pre Cycle 0, Hit cycle_0 |
| ~T+0m | 0 | RTI: Pre Mount (connecting to ZeBu) |
| ~T+3m | 0 | RTI: Mounted (ZeBu connected) |
| ~T+4m | 0 | Fuse + Primecode + S3M loading |
| ~T+6m | 0 | S3M FW_BYPASS set, VP disabled, Waiting for RTL Core Reset |
| ~T+8m | ~210 | RESET_PHASE_1 starts |
| ~T+17m | ~419M | End RESET_PHASE_2 |
| ~T+21m | ~606M | RESET_PHASE_3_INFRA complete (both IMH) |
| ~T+21m | ~607M | RESET_PHASE_3_D2D complete |
| ~T+22m | ~624M | RESET_PHASE_3_INFRA_CFG complete |
| ~T+24m | ~652M | End RESET_PHASE_4 |
| ~T+30m | ~875M | End RESET_PHASE_5 → RESET_PHASE_6 → Hybrid switch |
| ~T+33m+ | ... | BIOS boot (SEC→PEI→DXE→BDS), ~30–60 min total |
| ~T+66m | ~40.7B | SVOS GRUB menu |
| ~T+67m | ~40.7B | Linux kernel starts |

---

## Output

Return the **HSLE Run Debug Summary** (Step 6 format) followed by specific recommendations.
State clearly which stage failed, what log evidence supports it, and which known signature (if any) matches.
