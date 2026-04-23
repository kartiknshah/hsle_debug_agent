---
name: bios-issue-analyzer
description: >
  Analyze BIOS failures in DMR MCP ICI HSLE runs (Stage 6–7) or from standalone BIOS serial
  logs. Decodes EWL (Enhanced Warning Log), IPSD, and RC Fatal error codes from serconsole
  output embedded in testbench.log. Also detects BIOS assertion failures (ASSERT_EFI_ERROR,
  ASSERT macros) and POST code hangs. Produces a structured BIOS Issue Analysis Summary with
  hardware topology (Socket/Channel/DIMM/Rank), code names, descriptions, and root-cause
  recommendations. Use when Stage 6 (BIOS boot) or Stage 7 (OS boot) fails in an HSLE run,
  or when a user provides a BIOS serial log and asks to analyze BIOS errors.
---

# BIOS Issue Analyzer

Decode and triage BIOS failures from **DMR MCP ICI HSLE emulation runs** or standalone BIOS
serial logs. Covers EWL, IPSD, RC Fatal, ASSERT, and POST code failures.

---

## When to Use

- **HSLE run Stage 6 failure**: `hsle-run-debugger` has identified the run failed during
  BIOS boot (PEI/DXE/BDS) and has called this skill for deeper BIOS analysis.
- **HSLE run Stage 7 failure**: OS boot failed and BIOS-side errors (EWL, assertions, POST
  codes) need to be checked as a contributing factor.
- **Standalone**: User provides a raw BIOS serial log path and asks to decode errors.
- **Single code decode**: User asks "what does EWL 0x0A/0x05 mean?" or "decode PC-73".

---

## Data Sources (all self-contained in `scripts/`)

| File | Contents |
|------|----------|
| `ewl_codes_database.json` | 124 EWL major + 412 minor codes |
| `rc_fatal_errors_database.json` | 49 RC Fatal major + 449 minor codes |
| `ipsd_codes_database.json` | 23 IPSD error codes |
| `post_codes_database.json` | DMR BIOS POST + ACPI debug codes (port 0x80) |
| `decoder_utils.py` | Shared JSON loader / hex normalizer |

Do **not** guess or invent codes. If a code is not in the database, say so.

---

## Input

### From an HSLE Run (testbench.log)

The BIOS serial output is embedded in `testbench.log` as lines containing `serconsole.con>`.
Extract and analyze it with:

```bash
TBLOG="<run_dir>/testbench.log"
# or for gzipped logs use zgrep/zcat throughout

# --- Step A: Quick error pattern scan (no script needed) ---
grep "serconsole.con>" "$TBLOG" | grep -i \
  "Enhanced warning\|Major Warning Code\|FATAL ERROR\|RC_FATAL_ERROR\|ASSERT_EFI_ERROR\|ASSERT ~\|ERROR: C8" \
  | head -60

# --- Step B: Extract serconsole text and run full decoder ---
grep "serconsole.con>" "$TBLOG" | sed 's/.*serconsole\.con> //' \
  | python3 .github/skills/bios-issue-analyzer/scripts/decode_ewl.py --log /dev/stdin

# --- Step C: POST code / checkpoint extraction ---
grep "serconsole.con>" "$TBLOG" | grep -iE \
  "checkpoint|post.code|0x[0-9a-f]{2}[^0-9a-f]" | tail -30
```

### From a Standalone BIOS Serial Log

```bash
# Full log analysis
python3 .github/skills/bios-issue-analyzer/scripts/decode_ewl.py --log /path/to/bios_serial.log

# Single code decode
python3 .github/skills/bios-issue-analyzer/scripts/decode_ewl.py --code 0x0A --minor 0x05
python3 .github/skills/bios-issue-analyzer/scripts/decode_post_code.py 0x73
```

---

## Procedure

### Step 1 — Locate BIOS Output

For HSLE runs, confirm Stage 6 was reached:
```bash
# Verify IDI Mux (hybrid switch) occurred — BIOS fetch starts after this
grep -n "IDI Mux enabled" "$TBLOG"

# Check for any serconsole output after IDI Mux enabled
IDI_LINE=$(grep -n "IDI Mux enabled" "$TBLOG" | tail -1 | cut -d: -f1)
tail -n +"$IDI_LINE" "$TBLOG" | grep "serconsole.con>" | head -10
```

