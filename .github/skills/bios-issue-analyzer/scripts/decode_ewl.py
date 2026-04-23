#!/usr/bin/env python3
"""
EWL Log Decoder - Parse BIOS logs and decode error codes

Extracts Enhanced Warning Log codes from BIOS logs and provides code meanings from EWL spec.

@copyright
INTEL CONFIDENTIAL
Copyright (C) 2026 Intel Corporation.
"""

import re
import json
import sys
import os
from pathlib import Path
from collections import defaultdict

# decoder_utils.py lives in the same scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from decoder_utils import load_json_database, resolve_db_path


class EWLDecoder:
    """Decoder for Enhanced Warning Log codes"""
    
    _IPSD_DB_PATH = Path(__file__).parent / "ipsd_codes_database.json"

    def __init__(self, db_path=None, rc_db_path=None):
        """Initialize decoder with code databases."""
        # Use script directory as default if paths not provided
        script_dir = Path(__file__).parent
        
        if db_path is None:
            db_path = script_dir / 'ewl_codes_database.json'
        else:
            db_path = Path(db_path)
            # If relative path, make it relative to script directory
            if not db_path.is_absolute() and not db_path.exists():
                alt_path = script_dir / db_path
                if alt_path.exists():
                    db_path = alt_path
        
        if rc_db_path is None:
            rc_db_path = script_dir / 'rc_fatal_errors_database.json'
        else:
            rc_db_path = Path(rc_db_path)
            # If relative path, make it relative to script directory
            if not rc_db_path.is_absolute() and not rc_db_path.exists():
                alt_path = script_dir / rc_db_path
                if alt_path.exists():
                    rc_db_path = alt_path
        
        self.db = self.load_database(str(db_path))
        self.rc_db = self.load_database(str(rc_db_path))

        ipsd_raw = load_json_database(self._IPSD_DB_PATH, default={})
        self.IPSD_ERRORS = {int(k, 16): v["description"] for k, v in ipsd_raw.items()} if ipsd_raw else {}

        if self.db:
            print(f"✓ Loaded {len(self.db)} EWL codes from database", file=sys.stderr)
        if self.rc_db:
            total_rc_minors = sum(len(info.get('minors', {})) for info in self.rc_db.values())
            print(f"✓ Loaded {len(self.rc_db)} RC fatal major codes ({total_rc_minors} minors) from database", file=sys.stderr)
    
    def load_database(self, path):
        """Load JSON database file."""
        return load_json_database(path, default=None)
    
    def decode_code(self, major_code, minor_code=None):
        """
        Decode error/warning codes and return information.
        
        Returns dict with: code, name, description
        """
        result = {
            'major_code': major_code,
            'minor_code': minor_code,
            'major_name': None,
            'minor_name': None,
            'major_desc': None,
            'minor_desc': None
        }
        
        # Lookup codes in database (majors at top level, minors nested under majors)
        if self.db:
            major_info = self.db.get(major_code.lower())  # DB uses lowercase
            if major_info and major_info.get('type') == 'major':
                result['major_name'] = major_info.get('name')
                result['major_desc'] = major_info.get('description')
                
                # Look for minor code under this major
                if minor_code:
                    minors = major_info.get('minors', {})
                    minor_info = minors.get(minor_code.lower())
                    if minor_info:
                        result['minor_name'] = minor_info.get('name')
                        result['minor_desc'] = minor_info.get('description')
        
        return result
    
    def decode_rc_fatal_error(self, major_code, minor_code=None):
        """
        Decode RC (Reference Code) fatal error codes.
        
        Args:
            major_code: Major error code (e.g., '0xCD')
            minor_code: Minor error code (e.g., '0x30')
        
        Returns:
            dict with: major_code, minor_code, major_name, minor_name, descriptions, source
        """
        result = {
            'major_code': major_code,
            'minor_code': minor_code,
            'major_name': None,
            'minor_name': None,
            'major_desc': None,
            'minor_desc': None,
            'major_source': None,
            'minor_source': None
        }
        
        # Lookup codes in RC database (majors at top level, minors nested)
        if self.rc_db:
            major_info = self.rc_db.get(major_code.lower())
            if major_info and major_info.get('type') == 'major':
                result['major_name'] = major_info.get('name')
                result['major_desc'] = major_info.get('description')
                result['major_source'] = major_info.get('source')
                
                # Look for minor code under this major
                if minor_code:
                    minors = major_info.get('minors', {})
                    minor_info = minors.get(minor_code.lower())
                    if minor_info:
                        result['minor_name'] = minor_info.get('name')
                        result['minor_desc'] = minor_info.get('description')
                        result['minor_source'] = minor_info.get('source')
        
        return result
    
    def decode_error_code(self, error_code):
        """
        Decode combined error code format (e.g., 0x3000CD2C).
        
        Format appears to be:
        - Bits 31-16: Context/flags (0x3000)
        - Bits 15-8:  Major error code (0xCD)
        - Bits 7-0:   Minor error code (0x2C)
        
        Args:
            error_code: Combined error code as string or integer
        
        Returns:
            dict with: major_code, minor_code, context, decoded_info
        """
        # Convert to integer if string
        if isinstance(error_code, str):
            if error_code.upper().startswith('0X'):
                error_val = int(error_code, 16)
            else:
                error_val = int(error_code, 0)
        else:
            error_val = error_code
        
        # Extract fields
        context = (error_val >> 16) & 0xFFFF
        major = (error_val >> 8) & 0xFF
        minor = error_val & 0xFF

        major_hex = f"0x{major:02X}"
        minor_hex = f"0x{minor:02X}"

        # Decode using RC fatal error decoder
        decoded = self.decode_rc_fatal_error(major_hex, minor_hex)

        return {
            'error_code': f"0x{error_val:08X}",
            'context': f"0x{context:04X}",
            'major_code': major_hex,
            'minor_code': minor_hex,
            'decoded': decoded
        }
    
    def decode_ipsd_error(self, error_code):
        """
        Decode IPSD (Intel Platform Service Provider) error code.
        
        Args:
            error_code: String like "C80000002" or integer
        
        Returns:
            dict with code and description
        """
        if isinstance(error_code, str):
            # Remove 'C' prefix if present
            if error_code.upper().startswith('C'):
                error_code = error_code[1:]
            error_val = int(error_code, 16)
        else:
            error_val = error_code
        
        description = self.IPSD_ERRORS.get(error_val, "Unknown IPSD Error")
        
        return {
            'code': f"0x{error_val:08X}",
            'description': description,
            'type': 'IPSD'
        }
    
    def parse_log(self, log_text):
        """
        Parse log text and extract all error codes with context.

        Returns list of dicts with type in {'EWL', 'IPSD', 'RC_FATAL'} plus
        relevant fields (major, minor, socket, context, file_ref, ...).
        """
        codes = []

        # Pattern 1: "Major Warning Code = 0xNN, Minor Warning Code = 0xNN"
        pattern1 = r'Major Warning Code\s*=\s*(0x[0-9A-Fa-f]+).*?Minor Warning Code\s*=\s*(0x[0-9A-Fa-f]+)'

        # Pattern 2: "Error Logged: Class Code = 0011, Error Code = 0005, Minor Code = 0026"
        # Class Code = major, Minor Code = minor; middle Error Code field is ignored
        pattern2 = r'Error Logged:\s*Class Code\s*=\s*([0-9A-Fa-f]+),\s*Error Code\s*=\s*([0-9A-Fa-f]+),\s*Minor Code\s*=\s*([0-9A-Fa-f]+)'

        # Pattern 3: IPSD errors "ERROR: C8XXXXXXX:..."
        pattern3 = r'ERROR:\s*(C8[0-9A-Fa-f]{7}):([^\s]+)'

        # Pattern 4: "Enhanced warning of type N logged:" (multi-line block)
        pattern4 = r'Enhanced warning of type (\d+) logged:'

        # Pattern 5: RC Fatal multi-line block starting with **FATAL ERROR** or similar
        # Matches: "**FATAL ERROR**", "FATAL ERROR:", "RC_FATAL_ERROR", "FATAL_ERROR!"
        pattern5_trigger = re.compile(
            r'(?:\*\*FATAL ERROR\*\*|FATAL ERROR:|RC_FATAL_ERROR!?|FATAL_ERROR!)',
            re.IGNORECASE
        )

        # Pattern 6: Standalone combined RC Fatal code (exactly 8 hex digits, outside a FATAL ERROR block)
        # Upper 16 bits = context, bits [15:8] = major, bits [7:0] = minor
        # Require exactly 8 digits to avoid false-positive matches on shorter hex values
        pattern6 = re.compile(r'(?:Error Code|RC Fatal Error Code)\s*=\s*(0x[0-9A-Fa-f]{8})\b', re.IGNORECASE)

        lines = log_text.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i]

            # Strip optional timestamp prefix like "[2026-02-05-18:24:53] "
            clean = re.sub(r'^\[[\d\-:]+\]\s*', '', line).strip()

            # Pattern 4 (Enhanced warning blocks) — highest EWL priority
            match = re.search(pattern4, clean)
            if match:
                ewl_type = match.group(1)
                block_data, lines_consumed = self._parse_enhanced_warning_block(lines, i, ewl_type)
                if block_data:
                    codes.append(block_data)
                i += lines_consumed
                continue

            # Pattern 5: RC Fatal block trigger line
            if pattern5_trigger.search(clean):
                block_data, lines_consumed = self._parse_rc_fatal_block(lines, i)
                if block_data:
                    codes.append(block_data)
                i += lines_consumed
                continue

            # Pattern 1: inline EWL codes
            match = re.search(pattern1, clean)
            if match:
                major = "0x" + match.group(1)[2:].upper()
                minor = "0x" + match.group(2)[2:].upper()
                socket_match = re.match(r'S(\d+),', clean)
                socket = socket_match.group(1) if socket_match else None
                codes.append({
                    'type': 'EWL',
                    'major': major,
                    'minor': minor,
                    'socket': socket,
                    'context': line.strip()
                })
                i += 1
                continue

            # Pattern 2: legacy "Error Logged" format
            match = re.search(pattern2, clean)
            if match:
                class_code = match.group(1)
                minor_code = match.group(3)
                major = f"0x{int(class_code, 16):02X}"
                minor = f"0x{int(minor_code, 16):02X}"
                socket_match = re.match(r'S(\d+),', clean)
                socket = socket_match.group(1) if socket_match else None
                context_lines = [lines[j].strip() for j in range(max(0, i-2), min(len(lines), i+3))]
                codes.append({
                    'type': 'EWL',
                    'major': major,
                    'minor': minor,
                    'socket': socket,
                    'context': '\n'.join(context_lines)
                })
                i += 1
                continue

            # Pattern 3: IPSD
            match = re.search(pattern3, clean)
            if match:
                ipsd_code = match.group(1)
                guid = match.group(2)
                context_lines = [lines[j].strip() for j in range(max(0, i-2), min(len(lines), i+3))]
                codes.append({
                    'type': 'IPSD',
                    'ipsd_code': ipsd_code,
                    'guid': guid,
                    'socket': None,
                    'context': '\n'.join(context_lines)
                })
                i += 1
                continue

            # Pattern 6: standalone combined RC Fatal code (outside a FATAL ERROR block)
            match = pattern6.search(clean)
            if match:
                combined = match.group(1)
                decoded = self.decode_error_code(combined)
                if decoded['decoded'].get('major_name'):
                    context_lines = [lines[j].strip() for j in range(max(0, i-2), min(len(lines), i+3))]
                    codes.append({
                        'type': 'RC_FATAL',
                        'major': decoded['major_code'],
                        'minor': decoded['minor_code'],
                        'combined_code': decoded['error_code'],
                        'socket': None,
                        'context': '\n'.join(context_lines)
                    })
                i += 1
                continue

            i += 1

        return codes

    def _parse_rc_fatal_block(self, lines, start_idx):
        """
        Parse a multi-line RC Fatal block starting at the trigger line.

        Recognises formats emitted by Intel BIOS firmware:
          **FATAL ERROR**
          Major Error Code = 0xCD
          Minor Error Code = 0x2C
          Socket = 0

        Also handles single-line forms like:
          RC_FATAL_ERROR! path/file.c: 741   (file-reference only)

        Returns (dict | None, lines_consumed).
        """
        trigger_line = re.sub(r'^\[[\d\-:]+\]\s*', '', lines[start_idx]).strip()

        data = {
            'type': 'RC_FATAL',
            'major': None,
            'minor': None,
            'combined_code': None,
            'socket': None,
            'file_ref': None,
            'context_lines': [trigger_line]
        }

        # Compile pattern5 trigger once for use as stop-condition inside lookahead
        _p5 = re.compile(
            r'(?:\*\*FATAL ERROR\*\*|FATAL ERROR:|RC_FATAL_ERROR!?|FATAL_ERROR!)',
            re.IGNORECASE
        )
        # Exact-8-digit combined code pattern (bits[15:8]=major, bits[7:0]=minor)
        _combined = re.compile(
            r'(?:Error Code|RC Fatal Error Code)\s*=\s*(0x[0-9A-Fa-f]{8})\b',
            re.IGNORECASE
        )

        # Extract file reference from trigger line itself (RC_FATAL_ERROR! file.c: 741)
        file_ref_match = re.search(r'(?:RC_FATAL_ERROR!?|FATAL_ERROR!)\s+(.*?:\s*\d+)', trigger_line, re.IGNORECASE)
        if file_ref_match:
            data['file_ref'] = file_ref_match.group(1).strip()

        # Bug fix #2: also scan the trigger line itself for a combined code
        # (handles single-line form: "RC_FATAL_ERROR! RC Fatal Error Code = 0x3000CD2C")
        m = _combined.search(trigger_line)
        if m:
            decoded = self.decode_error_code(m.group(1))
            data['combined_code'] = decoded['error_code']
            data['major'] = decoded['major_code']
            data['minor'] = decoded['minor_code']

        # Scan up to 20 following lines for code fields
        i = start_idx + 1
        block_end = min(start_idx + 20, len(lines))

        while i < block_end:
            raw = lines[i]
            clean = re.sub(r'^\[[\d\-:]+\]\s*', '', raw).strip()

            # Stop at blank line, next FATAL ERROR block, or other recognised log section
            # Bug fix #1: stop at another FATAL ERROR trigger so adjacent blocks are not merged
            if not clean:
                break
            if _p5.search(clean) or re.search(r'Enhanced warning of type|ERROR:\s*C8', clean):
                break

            data['context_lines'].append(clean)

            # Major Error Code = 0xXX
            m = re.search(r'Major\s+(?:Error|Warning)\s+Code\s*=\s*(0x[0-9A-Fa-f]+)', clean, re.IGNORECASE)
            if m:
                data['major'] = "0x" + m.group(1)[2:].upper()

            # Minor Error Code = 0xXX
            m = re.search(r'Minor\s+(?:Error|Warning)\s+Code\s*=\s*(0x[0-9A-Fa-f]+)', clean, re.IGNORECASE)
            if m:
                data['minor'] = "0x" + m.group(1)[2:].upper()

            # Combined code (exactly 8 hex digits to avoid false positives)
            m = _combined.search(clean)
            if m:
                decoded = self.decode_error_code(m.group(1))
                data['combined_code'] = decoded['error_code']
                if not data['major']:
                    data['major'] = decoded['major_code']
                if not data['minor']:
                    data['minor'] = decoded['minor_code']

            # Socket
            m = re.search(r'^Socket\s*=?\s*(\d+)', clean, re.IGNORECASE)
            if m:
                data['socket'] = m.group(1)

            i += 1

        lines_consumed = i - start_idx
        data['context'] = '\n'.join(data['context_lines'][:15])

        # Accept the block if we have at least a major code OR a file reference
        if data['major'] or data['file_ref']:
            return data, lines_consumed

        return None, lines_consumed
    
    def _parse_enhanced_warning_block(self, lines, start_idx, ewl_type):
        """
        Parse multi-line Enhanced warning block starting from the marker line.
        
        Format example:
            Enhanced warning of type 1 logged:
            Major Warning Code = 0x0A, Minor Warning Code = 0x10,
            Major Checkpoint: 0xB7
            Minor Checkpoint: 0x51
            Socket 0
            Channel 15
            Dimm 0
            Rank 1
        
        Returns tuple: (dict with parsed data or None if invalid, lines_consumed)
        """
        data = {
            'type': 'EWL',
            'ewl_type': ewl_type,
            'major': None,
            'minor': None,
            'socket': None,
            'channel': None,
            'dimm': None,
            'rank': None,
            'major_checkpoint': None,
            'minor_checkpoint': None,
            'context_lines': []
        }
        
        # Capture the marker line
        marker_line = lines[start_idx].strip()
        data['context_lines'].append(marker_line)
        
        # Parse next 15 lines max (Enhanced warning blocks are typically 5-15 lines)
        i = start_idx + 1
        block_end = min(start_idx + 20, len(lines))
        
        while i < block_end:
            line = lines[i]
            
            # Strip timestamp prefix like "[2026-02-05-18:24:53] "
            clean_line = re.sub(r'^\[[\d\-:]+\]\s*', '', line).strip()
            
            # Stop at empty line or next "Enhanced warning" marker or next timestamp section
            if not clean_line or 'Enhanced warning of type' in clean_line:
                break
            
            # Stop at MMC handler markers (indicates end of block)
            if 'MMC[' in clean_line and 'MmcHostAppDataQueueHandler' in clean_line:
                break
            
            # Stop if we hit another log entry (starts with Node prefix or other markers)
            if clean_line and not clean_line.startswith('>>>'):
                # Check if it's a different log entry (not a field we're parsing)
                if re.match(r'^N\d+\.(M\d+\.)?C\d+\.D\d+:', clean_line):
                    # This is a context line, not a field - we can include it but should stop soon
                    data['context_lines'].append(line.strip())
                    i += 1
                    break
            
            # Skip lines with ">>>>>>>" prefix (MMC output format - strip it)
            clean_line = clean_line.lstrip('> ')
            
            # Parse Major/Minor Warning Code (can be on same line or separate)
            major_match = re.search(r'Major Warning Code\s*=\s*(0x[0-9A-Fa-f]+)', clean_line)
            if major_match:
                data['major'] = "0x" + major_match.group(1)[2:].upper()

            minor_match = re.search(r'Minor Warning Code\s*=\s*(0x[0-9A-Fa-f]+)', clean_line)
            if minor_match:
                data['minor'] = "0x" + minor_match.group(1)[2:].upper()

            # Parse checkpoints
            if 'Major Checkpoint:' in clean_line:
                cp_match = re.search(r'Major Checkpoint:\s*(0x[0-9A-Fa-f]+)', clean_line)
                if cp_match:
                    data['major_checkpoint'] = "0x" + cp_match.group(1)[2:].upper()

            if 'Minor Checkpoint:' in clean_line:
                cp_match = re.search(r'Minor Checkpoint:\s*(0x[0-9A-Fa-f]+)', clean_line)
                if cp_match:
                    data['minor_checkpoint'] = "0x" + cp_match.group(1)[2:].upper()
            
            # Parse topology fields - must match line start (after cleaning)
            if re.match(r'^Socket\s+\d+', clean_line):
                socket_match = re.search(r'Socket\s+(\d+)', clean_line)
                if socket_match:
                    data['socket'] = socket_match.group(1)
            
            if re.match(r'^Channel\s+\d+', clean_line):
                channel_match = re.search(r'Channel\s+(\d+)', clean_line)
                if channel_match:
                    data['channel'] = channel_match.group(1)
            
            if re.match(r'^Dimm\s+\d+', clean_line):
                dimm_match = re.search(r'Dimm\s+(\d+)', clean_line)
                if dimm_match:
                    data['dimm'] = dimm_match.group(1)
            
            if re.match(r'^Rank\s+\d+', clean_line):
                rank_match = re.search(r'Rank\s+(\d+)', clean_line)
                if rank_match:
                    data['rank'] = rank_match.group(1)
            
            # Parse Type 2 specific fields (Strobe, Level, Group, Eyesize)
            if 'Strobe:' in clean_line:
                strobe_match = re.search(r'Strobe:\s+(\d+)', clean_line)
                if strobe_match:
                    data['strobe'] = strobe_match.group(1)
            
            if 'Level:' in clean_line:
                level_match = re.search(r'Level:\s+(\w+)', clean_line)
                if level_match:
                    data['level'] = level_match.group(1)
            
            if 'Group:' in clean_line:
                group_match = re.search(r'Group:\s+(\w+)', clean_line)
                if group_match:
                    data['group'] = group_match.group(1)
            
            if 'Eyesize' in clean_line:
                eyesize_match = re.search(r'Eyesize\s+(\d+)', clean_line)
                if eyesize_match:
                    data['eyesize'] = eyesize_match.group(1)
            
            data['context_lines'].append(line.strip())
            i += 1
        
        # Calculate lines consumed (including the marker line)
        lines_consumed = i - start_idx
        
        # Build context string
        data['context'] = '\n'.join(data['context_lines'][:15])  # Limit context
        
        # Only return data if we found major/minor codes
        if data['major'] and data['minor']:
            return data, lines_consumed
        
        return None, lines_consumed
    
    def generate_summary(self, codes):
        """
        Generate a summary report from list of codes.
        
        Args:
            codes: List of dicts with major, minor, socket, context, type keys
        
        Returns:
            String containing formatted summary report
        """
        if not codes:
            return "No error codes found in log.\n"

        # Separate by type
        ewl_codes = [c for c in codes if c.get('type') == 'EWL']
        ipsd_codes = [c for c in codes if c.get('type') == 'IPSD']
        rc_fatal_codes = [c for c in codes if c.get('type') == 'RC_FATAL']

        summary = []
        summary.append("## BIOS Error Code Analysis\n\n")
        summary.append(f"**Total error codes found:** {len(codes)}\n")
        summary.append(f"- EWL errors: {len(ewl_codes)}\n")
        summary.append(f"- IPSD errors: {len(ipsd_codes)}\n")
        summary.append(f"- RC Fatal errors: {len(rc_fatal_codes)}\n\n")
        
        # Process EWL codes
        if ewl_codes:
            summary.append("## EWL (Enhanced Warning Log) Errors\n\n")
            
            # Group by (major, minor) and collect sockets/contexts
            code_groups = defaultdict(lambda: {
                'count': 0, 
                'sockets': set(), 
                'topology': [],  # List of (socket, channel, dimm, rank) tuples
                'contexts': [],
                'checkpoints': set()
            })
            for entry in ewl_codes:
                key = (entry['major'], entry['minor'])
                code_groups[key]['count'] += 1
                if entry.get('socket'):
                    code_groups[key]['sockets'].add(entry['socket'])
                
                # Collect topology information
                if entry.get('channel') is not None:
                    topo = (
                        entry.get('socket', '-'),
                        entry.get('channel', '-'),
                        entry.get('dimm', '-'),
                        entry.get('rank', '-')
                    )
                    code_groups[key]['topology'].append(topo)
                
                # Collect checkpoint information
                if entry.get('major_checkpoint'):
                    cp_pair = f"{entry['major_checkpoint']}/{entry['minor_checkpoint']}"
                    code_groups[key]['checkpoints'].add(cp_pair)
                
                code_groups[key]['contexts'].append(entry.get('context', ''))
            
            # Sort by count (descending)
            sorted_codes = sorted(code_groups.items(), key=lambda x: x[1]['count'], reverse=True)
            
            for idx, ((major, minor), data) in enumerate(sorted_codes, 1):
                info = self.decode_code(major, minor)
                count = data['count']
                sockets = sorted(data['sockets'])
                
                # Code header with count
                summary.append(f"### Error #{idx}: `{major} / {minor}`\n\n")
                summary.append(f"**Occurrences:** {count}\n\n")
                
                # Socket information
                if sockets:
                    socket_str = ', '.join([f"S{s}" for s in sockets])
                    summary.append(f"**Sockets:** {socket_str}\n\n")
                
                # Topology information (Channel/Dimm/Rank)
                if data['topology']:
                    summary.append(f"**Affected Hardware:**\n")
                    # Show unique topology combinations (limit to 10)
                    unique_topo = list(set(data['topology']))
                    for topo in unique_topo[:10]:
                        s, c, d, r = topo
                        topo_str = f"Socket {s}, Channel {c}, DIMM {d}, Rank {r}"
                        summary.append(f"- {topo_str}\n")
                    if len(unique_topo) > 10:
                        summary.append(f"- ... and {len(unique_topo) - 10} more locations\n")
                    summary.append("\n")
                
                # Checkpoint information
                if data['checkpoints']:
                    cp_list = sorted(data['checkpoints'])
                    summary.append(f"**Checkpoints:** {', '.join(cp_list)}\n\n")
                
                # Major name
                if info['major_name']:
                    summary.append(f"**Major Code:** {info['major_name']}\n\n")
                
                # Minor name
                if info['minor_name']:
                    summary.append(f"**Minor Code:** {info['minor_name']}\n\n")
                
                # Description
                if info['major_desc'] or info['minor_desc']:
                    summary.append(f"**Description:**\n")
                    if info['major_desc']:
                        summary.append(f"- {info['major_desc']}\n")
                    if info['minor_desc']:
                        summary.append(f"- {info['minor_desc']}\n")
                    summary.append("\n")
                
                # Not found message
                if not info['major_name']:
                    summary.append("*Code not found in database*\n\n")
                
                # Show first context example
                if data['contexts'] and data['contexts'][0]:
                    summary.append(f"**Example Context:**\n```\n{data['contexts'][0][:500]}\n```\n\n")
                
                summary.append("---\n\n")

        # Process IPSD codes
        if ipsd_codes:
            summary.append("## IPSD (Intel Platform Service Provider) Errors\n\n")

            # Group by IPSD code
            ipsd_groups = defaultdict(lambda: {'count': 0, 'guids': set(), 'contexts': []})
            for entry in ipsd_codes:
                key = entry['ipsd_code']
                ipsd_groups[key]['count'] += 1
                if entry.get('guid'):
                    ipsd_groups[key]['guids'].add(entry['guid'])
                ipsd_groups[key]['contexts'].append(entry.get('context', ''))

            sorted_ipsd = sorted(ipsd_groups.items(), key=lambda x: x[1]['count'], reverse=True)

            for idx, (ipsd_code, data) in enumerate(sorted_ipsd, 1):
                decoded = self.decode_ipsd_error(ipsd_code)
                count = data['count']
                guids = list(data['guids'])

                summary.append(f"### IPSD Error #{idx}: `{ipsd_code}`\n\n")
                summary.append(f"**Occurrences:** {count}\n\n")
                summary.append(f"**Error Code:** {decoded['code']}\n\n")
                summary.append(f"**Description:** {decoded['description']}\n\n")

                if guids:
                    summary.append("**Associated GUIDs:**\n")
                    for guid in guids[:3]:
                        summary.append(f"- `{guid}`\n")
                    if len(guids) > 3:
                        summary.append(f"- ... and {len(guids) - 3} more\n")
                    summary.append("\n")

                if data['contexts'] and data['contexts'][0]:
                    summary.append(f"**Example Context:**\n```\n{data['contexts'][0][:500]}\n```\n\n")

                summary.append("---\n\n")

        # Process RC Fatal errors
        if rc_fatal_codes:
            summary.append("## RC Fatal Errors\n\n")

            rc_groups = defaultdict(lambda: {
                'count': 0, 'sockets': set(), 'file_refs': set(), 'contexts': []
            })
            for entry in rc_fatal_codes:
                key = (entry.get('major') or 'UNKNOWN', entry.get('minor') or 'UNKNOWN')
                rc_groups[key]['count'] += 1
                if entry.get('socket'):
                    rc_groups[key]['sockets'].add(entry['socket'])
                if entry.get('file_ref'):
                    rc_groups[key]['file_refs'].add(entry['file_ref'])
                rc_groups[key]['contexts'].append(entry.get('context', ''))

            sorted_rc = sorted(rc_groups.items(), key=lambda x: x[1]['count'], reverse=True)

            for idx, ((major, minor), data) in enumerate(sorted_rc, 1):
                count = data['count']
                sockets = sorted(data['sockets'])

                summary.append(f"### RC Fatal Error #{idx}: `{major} / {minor}`\n\n")
                summary.append(f"**Occurrences:** {count}\n\n")

                if sockets:
                    summary.append(f"**Sockets:** {', '.join(['S' + s for s in sockets])}\n\n")

                # Decode via RC Fatal database
                if major != 'UNKNOWN':
                    info = self.decode_rc_fatal_error(major, minor if minor != 'UNKNOWN' else None)
                    if info['major_name']:
                        summary.append(f"**Major Code:** {info['major_name']}\n\n")
                    if info['minor_name']:
                        summary.append(f"**Minor Code:** {info['minor_name']}\n\n")
                    if info['major_desc'] or info['minor_desc']:
                        summary.append("**Description:**\n")
                        if info['major_desc']:
                            summary.append(f"- {info['major_desc']}\n")
                        if info['minor_desc']:
                            summary.append(f"- {info['minor_desc']}\n")
                        summary.append("\n")
                    if info.get('major_source'):
                        summary.append(f"**Source:** `{info['major_source']}`\n\n")
                    if not info['major_name']:
                        summary.append("*Code not found in RC Fatal database*\n\n")

                if data['file_refs']:
                    summary.append("**File References:**\n")
                    for ref in sorted(data['file_refs'])[:3]:
                        summary.append(f"- `{ref}`\n")
                    summary.append("\n")

                if data['contexts'] and data['contexts'][0]:
                    summary.append(f"**Example Context:**\n```\n{data['contexts'][0][:500]}\n```\n\n")

                summary.append("---\n\n")

        return ''.join(summary)

    def format_single_code(self, result):
        """Format single code decode result for display."""
        output = []
        output.append(f"Code: {result['major_code']}")
        if result['minor_code']:
            output.append(f" / {result['minor_code']}")
        output.append("\n")

        if result['major_name']:
            output.append(f"Name: {result['major_name']}")
            if result['minor_name']:
                output.append(f" / {result['minor_name']}")
            output.append("\n")

        if result['major_desc']:
            output.append(f"Description: {result['major_desc']}")
            if result['minor_desc']:
                output.append(f" / {result['minor_desc']}")
            output.append("\n")

        if not result['major_name']:
            output.append("Code not found in database\n")

        return ''.join(output)


