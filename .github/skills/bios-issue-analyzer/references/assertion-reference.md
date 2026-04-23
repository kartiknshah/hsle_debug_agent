# BIOS Assertion Reference

Assertion-specific aliases for EFI_STATUS text shown in BIOS logs.
Canonical EFI_STATUS mapping is in:
`../../bios-source-analysis/references/source-reference.md`.

## Assertion Status Text → Failure Category

| Status String | Hex | Category | Common Sources |
|---|---|---|---|
| `Volume Corrupt` | `0x8000000E` | FV hash mismatch / flash corruption | `FvReportPei.c`, OBB verification |
| `Unsupported` | `0x80000003` | Feature disabled or NEM exhausted | `WriteFspNvs()`, PCIe init |
| `Out of Resources` | `0x80000009` | Memory/HOB allocation failed | Any alloc call |
| `Not Found` | `0x80000014` | Missing PPI/Protocol/HOB | Early PEI driver ordering |
| `Device Error` | `0x80000007` | Hardware not responding | SPI, HECI, memory controller |
| `Security Violation` | `0x8000001A` | Secure boot / measurement failure | TrEE, FvReportPei |

## Known Assertion Locations

| File | Line area | Function | Typical Failure |
|------|-----------|----------|----------------|
| `Edk2/SecurityPkg/FvReportPei/FvReportPei.c` | ~440 | `CheckStoredHashFv()` | OBB hash mismatch after flash update |
| `Intel/OakStreamFspPkg/.../PeiFspNvsWriteLib.c` | ~120-225 | `WriteFspNvs()` | NEM shortage or flash size exceeded |
| `Edk2/MdeModulePkg/Core/Dxe/DxeMain.c` | varies | `DxeLoadCore()` | Missing depex or corrupt DXE driver |

## Assertion vs EWL Relationship

- EWL errors (decoded by `bios-log-analyzer`) appear **before** assertions as leading indicators
- Example: EWL `0x8D/0x02` (NVRAM thermal table missing) may precede an `EFI_NOT_FOUND` assertion
- Always correlate bios-log-analyzer output with the assertion timestamp/postcode
