
import json
import struct
import os
from typing import Dict, List, Tuple, Any

class CorruptionDetector:
    """Detects file corruption across multiple file types."""
    
    # Magic bytes for common file formats
    MAGIC_BYTES = {
        'png': b'\x89PNG',
        'jpg': [b'\xff\xd8\xff', b'\xff\xd8\xff\xe0', b'\xff\xd8\xff\xe1'],
        'pdf': b'%PDF',
        'gz': b'\x1f\x8b',
        'zip': b'PK\x03\x04',
    }
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file_ext = os.path.splitext(file_path)[1].lower().lstrip('.')
        self.issues: List[Dict[str, Any]] = []
        self.corruption_score = 0.0
        
    def detect_all(self) -> Dict[str, Any]:
        """Run all corruption detection checks."""
        try:
            with open(self.file_path, 'rb') as f:
                self.raw_data = f.read()
        except Exception as e:
            return {
                "has_corruption": True,
                "issues": [{"type": "read_error", "severity": "critical", "description": f"Failed to read file: {str(e)}", "fixable": False}],
                "corruption_score": 100
            }
        
        self._check_truncation()
        self._check_entropy()
        
        if self.file_ext in ['json']:
            self._check_json()
        elif self.file_ext in ['csv']:
            self._check_csv()
        elif self.file_ext in ['txt', 'md', 'log']:
            self._check_text()
        elif self.file_ext in ['png', 'jpg', 'jpeg']:
            self._check_image()
        elif self.file_ext in ['pdf']:
            self._check_pdf()
        
        self._calculate_corruption_score()
        
        return {
            "has_corruption": len(self.issues) > 0,
            "issues": self.issues,
            "corruption_score": self.corruption_score,
            "file_ext": self.file_ext,
            "file_size": len(self.raw_data)
        }
    
    def _add_issue(self, issue_type: str, severity: str, description: str, fixable: bool):
        """Add a corruption issue."""
        self.issues.append({
            "type": issue_type,
            "severity": severity,
            "description": description,
            "fixable": fixable
        })
    
    def _check_truncation(self):
        """Detect if file appears truncated."""
        if len(self.raw_data) == 0:
            self._add_issue("empty_file", "warning", "File is empty", fixable=False)
            return
        
        if self.raw_data[-1:] == b'\x00' * (len(self.raw_data[-10:]) // 2):
            self._add_issue("truncation", "warning", "File may be truncated (ends with null bytes)", fixable=False)
        
        if self.file_ext == 'png' and len(self.raw_data) < 67:
            self._add_issue("truncation", "critical", "PNG file too small (incomplete header)", fixable=False)
        elif self.file_ext in ['jpg', 'jpeg'] and len(self.raw_data) < 100:
            self._add_issue("truncation", "critical", "JPEG file too small (incomplete header)", fixable=False)
    
    def _check_entropy(self):
        """Detect random byte corruption via entropy analysis."""
        if len(self.raw_data) < 100:
            return
        
        sample = self.raw_data[:min(1000, len(self.raw_data))]
        byte_freq = {}
        for byte in sample:
            byte_freq[byte] = byte_freq.get(byte, 0) + 1
        
        import math
        entropy = 0.0
        for count in byte_freq.values():
            p = count / len(sample)
            if p > 0:
                entropy -= p * math.log2(p)
        
        if self.file_ext in ['json', 'csv', 'txt', 'md'] and entropy > 7.0:
            self._add_issue("high_entropy", "warning", f"Unusual entropy detected ({entropy:.2f}) - possible byte corruption", fixable=False)
        
        if self.file_ext in ['png', 'jpg'] and entropy < 1.5:
            self._add_issue("low_entropy", "warning", f"Unusually low entropy ({entropy:.2f}) - possible data corruption", fixable=False)
    
    def _check_json(self):
        """Validate JSON structure."""
        try:
            decoded = self.raw_data.decode('utf-8', errors='ignore')
            json.loads(decoded)
        except json.JSONDecodeError as e:
            self._add_issue("invalid_json", "critical", f"JSON syntax error: {str(e)}", fixable=True)
        except Exception as e:
            self._add_issue("json_error", "warning", f"JSON validation failed: {str(e)}", fixable=False)
    
    def _check_csv(self):
        """Validate CSV structure."""
        try:
            decoded = self.raw_data.decode('utf-8', errors='ignore')
            lines = decoded.strip().split('\n')
            
            if len(lines) == 0:
                self._add_issue("empty_csv", "warning", "CSV file is empty", fixable=False)
                return
            
            header_cols = len(lines[0].split(','))
            for i, line in enumerate(lines[1:], 1):
                cols = len(line.split(','))
                if cols != header_cols:
                    self._add_issue("csv_rows_mismatch", "warning", f"Row {i} has {cols} columns, header has {header_cols}", fixable=True)
                    break
        except Exception as e:
            self._add_issue("csv_error", "warning", f"CSV validation failed: {str(e)}", fixable=False)
    
    def _check_text(self):
        """Validate text file encoding and structure."""
        try:
            decoded = self.raw_data.decode('utf-8')
        except UnicodeDecodeError as e:
            self._add_issue("encoding_error", "warning", f"UTF-8 decode error at position {e.start}: {str(e)}", fixable=True)
            return
        
        if '\x00' in decoded:
            self._add_issue("null_bytes", "warning", "File contains null bytes", fixable=True)
    
    def _check_image(self):
        """Validate image file format."""
        if self.file_ext == 'png':
            if len(self.raw_data) >= 4:
                if self.raw_data[:4] != self.MAGIC_BYTES['png']:
                    self._add_issue("invalid_header", "critical", "PNG file has invalid magic bytes", fixable=False)
            
            if self.raw_data[-8:] != b'IEND\xae\x42\x60\x82':
                self._add_issue("missing_end_chunk", "warning", "PNG missing IEND chunk (file may be truncated)", fixable=False)
        
        elif self.file_ext in ['jpg', 'jpeg']:
            if len(self.raw_data) >= 2:
                if not any(self.raw_data[:3] == magic for magic in self.MAGIC_BYTES['jpg']):
                    self._add_issue("invalid_header", "critical", "JPEG file has invalid magic bytes", fixable=False)
            
            if self.raw_data[-2:] != b'\xff\xd9':
                self._add_issue("missing_eoi_marker", "warning", "JPEG missing EOI marker (file may be truncated)", fixable=False)
    
    def _check_pdf(self):
        """Validate PDF structure."""
        if len(self.raw_data) >= 4:
            if self.raw_data[:4] != self.MAGIC_BYTES['pdf']:
                self._add_issue("invalid_header", "critical", "PDF file has invalid magic bytes", fixable=False)
        
        if b'xref' not in self.raw_data or b'trailer' not in self.raw_data:
            self._add_issue("missing_structure", "warning", "PDF missing xref or trailer (may be truncated)", fixable=False)
    
    def _calculate_corruption_score(self):
        """Calculate overall corruption percentage 0-100."""
        if not self.issues:
            self.corruption_score = 0.0
            return
        
        critical_count = sum(1 for i in self.issues if i['severity'] == 'critical')
        warning_count = sum(1 for i in self.issues if i['severity'] == 'warning')
        info_count = sum(1 for i in self.issues if i['severity'] == 'info')
        
        self.corruption_score = min(100.0, critical_count * 40 + warning_count * 20 + info_count * 5)


class CorruptionRepairer:
    """Repairs detected corruption issues in files."""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file_ext = os.path.splitext(file_path)[1].lower().lstrip('.')
        self.original_size = 0
        self.repaired_size = 0
        self.repairs_applied = []
        
    def repair(self, approved_fixes: List[str]) -> Dict[str, Any]:
        """Repair file based on approved fixes."""
        try:
            with open(self.file_path, 'rb') as f:
                self.raw_data = f.read()
                self.original_size = len(self.raw_data)
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to read file: {str(e)}",
                "repairs_applied": []
            }
        
        for fix_type in approved_fixes:
            try:
                if fix_type == "null_bytes":
                    self._fix_null_bytes()
                elif fix_type == "encoding_error":
                    self._fix_encoding()
                elif fix_type == "invalid_json":
                    self._fix_json()
                elif fix_type == "csv_rows_mismatch":
                    self._fix_csv_rows()
                elif fix_type == "remove_garbage":
                    self._remove_garbage_bytes()
            except Exception as e:
                continue  # Skip failed repairs, continue with others
        
        # Write repaired data back
        try:
            with open(self.file_path, 'wb') as f:
                f.write(self.raw_data)
            self.repaired_size = len(self.raw_data)
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to write repaired file: {str(e)}",
                "repairs_applied": self.repairs_applied
            }
        
        return {
            "success": True,
            "repairs_applied": self.repairs_applied,
            "original_size": self.original_size,
            "repaired_size": self.repaired_size,
            "bytes_removed": self.original_size - self.repaired_size
        }
    
    def _fix_null_bytes(self):
        """Remove null bytes from file."""
        original_len = len(self.raw_data)
        # Remove all null bytes
        self.raw_data = self.raw_data.replace(b'\x00', b'')
        if len(self.raw_data) < original_len:
            self.repairs_applied.append({
                "type": "null_bytes",
                "description": f"Removed {original_len - len(self.raw_data)} null bytes"
            })
    
    def _fix_encoding(self):
        """Try to fix UTF-8 encoding errors."""
        try:
            # Decode with 'replace' to handle errors
            decoded = self.raw_data.decode('utf-8', errors='replace')
            # Encode back cleanly
            self.raw_data = decoded.encode('utf-8')
            self.repairs_applied.append({
                "type": "encoding_error",
                "description": "Fixed UTF-8 encoding errors (invalid sequences replaced)"
            })
        except Exception:
            pass
    
    def _fix_json(self):
        """Try to repair malformed JSON."""
        try:
            decoded = self.raw_data.decode('utf-8', errors='ignore')
            # Try to close unclosed structures
            brace_count = decoded.count('{') - decoded.count('}')
            bracket_count = decoded.count('[') - decoded.count(']')
            paren_count = decoded.count('(') - decoded.count(')')
            
            fixed = decoded.rstrip()
            fixed_count = 0
            
            if bracket_count > 0:
                fixed += ']' * bracket_count
                fixed_count += bracket_count
            if brace_count > 0:
                fixed += '}' * brace_count
                fixed_count += brace_count
            if paren_count > 0:
                fixed += ')' * paren_count
                fixed_count += paren_count
            
            # Validate
            json.loads(fixed)
            self.raw_data = fixed.encode('utf-8')
            self.repairs_applied.append({
                "type": "invalid_json",
                "description": f"Closed unclosed JSON structures (added {fixed_count} characters)"
            })
        except Exception:
            pass
    
    def _fix_csv_rows(self):
        """Try to fix CSV row inconsistencies."""
        try:
            decoded = self.raw_data.decode('utf-8', errors='ignore')
            lines = [l.strip() for l in decoded.split('\n') if l.strip()]
            
            if len(lines) < 2:
                return
            
            header_cols = len(lines[0].split(','))
            fixed_lines = []
            
            for line in lines:
                cols = line.split(',')
                # Pad or trim to match header
                if len(cols) < header_cols:
                    cols.extend([''] * (header_cols - len(cols)))
                elif len(cols) > header_cols:
                    cols = cols[:header_cols]
                fixed_lines.append(','.join(cols))
            
            fixed_content = '\n'.join(fixed_lines) + '\n'
            self.raw_data = fixed_content.encode('utf-8')
            self.repairs_applied.append({
                "type": "csv_rows_mismatch",
                "description": f"Aligned CSV rows to {header_cols} columns"
            })
        except Exception:
            pass
    
    def _remove_garbage_bytes(self):
        """Remove high-entropy garbage bytes."""
        try:
            # Replace non-printable sequences with spaces
            result = bytearray()
            for byte in self.raw_data:
                if byte < 32 and byte not in [9, 10, 13]:  # Keep tabs, newlines, carriage returns
                    result.append(32)  # Replace with space
                else:
                    result.append(byte)
            
            if len(result) == len(self.raw_data):
                self.raw_data = bytes(result)
                self.repairs_applied.append({
                    "type": "remove_garbage",
                    "description": "Normalized non-printable characters"
                })
        except Exception:
            pass


def check_file_corruption(file_path: str) -> Dict[str, Any]:
    """Helper function to check file corruption."""
    detector = CorruptionDetector(file_path)
    return detector.detect_all()


def repair_file_corruption(file_path: str, approved_fixes: List[str]) -> Dict[str, Any]:
    """Helper function to repair file corruption."""
    repairer = CorruptionRepairer(file_path)
    return repairer.repair(approved_fixes)
