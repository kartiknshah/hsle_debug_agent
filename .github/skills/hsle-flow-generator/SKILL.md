```skill
---
name: hsle-flow-generator
description: >
  Generates the golden HSLE execution flow from authoritative Intel spec and wiki sources.
  Uses Co-Design specs/wikis and GENI MCPs to derive each stage architecturally, maps CBB
  feature HSDs to the correct flow positions, and identifies known blocking sightings per
  stage. Produces flow.txt (ASCII tree, backward-compatible) and flow.md (Markdown with
  mermaid diagrams, HSD tables, validation report).
  The current flow.txt is used only as a sanity-check diff — NOT as a template or input.
  Supports three modes: generate (fresh from spec), validate (diff only), update (merge).
---

# HSLE Flow Generator

**Purpose**: Build the DMR MCP ICI HSLE golden execution flow from Intel architectural
specifications, wikis, CBB feature HSDs, and known-issue sightings. The flow represents
what the architecture *defines* must happen — not what a single `testbench.log` observed.

## When to Use

- A new stepping, die revision, or topology change requires an updated golden flow
- The team wants to validate whether the current `flow.txt` still matches spec reality
- Onboarding a new platform configuration (change `config.yaml`, re-run)
- Adding new HSLE capabilities (UCIe Gen2, new fmod modes, etc.) to the known flow

## Inputs

| Parameter | Source | Default |
|-----------|--------|---------|
| `config.yaml` | `.github/skills/hsle-flow-generator/config.yaml` | DMR MCP ICI |
| `queries.yaml` | `.github/skills/hsle-flow-generator/queries.yaml` | All 9 stages |
| `mode` | User invocation | `generate` |
| `stages` | config.yaml or user override | `all` |

## Modes

| Mode | What it does |
|------|-------------|
| `generate` | Full spec-to-flow pipeline → writes new `flow.txt` + `flow.md` |
| `validate` | Runs Phase A–C + diff only → writes `flow_validation_report.md`, no edits to flow files |
| `update` | Runs full pipeline, merges new spec/HSD findings into existing flow files |

---

## MANDATORY GUARDRAILS

### MUST
1. **Always call `mcp_co-design_codesign-get-spec-sources` first** — never assume project IDs.
2. **Derive stage definitions from spec queries, not from reading flow.txt.** Starting from
   existing flow introduces anchoring bias.
3. **Use flow.txt only in Phase D (Step 10) as a sanity-check diff.** It is a secondary
   input, not the source of truth.
4. **Map CBB feature HSDs explicitly** — each stage section must list which CBB features
   are architecturally active there.
5. **Cite every spec claim** — record the project_id + section/page for every architectural
   statement in the output.
6. **Run all five phases in order: A → B → C → D → E.**

### MUST NOT
1. Never copy text from flow.txt into a new flow — extract from spec first.
2. Never infer stage boundaries from a single log file.
3. Never guess HSD IDs — only include HSDs returned by MCP tool calls.
4. Never skip Phase B (HSD mapping) — a flow without feature/sighting alignment is incomplete.

---

## Procedure

### PHASE A: Architectural Discovery (Spec-First)

#### Step A1 — Resolve Spec Project IDs

```
Call: mcp_co-design_codesign-get-spec-sources
```

From the returned project list, identify all projects relevant to DMR MCP ICI HSLE:
- Look for: `DMR_CEP`, `DMR_CBB`, `DMR_SOC`, any HSLE or emulation wiki project
- Store validated IDs as `{{PROJECT_IDS}}`
- If `config.yaml` specifies `project_ids: []` (auto), use all identified IDs
- If `config.yaml` specifies an explicit list, validate each against the source list

> If `mcp_co-design_codesign-get-spec-sources` returns no HSLE-relevant projects,
> note this in the output and continue with the broadest available DMR project set.

---

#### Step A2 — Top-Level Execution Model Query

```
Call: mcp_co-design_codesign-ask-specs-and-wikis
Project IDs: {{PROJECT_IDS}}
Query (from queries.yaml: stage_model_query):
  "What is the complete and ordered execution sequence for a DMR MCP multi-die ICI HSLE
   emulation run on ZeBu ZSE5 hardware?
   The configuration is: MCP 1-Socket, 2 IMH dies (imh8 + imh9), 4 CBB dies, UCIe fullstack ICI.
   List all major execution phases from SPARK framework initialization through OS boot
   and test termination, in architectural order. For each phase specify:
   1. The architectural name and role of that phase
   2. What hardware or firmware subsystem drives it
   3. Its defined entry trigger (what must be true for it to start)
   4. Its defined exit/completion condition
   5. Any ordering constraint relative to other phases (must precede / must follow)
   Include: hardware reset sequencing, firmware loading order (S3M, primecode, pcode/acode),
   inter-die link initialization (UCIe, ICI, D2D), BIOS UEFI phases, and OS handoff."
