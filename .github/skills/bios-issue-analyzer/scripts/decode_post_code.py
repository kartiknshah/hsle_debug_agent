#!/usr/bin/env python3
"""
BIOS Post Code Decoder - Converts hex post code values to human-readable descriptions.

Supported formats:
  - Single hex code: "73", "0x73", "073h"
  - With text: "PC-73", "post code 0x73"
  - Major/Minor: "73/01", "0x73/0x01"
"""

import json
import re
import os
import sys
from pathlib import Path

# decoder_utils.py lives in the same scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from decoder_utils import load_json_database, normalize_hex_code


def load_database():
    """Load the post code database from JSON."""
    db_path = Path(__file__).parent / "post_codes_database.json"
    return load_json_database(db_path, default={})


def normalize_hex(value):
    """Convert various hex formats to 0xXX format."""
    return normalize_hex_code(value)


def search_in_log(text, db):
    """Search for post codes in log text and decode them."""
    # Pattern: PC-XX, PC XX, post code 0xXX, 0xXX, etc.
    patterns = [
        r'PC-([0-9A-F]{1,2})\b',  # PC-73
        r'PC[:\s]+([0-9A-F]{1,2})\b',  # PC: 73, PC 73
        r'post.?code[:\s]+(0x[0-9A-F]{1,2})\b',  # post code 0x73
        r'\b(0x[0-9A-F]{1,2})\b',  # 0x73
        r'\b([0-9A-F]{1,2})h\b',  # 73h
    ]
    
    # Build searchable database
    all_codes = {}
    if isinstance(db, dict) and "post_codes" in db:
        all_codes.update(db["post_codes"])
        all_codes.update(db.get("acpi_debug_codes", {}))
    else:
        all_codes = db
    
    found_codes = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            hex_code = match.group(1)
            normalized = normalize_hex(hex_code)
            if normalized and normalized in all_codes:
                found_codes.add(normalized)
    
    return sorted(found_codes)


def decode_code(code_str, db):
    """Decode a single post code."""
    normalized = normalize_hex(code_str)
    if not normalized:
        return None
    
    # Check in both post_codes and acpi_debug_codes sections
    code_type = None
    entry = None
    
    if isinstance(db, dict) and "post_codes" in db:
        # New nested format
        if normalized in db["post_codes"]:
            entry = db["post_codes"][normalized]
            code_type = "POST"
        elif normalized in db.get("acpi_debug_codes", {}):
            entry = db["acpi_debug_codes"][normalized]
            code_type = "ACPI"
    else:
        # Old flat format
        if normalized in db:
            entry = db[normalized]
            code_type = "unknown"
    
    if entry:
        return {
            "code": entry["code"],
            "macro": entry.get("macro", ""),
            "description": entry.get("description", ""),
            "type": code_type,
        }
    return None


def format_output(code, entry):
    """Format a decoded post code for display."""
    lines = [f"**Code:** `{code}`"]
    
    # Use the database categorization from decode_code
    if entry.get("type") == "POST":
        lines.append("**Type:** Real POST Code (BIOS Phase)")
    elif entry.get("type") == "ACPI":
        lines.append("**Type:** ACPI/OS Debug Code (Runtime)")
    
    if entry["macro"]:
        lines.append(f"**Macro:** `{entry['macro']}`")
    if entry["description"]:
        lines.append(f"**Description:** {entry['description']}")
    return "\n".join(lines)


def main():
    db = load_database()
    if not db:
        sys.exit(1)
    
    if len(sys.argv) < 2:
        print("Usage: decode_post_code.py <post_code> | --log <file>")
        print("Examples:")
        print("  decode_post_code.py 0x73")
        print("  decode_post_code.py PC-4A")
        print("  decode_post_code.py 73h")
        sys.exit(1)
    
    if sys.argv[1] == "--log" and len(sys.argv) > 2:
        # Analyze a log file
        log_file = sys.argv[2]
        try:
            with open(log_file) as f:
                content = f.read()
        except FileNotFoundError:
            print(f"Error: Log file not found: {log_file}", file=sys.stderr)
            sys.exit(1)
        
        codes = search_in_log(content, db)
        if not codes:
            print("No post codes found in log.")
            sys.exit(0)
        
        print("### Post Codes Found\n")
        for code in codes:
            entry = decode_code(code, db)
            if entry:
                print(format_output(code, entry))
                print()
        
    else:
        # Decode a single code
        code_input = sys.argv[1]
        entry = decode_code(code_input, db)
        
        if entry:
            print(format_output(entry["code"], entry))
        else:
            print(f"Post code not found: {code_input}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
