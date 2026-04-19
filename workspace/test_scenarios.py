
import tempfile
import os
import json
from corruption_detector import check_file_corruption, repair_file_corruption

class TestScenarios:
    """Collection of test scenarios for corruption detection and repair."""
    
    @staticmethod
    def create_test_files():
        """Create test files with various types of corruption."""
        test_dir = tempfile.mkdtemp(prefix="test_corruption_")
        test_files = {}
        
        print(f"Creating test files in {test_dir}")
        
        # Test 1: Valid JSON
        with open(os.path.join(test_dir, "valid.json"), 'w') as f:
            json.dump({"status": "ok", "data": [1, 2, 3, 4, 5]}, f)
        test_files["valid.json"] = "Valid JSON file"
        
        # Test 2: Corrupted JSON (missing closing brace)
        with open(os.path.join(test_dir, "corrupted.json"), 'w') as f:
            f.write('{"status": "error", "data": [1, 2, 3')
        test_files["corrupted.json"] = "JSON with unclosed structures"
        
        # Test 3: Text with null bytes
        with open(os.path.join(test_dir, "nullbytes.txt"), 'wb') as f:
            f.write(b"Hello\x00World\x00Test\x00Data")
        test_files["nullbytes.txt"] = "Text file with null bytes"
        
        # Test 4: Valid CSV
        with open(os.path.join(test_dir, "valid.csv"), 'w') as f:
            f.write("id,name,age\n1,Alice,30\n2,Bob,25\n")
        test_files["valid.csv"] = "Valid CSV file"
        
        # Test 5: Corrupted CSV (inconsistent columns)
        with open(os.path.join(test_dir, "corrupted.csv"), 'w') as f:
            f.write("id,name,age\n1,Alice\n2,Bob,25,extra\n")
        test_files["corrupted.csv"] = "CSV with inconsistent row lengths"
        
        # Test 6: Valid PNG header
        with open(os.path.join(test_dir, "valid.png"), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'dummy data' * 100 + b'IEND\xae\x42\x60\x82')
        test_files["valid.png"] = "Valid PNG with proper headers"
        
        # Test 7: Invalid PNG (bad header)
        with open(os.path.join(test_dir, "invalid.png"), 'wb') as f:
            f.write(b'NOTPNG' + b'dummy data' + b'IEND\xae\x42\x60\x82')
        test_files["invalid.png"] = "PNG with invalid magic bytes"
        
        # Test 8: UTF-8 with encoding errors
        with open(os.path.join(test_dir, "encoding_error.txt"), 'wb') as f:
            f.write(b'Valid text\xff\xfeInvalid\xc3\x28More text')
        test_files["encoding_error.txt"] = "Text with UTF-8 encoding errors"
        
        # Test 9: Large file with garbage
        with open(os.path.join(test_dir, "garbage.bin"), 'wb') as f:
            import random
            f.write(bytes([random.randint(0, 255) for _ in range(10000)]))
        test_files["garbage.bin"] = "Binary file with random data"
        
        # Test 10: Empty file
        open(os.path.join(test_dir, "empty.txt"), 'w').close()
        test_files["empty.txt"] = "Empty file"
        
        return test_dir, test_files
    
    @staticmethod
    def run_detection_tests(test_dir, test_files):
        """Run corruption detection on all test files."""
        print("\n" + "="*80)
        print("RUNNING CORRUPTION DETECTION TESTS")
        print("="*80)
        
        results = {}
        
        for filename, description in test_files.items():
            filepath = os.path.join(test_dir, filename)
            print(f"\n[TEST] {filename}: {description}")
            
            try:
                report = check_file_corruption(filepath)
                
                print(f"  Corruption Score: {report['corruption_score']:.1f}%")
                print(f"  Has Issues: {report['has_corruption']}")
                print(f"  Issue Count: {len(report['issues'])}")
                
                if report['issues']:
                    print(f"  Issues Found:")
                    for issue in report['issues'][:3]:
                        print(f"    - [{issue['severity']}] {issue['type']}: {issue['description'][:50]}")
                
                results[filename] = {
                    "success": True,
                    "has_corruption": report['has_corruption'],
                    "corruption_score": report['corruption_score'],
                    "issue_count": len(report['issues'])
                }
            except Exception as e:
                print(f"  ERROR: {str(e)}")
                results[filename] = {
                    "success": False,
                    "error": str(e)
                }
        
        return results
    
    @staticmethod
    def run_repair_tests(test_dir, test_files):
        """Run repair tests on corrupted files."""
        print("\n" + "="*80)
        print("RUNNING REPAIR TESTS")
        print("="*80)
        
        repair_candidates = [
            ("corrupted.json", ["invalid_json"]),
            ("nullbytes.txt", ["null_bytes"]),
            ("corrupted.csv", ["csv_rows_mismatch"]),
            ("encoding_error.txt", ["encoding_error"]),
        ]
        
        results = {}
        
        for filename, fixes in repair_candidates:
            filepath = os.path.join(test_dir, filename)
            print(f"\n[REPAIR TEST] {filename}")
            print(f"  Attempting: {', '.join(fixes)}")
            
            try:
                repair_result = repair_file_corruption(filepath, fixes)
                
                print(f"  Success: {repair_result['success']}")
                if repair_result.get('repairs_applied'):
                    print(f"  Repairs Applied: {len(repair_result['repairs_applied'])}")
                    for repair in repair_result['repairs_applied']:
                        print(f"    - {repair['type']}: {repair['description'][:50]}")
                
                if repair_result.get('bytes_removed'):
                    print(f"  Bytes Removed: {repair_result['bytes_removed']}")
                
                # Verify repair
                verify_report = check_file_corruption(filepath)
                print(f"  Verification - New Score: {verify_report['corruption_score']:.1f}%")
                
                results[filename] = {
                    "success": repair_result['success'],
                    "repairs": len(repair_result.get('repairs_applied', [])),
                    "verification_score": verify_report['corruption_score']
                }
            except Exception as e:
                print(f"  ERROR: {str(e)}")
                results[filename] = {
                    "success": False,
                    "error": str(e)
                }
        
        return results
    
    @staticmethod
    def cleanup_test_files(test_dir):
        """Remove test directory."""
        import shutil
        try:
            shutil.rmtree(test_dir)
            print(f"\nCleaned up test directory: {test_dir}")
        except Exception as e:
            print(f"Warning: Could not cleanup {test_dir}: {str(e)}")