```

Parse the response into a **spec-derived stage list** `{{SPEC_STAGES}}`.
Each entry:
```json
{
  "stage_id": "auto-numbered or spec-named",
  "name": "...",
  "driver": "SPARK | Simics | RTL | BIOS | OS | ...",
  "entry_condition": "...",
  "exit_condition": "...",
  "spec_source": "project_id + section reference"
}
```

> If the spec returns fewer than 5 phases, the query scope may be too narrow.
> Re-run with a broader project set before proceeding.

---

#### Step A3 — Per-Stage Deep Dive

For **each stage** in `{{SPEC_STAGES}}`, call:

```
Call: mcp_co-design_codesign-ask-specs-and-wikis
Project IDs: {{PROJECT_IDS}}
Query (from queries.yaml: stage_deep_dive_template, substituting {{STAGE_NAME}}):
  "For the {{STAGE_NAME}} phase of DMR MCP ICI HSLE emulation:
   1. What are the architectural prerequisites (what prior phases must have completed)?
   2. What is the exact entry trigger or enabling condition?
   3. Which hardware and firmware components are active or transitioning in this phase?
      Include: IMH, CBB, UCIe, ICI, S3M, primecode, pcode, BIOS, OS as applicable.
   4. What inter-die communication occurs (UCIe link states, ICI transactions, D2D protocol)?
   5. What constitutes a successful completion of this phase (exit criteria)?
   6. What are the spec-defined error conditions, timeout thresholds, or failure modes?
   7. What diagnostic registers or observable signals indicate the phase state?"
```

Store per-stage results in `{{STAGE_SPECS[N]}}`.

---

#### Step A4 — BIOS Boot Architecture Deep Dive

This is always a separate query regardless of what A2 returned, because BIOS boot
has its own multi-layer spec:

```
Call: mcp_co-design_codesign-ask-specs-and-wikis
Project IDs: {{PROJECT_IDS}}
Query (from queries.yaml: bios_architecture_query):
  "What is the DMR OakStream UEFI BIOS boot architecture from hardware reset vector
   (0xFFFFFFF0) through ExitBootServices and OS handoff?
   Provide the complete ordered sequence of BIOS phases including:
   1. SEC (Security) phase: CAR setup, reset vector execution, what POST codes are emitted
   2. PEI pre-memory phase: what PEIMs load, what hardware init occurs, DDR5 detection
   3. FSP-M / MRC phase: what DDR5 initialization steps run, what is bypassed in fmod mode
   4. Post-memory PEI phase: what happens after PeiInstallPeiMemory
   5. DXE phase: CORE load, driver dispatch order, NVMe / storage enumeration
   6. BDS phase: boot device selection, GRUB handoff
   7. OS handoff: ExitBootServices, kernel decompression, Linux boot
   For each phase: entry condition, exit condition, what I/O port 0x80 POST codes are
   defined as checkpoints, and what the UART serconsole output pattern looks like.
   Note any behavior differences when MRC/DDR5 training is bypassed (fmod mode)."
```

Store in `{{BIOS_STAGE_SPEC}}`. This replaces or enriches the BIOS stage(s) from A3.

---

#### Step A5 — Reset Phase Architecture

```
Call: mcp_co-design_codesign-ask-specs-and-wikis
Project IDs: {{PROJECT_IDS}}
Query (from queries.yaml: reset_phase_query):
  "What is the DMR MCP hardware reset sequencing architecture for a 2-IMH 4-CBB
   ICI topology on ZeBu ZSE5?
   Define each RESET_PHASE step (RESET_PHASE_1 through RESET_PHASE_6 or however many
   are architecturally defined):
   1. What hardware blocks are released or initialized at each phase?
   2. What signals gate the transition to the next phase?
   3. What is the role of the hybrid switch point and at which phase does it occur?
   4. What is the architectural relationship between IMH reset phases and CBB reset phases?
   5. What UCLOCK ungating, IDI MUX enabling, and VP core enabling steps are ordered by the spec?
   6. What are the completion conditions signaling RESET_PHASE_6_INFRA complete?"