**If no serconsole output after IDI Mux enabled**: BIOS fetch failure — VP core is not
fetching from IFWI. Check `bios_fetchor_control` and BIOS image path. This is a Stage 6
setup issue, not a BIOS log issue.

---

### Step 2 — Scan for Error Signatures

Run these targeted greps on the serconsole output section of testbench.log:

```bash
TBLOG="<run_dir>/testbench.log"

# EWL / RC Fatal / IPSD
grep "serconsole.con>" "$TBLOG" | grep -i \
  "Enhanced warning\|Major Warning Code\|FATAL ERROR\|RC_FATAL_ERROR\|ERROR: C8\|Error Logged"

# BIOS Assertions
grep "serconsole.con>" "$TBLOG" | grep -i "ASSERT"

# Last BIOS checkpoint / POST code (what was BIOS doing before it stopped?)
grep "serconsole.con>" "$TBLOG" | tail -30

# MRC memory training failures (PEI phase)
grep "serconsole.con>" "$TBLOG" | grep -i \
  "MRC\|memory training\|DIMM\|DDR\|memory init\|PeiServicesInstallPeiMemory" | head -20

# DXE driver failures
grep "serconsole.con>" "$TBLOG" | grep -i \
  "DXE\|driver.*fail\|cannot find\|not found\|EFI_NOT_FOUND" | head -20
```

---

### Step 3 — Decode Error Codes

#### EWL / IPSD / RC Fatal

Run the full decoder on extracted serconsole text:
```bash
grep "serconsole.con>" "$TBLOG" | sed 's/.*serconsole\.con> //' \
  | python3 .github/skills/bios-issue-analyzer/scripts/decode_ewl.py --log /dev/stdin
```

For a single code:
```bash
python3 .github/skills/bios-issue-analyzer/scripts/decode_ewl.py --code 0x0A --minor 0x05
```

#### POST Code Decode

```bash
python3 .github/skills/bios-issue-analyzer/scripts/decode_post_code.py 0x73
# or: decode_post_code.py PC-73
```

#### BIOS Assertions

If `ASSERT_EFI_ERROR` or `ASSERT ~/OKS/...` appears in the serconsole output:

1. Extract the assertion line:
   ```bash
   grep "serconsole.con>" "$TBLOG" | grep -i "ASSERT"
   ```

2. Map the EFI_STATUS string to a failure category using
   `references/assertion-reference.md`:
   - `Volume Corrupt` → FV hash mismatch / flash corruption
   - `Unsupported` → Feature disabled or NEM exhausted
   - `Out of Resources` → Memory/HOB allocation failed
   - `Not Found` → Missing PPI/Protocol/HOB
   - `Device Error` → Hardware not responding
   - `Security Violation` → Secure boot / measurement failure

3. For source-level tracing (if `~/OKS` BIOS source is available):
   ```bash
   # Verify OKS exists
   test -d ~/OKS || echo "MISSING_OKS — cannot do source tracing"
   # Search for the asserted file
   rg -n "AssertFunctionName\|<error_string>" ~/OKS/ --type c | head -10
   ```

---

### Step 4 — Map to HSLE BIOS Failure Signatures

