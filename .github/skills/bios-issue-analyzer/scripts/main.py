#!/usr/bin/env python3
"""
EWL Decoder SKILL - GitHub Copilot SKILL for decoding BIOS error codes

Commands:
  decode <major> [minor] - Decode a single error code
  analyze-log <text>     - Analyze full BIOS log
  help                   - Show usage information
"""

import sys
import os
from pathlib import Path

# Import decoder from same directory
from decode_ewl import EWLDecoder


def cmd_decode(args):
    """Handle /decode command."""
    if not args:
        print("Usage: decode <major_code> [minor_code]")
        print("Example: decode 0x29 0x15")
        return
    
    major = args[0].upper()
    if not major.startswith('0X'):
        major = f"0X{int(major, 0):X}"
    
    minor = None
    if len(args) > 1:
        minor = args[1].upper()
        if not minor.startswith('0X'):
            minor = f"0X{int(minor, 0):X}"
    
    # Initialize decoder
    skill_dir = Path(__file__).parent
    decoder = EWLDecoder(
        db_path=os.path.join(skill_dir, 'ewl_codes_database.json')
    )
    
    # Decode
    result = decoder.decode_code(major, minor)
    
    # Format for Copilot display
    output = ["## Decoded Error Code\n\n"]
    output.append(f"**Code:** `{result['major_code']}`")
    if result['minor_code']:
        output.append(f" / `{result['minor_code']}`")
    output.append("\n\n")
    
    if result['major_name']:
        output.append(f"**Name:** {result['major_name']}")
        if result['minor_name']:
            output.append(f" / {result['minor_name']}")
        output.append("\n\n")
    
    if result['major_desc']:
        output.append(f"**Description:** {result['major_desc']}")
        if result['minor_desc']:
            output.append(f" / {result['minor_desc']}")
        output.append("\n\n")
    
    if not result['major_name']:
        output.append("\n**Status:** Code not found in database\n")
    
    print(''.join(output))


def cmd_analyze_log(args):
    """Handle /analyze-log command."""
    if not args:
        # Read from stdin
        if not sys.stdin.isatty():
            log_text = sys.stdin.read()
        else:
            print("Usage: analyze-log <log_text>")
            print("Or pipe log content via stdin")
            return
    else:
        log_text = ' '.join(args)
    
    # Initialize decoder
    skill_dir = Path(__file__).parent
    decoder = EWLDecoder(
        db_path=os.path.join(skill_dir, 'ewl_codes_database.json')
    )
    
    # Parse and decode
    codes = decoder.parse_log(log_text)
    summary = decoder.generate_summary(codes)
    
    print(summary)


def cmd_help():
    """Show help information."""
    help_text = """
# EWL Decoder SKILL

Decode BIOS Enhanced Warning Log (EWL) error codes.

## Commands

### decode <major> [minor]
Decode a single error code.

Examples:
  decode 0x29 0x15
  decode 29 15

### analyze-log <text>
Analyze a full BIOS log and decode all error codes.

Examples:
  analyze-log "Enhanced warning of type 2 logged:Major Warning Code = 0x29, Minor Warning Code = 0x15,"
  cat bios.log | analyze-log

### help
Show this help message.

## Database
- 124 major codes
- 412 minor codes
- From Intel firmware header file (EnhancedWarningLogLib.h)

## Coverage
Memory, CPU, UPI/KTI, PCIe, CXL, NVDIMM, ME, and more
"""
    print(help_text)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(1)
    
    command = sys.argv[1].lower()
    args = sys.argv[2:] if len(sys.argv) > 2 else []
    
    if command == 'decode':
        cmd_decode(args)
    elif command == 'analyze-log':
        cmd_analyze_log(args)
    elif command == 'help':
        cmd_help()
    else:
        print(f"Unknown command: {command}")
        print("Use 'help' for usage information")
        sys.exit(1)


if __name__ == '__main__':
    main()