```

Store in `{{RESET_PHASE_SPEC}}`.

---

### PHASE B: Feature HSD Mapping

For each stage N in `{{SPEC_STAGES}}`, run both sub-steps:

#### Step B1 — CBB Feature HSD Discovery per Stage

```
Call: mcp_co-design_codesign-ask-hsd-agent
  tenant: server_platf.bug   (or use hsd_tenants.features from config.yaml)
  query: "DMR CBB {{STAGE_NAME}} feature: [use stage-specific keyword set from queries.yaml]"
  invocation_purpose: "Map CBB feature HSDs defining capabilities active at HSLE {{STAGE_NAME}}"
```

Simultaneously call:
```
Call: mcp_intel_geni_pr_HSDIndexTool
  Family: DMR
  Keywords: "CBB {{STAGE_KEYWORD}} feature definition HSLE"
```

For each returned HSD, record:
```json
{
  "hsd_id": "...",
  "title": "...",
  "status": "open | resolved | closed",
  "stage_relevance": "what capability this feature defines at this stage",
  "spec_source": "cited from HSD or spec query"
}
```

---

#### Step B2 — Blocking Sightings per Stage

```
Call: mcp_co-design_codesign-ask-hsd-agent
  tenant: sighting_central.sighting
  query: "DMR MCP ICI HSLE {{STAGE_NAME}} failure hang timeout block"
  invocation_purpose: "Identify known sightings that block HSLE at {{STAGE_NAME}}"
```

Simultaneously call:
```
Call: mcp_intel_geni_pr_HSDTool
  SQL-style: SELECT id, title, status, component, resolution
             FROM sighting_central.sighting
             WHERE platform LIKE '%DMR%'
               AND (title LIKE '%HSLE%' OR component LIKE '%HSLE%')
               AND title LIKE '%{{STAGE_KEYWORD}}%'
             LIMIT 20
```

For each returned sighting, record:
```json
{
  "hsd_id": "...",
  "title": "...",
  "status": "...",
  "failure_signature": "what observable failure pattern this represents",
  "workaround": "if known",
  "stage_impact": "which exact phase is blocked"
}
```

---

### PHASE C: Debug Indicator Discovery

For each stage N, call:

```
Call: mcp_intel_geni_pr_DebugAssistantAgentTool
  Query:
    "For DMR MCP ICI HSLE emulation at the {{STAGE_NAME}} phase:
     1. What are the observable indicators in testbench.log that this phase has
        completed successfully? Include specific log message patterns, prefixes,
        signal names, or POST code values.
     2. What are the observable indicators that this phase has failed or timed out?
     3. What is the recommended first debug step when this phase does not complete?
     4. What log streams are active during this phase
        (serconsole / debug_port / emu.engine / emu.devices / RTL stdout)?
     5. Are there specific grep patterns a validation engineer would use to confirm
        this stage passed?"
```

Store per-stage in `{{DEBUG_INDICATORS[N]}}`:
```json
{
  "pass_markers": ["pattern1", "pattern2"],
  "fail_markers": ["error_pattern1", "timeout_pattern"],
  "active_log_streams": ["serconsole", "debug_port", "emu.engine"],
  "grep_commands": ["grep 'pattern' testbench.log"],
  "first_debug_step": "..."
}
```

---

### PHASE D: Assembly and Cross-Validation

#### Step D1 — Assemble Spec-Derived Flow

Combine `{{SPEC_STAGES}}`, `{{STAGE_SPECS[N]}}`, `{{BIOS_STAGE_SPEC}}`,
`{{RESET_PHASE_SPEC}}`, `{{STAGE_FEATURE_HSDS[N]}}`, `{{STAGE_SIGHTINGS[N]}}`,
and `{{DEBUG_INDICATORS[N]}}` into a unified per-stage data structure:

```
STAGE N: {{name}}
════════════════════════════════════════════════════════
Spec Source      : {{spec_source (project_id + section)}}
Driver           : {{hardware/firmware driver}}
Entry Condition  : {{spec-defined entry trigger}}
Exit Condition   : {{spec-defined completion criteria}}
────────────────────────────────────────────────────────
Architecture:
  {{ordered list of architectural steps from A3 query}}