def _sanitize_for_json(codes):
    """Convert parsed code entries to JSON-serialisable dicts.

    Removes internal-only fields (context_lines) and converts any set-typed
    fields (sockets, checkpoints) to sorted lists.
    """
    safe = []
    for c in codes:
        d = dict(c)
        d.pop('context_lines', None)
        for key in ('sockets', 'checkpoints'):
            if isinstance(d.get(key), set):
                d[key] = sorted(d[key])
        safe.append(d)
    return safe


def main():
    """Main entry point for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(description='Decode BIOS EWL / IPSD / RC Fatal error codes')
    parser.add_argument('--code', help='Major code to decode (e.g., 0x29)')
    parser.add_argument('--minor', help='Minor code to decode (e.g., 0x15)')
    parser.add_argument('--log', help='Log file to analyze')
    parser.add_argument('--db', default=None, help='Path to EWL database (default: ewl_codes_database.json in script dir)')
    parser.add_argument('--json', action='store_true', dest='json_output',
                        help='Emit machine-readable JSON instead of markdown')

    args = parser.parse_args()

    decoder = EWLDecoder(db_path=args.db)

    # Single code decode
    if args.code:
        major = args.code
        if not major.lower().startswith('0x'):
            major = f"0x{int(major, 0):X}"

        minor = None
        if args.minor:
            minor = args.minor
            if not minor.lower().startswith('0x'):
                minor = f"0x{int(minor, 0):X}"

        result = decoder.decode_code(major, minor)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(decoder.format_single_code(result))

    # Log analysis — file or stdin
    else:
        if args.log:
            with open(args.log, 'r', errors='replace') as f:
                log_text = f.read()
        elif not sys.stdin.isatty():
            log_text = sys.stdin.read()
        else:
            parser.print_help()
            sys.exit(1)

        codes = decoder.parse_log(log_text)
        if args.json_output:
            print(json.dumps(_sanitize_for_json(codes), indent=2))
        else:
            print(decoder.generate_summary(codes))


if __name__ == '__main__':
    main()
