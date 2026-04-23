"""
Shared utilities for DAIV skill decoders.

Common functions used across bios-log-analyzer, mca-log-analyzer,
and bios-post-code-decoder to avoid code duplication.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def load_json_database(path: str | Path, default=None):
    """Load a JSON database file with consistent error handling.

    Args:
        path: Path to the JSON file.
        default: Value to return if loading fails (None, {}, [], etc.).

    Returns:
        Parsed JSON data, or *default* on failure.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Warning: Could not load {path}: {exc}", file=sys.stderr)
        return default


def resolve_db_path(filename: str, caller_file: str) -> Path:
    """Resolve a database filename relative to the calling script's directory.

    Args:
        filename: Database filename (e.g. ``"ewl_codes_database.json"``).
        caller_file: Pass ``__file__`` from the calling module.

    Returns:
        Absolute path to the database file.
    """
    return Path(caller_file).parent / filename


def normalize_hex_code(value: str) -> str | None:
    """Normalize various hex string formats to ``0xXX`` (uppercase, zero-padded).

    Handles: ``"73"``, ``"0x73"``, ``"073h"``, ``"73h"``, ``"PC-73"``,
    ``"post code 0x73"``, and wider values like ``"0x0400"``.

    Returns:
        Normalized string like ``"0x73"`` or ``"0x0400"``, or *None* if
        the input cannot be parsed.
    """
    cleaned = value.strip().upper()
    # Strip common prefixes (PC-, POST CODE, etc.)
    cleaned = re.sub(r"^(PC[-_\s]|POST\s*CODE\s*)", "", cleaned, flags=re.IGNORECASE).strip()

    # 0xHEX
    m = re.match(r"^0X([0-9A-F]+)$", cleaned, re.IGNORECASE)
    if m:
        digits = m.group(1)
        padded = digits.zfill(2) if len(digits) <= 2 else digits
        return f"0x{padded.upper()}"

    # HEXh suffix
    m = re.match(r"^([0-9A-F]+)H$", cleaned, re.IGNORECASE)
    if m:
        digits = m.group(1)
        padded = digits.zfill(2) if len(digits) <= 2 else digits
        return f"0x{padded.upper()}"

    # Bare hex digits
    m = re.match(r"^([0-9A-F]+)$", cleaned, re.IGNORECASE)
    if m:
        digits = m.group(1)
        padded = digits.zfill(2) if len(digits) <= 2 else digits
        return f"0x{padded.upper()}"

    return None


def parse_hex_value(value: str | int | None) -> int | None:
    """Parse a hex string or int into an integer.

    Handles ``"0x1234"``, ``"1234"``, ``"1234h"``, and bare integers.

    Returns:
        Integer value, or *None* if parsing fails.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Remove 0x prefix or h suffix
    cleaned = text.lower().replace("0x", "").rstrip("h")
    if not cleaned:
        return None
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


def normalize_text(value: str | None) -> str | None:
    """Replace common Unicode artifacts with ASCII equivalents.

    Useful when processing data copied from Excel, Word, or web pages.
    """
    if value is None:
        return None
    text = str(value).strip()
    replacements = {
        "\u00a0": " ",   # non-breaking space
        "\u2019": "'",   # right single quote
        "\u2018": "'",   # left single quote
        "\u2013": "-",   # en-dash
        "\u2014": "-",   # em-dash
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