| Signature | Stage | Root Cause | Fix / Action |
|-----------|-------|------------|--------------|
| EWL `0x08/0x*` (WARN_MEMORY_TRAINING_*) in PEI | 6 | MRC memory training warning — DDR init issue | Check `mrc_mem_flows` bitmask; verify DFI xtors active |
| EWL `0x0A` (WARN_USER_DIMM_DISABLE) | 6 | A DIMM is disabled due to POR violation | Check DIMM population / LRDIMM vs RDIMM config |
| RC Fatal `0xCD/0x2C` (MEMORY_DECODE) | 6 | Fatal memory decode error — BIOS hangs post-MRC | Check DDR config, DFI xtor connectivity in log |
| `ASSERT_EFI_ERROR (Status = Volume Corrupt)` | 6 | OBB hash mismatch — BIOS flash image corrupted | Re-flash IFWI; verify `bios_image` path is correct |
| `ASSERT_EFI_ERROR (Status = Unsupported)` in `WriteFspNvs` | 6 | NEM (Non-Eviction Mode) cache exhausted | Reduce PEI memory consumers or increase NEM size |
| `ASSERT_EFI_ERROR (Status = Out of Resources)` | 6 | HOB pool exhausted | Check HOB pool size, reduce feature load |
| `ASSERT_EFI_ERROR (Status = Not Found)` in early PEI | 6 | Missing PPI dependency — driver load order issue | Check PEI module DEPEX against installed PPIs |
| No serconsole BIOS output at all after IDI Mux | 6 | BIOS fetch failure — VP not executing IFWI | Check `bios_fetchor_control` param, IFWI path |
| BIOS stops mid-DXE (after `DXE IPL Entry`, before `ExitBootServices`) | 6 | DXE driver hang — CXL, PCIe, or NVMe stall | Disable suspect DXE driver; check PCIe/CXL xtor config |
| MRC warnings then hang in PEI (many `WARN_MEMORY_TRAINING`) | 6 | Memory training failure — DDR training stall | Check DFI xtors, reduce DDR frequency in config |
| BIOS reaches `ExitBootServices` but kernel never appears | 7 | Boot device not found or BDS timeout | Check NVMe/SATA config, boot device enumeration |

---

### Step 5 — Produce BIOS Issue Analysis Summary

Output this structure after completing Steps 1–4:

```
BIOS Issue Analysis Summary
============================
Run/Log       : <run_dir or log path>
Analysis Mode : <HSLE Stage 6/7 drill-down | Standalone BIOS log>
BIOS Output   : <First / Last serconsole line and line numbers from testbench.log>

Error Types Found:
  EWL errors      : <count> unique codes (<total> occurrences)
  IPSD errors     : <count>
  RC Fatal errors : <count>
  Assertions      : <list of ASSERT_EFI_ERROR / ASSERT lines, or "None">
  POST code hang  : <last known checkpoint code, or "Not identified">

Top Errors:
  #1  <major>/<minor> — <name> — <occurrences>x — Sockets: <S0,S1...>
      Desc: <description>
      HW:  <Socket N, Channel N, DIMM N, Rank N> (if applicable)
  #2  ...

Assertions:
  File    : <~/OKS/path/file.c> (<line>)
  Status  : <EFI_STATUS string>
  Category: <failure category from assertion-reference.md>

BIOS Phase at Failure:
  Last milestone : <PEI memory installed / DXE IPL Entry / ExitBootServices / other>
  Last console   : "<last serconsole.con> text>"

Failure Signature : <matching known signature, or "New — describe">
Root Cause        : <concise analysis with log evidence>
Recommendations   : <specific, actionable steps>

Next Steps (offer these options):
  1. Trace BIOS source deeper → bios-source-analysis (requires ~/OKS)
  2. Author a patch → bios-patch-author (requires ~/OKS)
  3. Return to HSLE run summary → hsle-run-debugger Step 6
```

---

## Quick Reference: BIOS Boot Phases in HSLE

| Phase | Log Marker | Notes |
|-------|-----------|-------|
| SEC | (no serconsole) | Before UART enabled |
| PEI start | `PeiCore` or first `serconsole.con>` output | Memory Reference Code runs here |
| PEI memory | `PeiServicesInstallPeiMemory` | MRC complete, DDR trained |
| DXE entry | `DXE IPL Entry` | DXE phase starts |
| DXE dispatch | Individual DXE driver names | CXL, PCIe, NVMe drivers load here |
| BDS | `BdsEntry` or `Boot Manager` | Boot device selection |
| ExitBootServices | `ExitBootServicesEntry` | BIOS→OS handoff |

---

## Boundaries

- EWL/IPSD/RC Fatal decoding requires only the script databases — no external source needed.
- BIOS assertion **source tracing** requires `~/OKS` BIOS source tree.
- This skill does NOT decode MCA errors (use `mca-log-analyzer`) or kernel panics.
- POST code decoding covers Diamond Rapids (DMR) codes only.
- For Stage 5 (reset phase) failures, return to `hsle-run-debugger`.