CBB Features Active (from Phase B):
  HSD {{id}} : {{title}} — {{what capability defines this stage}}
  HSD {{id}} : {{title}} — ...

Known Blocking Sightings (from Phase B):
  HSD {{id}} : {{title}} — {{failure signature}} — Status: {{open/resolved}}

Debug Indicators (from Phase C):
  PASS markers : {{pass_markers}}
  FAIL markers : {{fail_markers}}
  Log streams  : {{active_log_streams}}
  Key greps    : {{grep_commands}}
════════════════════════════════════════════════════════
```

---

#### Step D2 — Cross-Validate Against Existing flow.txt

Read the existing `.github/skills/hsle-run-debugger/flow.txt`.

For each stage in the spec-derived flow, compare against the corresponding section
in flow.txt and classify each item:

| Classification | Meaning |
|----------------|---------|
| `SPEC_AND_LOG_ALIGNED` | Item appears in both spec query results AND flow.txt |
| `SPEC_ONLY` | Item is in spec query results but missing from flow.txt — **add to new flow** |
| `LOG_ONLY` | Item is in flow.txt but has no spec backing — **flag in validation report** |
| `SPEC_CONFLICT` | flow.txt describes it differently than spec — **spec wins, note the conflict** |

Produce `{{VALIDATION_DIFF}}` — a structured list of all classified items.

---

#### Step D3 — Generate Output Files

**File 1: `flow.txt`** (write to `.github/skills/hsle-run-debugger/flow.txt`)
- ASCII tree format, backward-compatible with existing hsle-run-debugger skill
- Replaces: stage headers, architectural steps, key greps
- Appends: CBB feature notes, known sighting signatures
- Header block: includes generation timestamp, spec project IDs cited, mode used

**File 2: `flow.md`** (write to `.github/skills/hsle-run-debugger/flow.md`)
- Full markdown version with:
  - Mermaid sequence diagram (top-level stage flow)
  - Per-stage collapsible sections
  - CBB feature HSD tables (id / title / status / stage relevance)
  - Known blocking sightings table (id / title / failure signature / workaround)
  - Debug indicators table (pass markers / fail markers / key greps)
  - Spec citation: project_id + section for every architectural claim

**File 3: `flow_validation_report.md`** (write to `.github/skills/hsle-flow-generator/flow_validation_report.md`)
- Summary of `{{VALIDATION_DIFF}}`
- Counts: N spec-aligned, N spec-only (new), N log-only (unvalidated), N conflicts
- Full item list with classifications

> In `validate` mode: generate only File 3, do not modify flow.txt or flow.md.
> In `update` mode: merge new findings into existing files, preserve LOG_ONLY items
>   (annotated as "Not yet spec-validated").

---

### PHASE E: Parameterization

The skill reads from `config.yaml` for all platform-specific values:
- `platform`, `topology`, `dies`, `interconnect`, `hsle_engine`, `fmod_enabled`
- `project_ids` (auto-discover if empty)
- `hsd_tenants` (feature + sighting tenants)
- `stages` (subset of stages to process)
- `output_format`

When `platform` is not `DMR`, adjust all queries to substitute the correct platform
name and relevant spec project IDs discovered in Step A1.

Stage-specific query text is stored in `queries.yaml` as templates with
`{{STAGE_NAME}}`, `{{STAGE_KEYWORD}}`, `{{PLATFORM}}` substitution tokens.

---

## Output Summary

On completion, emit a structured summary:

```
HSLE Flow Generation Summary
=============================
Mode        : generate | validate | update
Platform    : {{platform}} {{topology}}
Spec Sources: {{project_ids used}}
Stages      : {{stages processed}}

Phase A: {{N}} stage specs extracted from {{M}} spec queries
Phase B: {{N}} CBB feature HSDs mapped, {{M}} sighting HSDs identified
Phase C: {{N}} debug indicator sets extracted
Phase D: 
  Spec-aligned items  : {{N}}
  Spec-only (new)     : {{N}}
  Log-only (flagged)  : {{N}}
  Spec conflicts      : {{N}}

Output:
  flow.txt written      : yes | no (validate mode)
  flow.md written       : yes | no (validate mode)
  validation report     : .github/skills/hsle-flow-generator/flow_validation_report.md
```
```