def run_all_tests():
    """Execute all test scenarios."""
    print("\n" + "="*80)
    print("PHASE 4: COMPREHENSIVE TEST SUITE")
    print("="*80)
    
    # Create test files
    test_dir, test_files = TestScenarios.create_test_files()
    print(f"Created {len(test_files)} test files")
    
    # Run detection tests
    detection_results = TestScenarios.run_detection_tests(test_dir, test_files)
    
    # Run repair tests
    repair_results = TestScenarios.run_repair_tests(test_dir, test_files)
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    detection_success = sum(1 for r in detection_results.values() if r.get("success"))
    corrupted_detected = sum(1 for r in detection_results.values() if r.get("has_corruption"))
    repair_success = sum(1 for r in repair_results.values() if r.get("success"))
    
    print(f"Detection Tests: {detection_success}/{len(detection_results)} passed")
    print(f"Corrupted Files Detected: {corrupted_detected}/{len(detection_results)}")
    print(f"Repair Tests: {repair_success}/{len(repair_results)} passed")
    
    # Cleanup
    TestScenarios.cleanup_test_files(test_dir)
    
    return {
        "detection": detection_results,
        "repair": repair_results,
        "summary": {
            "detection_success": detection_success,
            "corruption_detected": corrupted_detected,
            "repair_success": repair_success
        }
    }
