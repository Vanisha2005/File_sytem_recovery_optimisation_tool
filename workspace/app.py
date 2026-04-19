from flask import Flask, request, jsonify
import time
import os
import json
from corruption_detector import CorruptionDetector, CorruptionRepairer, check_file_corruption, repair_file_corruption
from recovery_utils import BackupRecoveryManager, CorruptionHistory, BatchOperations

app = Flask(__name__)

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ========== CONFIGURATION ==========
WORKSPACE_PATH = "workspace"
BACKUPS_PATH = os.path.join(WORKSPACE_PATH, ".backups")
os.makedirs(BACKUPS_PATH, exist_ok=True)
os.makedirs(WORKSPACE_PATH, exist_ok=True)

# Initialize managers
recovery_manager = BackupRecoveryManager(BACKUPS_PATH, WORKSPACE_PATH)
corruption_history = CorruptionHistory(os.path.join(WORKSPACE_PATH, '.corruption_history.json'))

# ========== FILESYSTEM STATE ==========
fs = {
    "blocks_total": 2560,
    "block_size": 4096,
    "bitmap": [0] * 2560,
    "inodes": {},
    "next_inode_id": 1,
    "log": [],
}

def seed_files():
    """Initialize the filesystem with sample files."""
    create_file_internal("readme.txt", "Welcome to DiskOS v2 🚀\nThis is a virtual file system.")
    create_file_internal("notes.md", "# Notes\n- Learn OS\n- Understand file systems")
    create_file_internal("system.log", "[BOOT] System initialized\n[OK] All services running")
    create_file_internal("script.js", "console.log('Hello DiskOS');")
    create_file_internal("bigdata.bin", "A" * 800)
    
    inode1 = create_file_internal("old_notes.txt", "This file was deleted")
    delete_file_internal(inode1["id"])
    
    inode2 = create_file_internal("temp.log", "Temporary logs")
    delete_file_internal(inode2["id"])

def log_event(msg, level="info"):
    """Prepend a timestamped event to the system log (max 60 entries)."""
    fs["log"].insert(0, {
        "msg":   msg,
        "level": level,
        "time":  time.strftime("%H:%M:%S"),
    })
    if len(fs["log"]) > 60:
        fs["log"] = fs["log"][:60]

def create_file_internal(name, content=""):
    """Create a file and allocate blocks."""
    size = len(content.encode())
    blocks_needed = (size + fs["block_size"] - 1) // fs["block_size"]
    
    if blocks_needed > sum(1 for b in fs["bitmap"] if b == 0):
        return None
    
    inode_id = fs["next_inode_id"]
    fs["next_inode_id"] += 1
    
    allocated_blocks = []
    for i, bit in enumerate(fs["bitmap"]):
        if bit == 0 and len(allocated_blocks) < blocks_needed:
            fs["bitmap"][i] = 1
            allocated_blocks.append(i)
    
    inode = {
        "id": inode_id,
        "name": name,
        "path": f"/{name}",
        "blocks": allocated_blocks,
        "size": size,
        "created": time.time(),
        "modified": time.time(),
        "status": "active",
        "content": content,
        "repaired": False,
        "repair_fixes": [],
    }
    
    fs["inodes"][inode_id] = inode
    log_event(f"File created: {name}")
    return inode

def delete_file_internal(inode_id):
    """Mark file as deleted and free blocks."""
    inode = fs["inodes"].get(inode_id)
    if inode:
        inode["status"] = "deleted"
        for block_id in inode["blocks"]:
            fs["bitmap"][block_id] = 0
        log_event(f"File deleted: {inode['name']}")
        return True
    return False

# Initialize filesystem
seed_files()

@app.route("/api/status", methods=["GET"])
def status():
    """Get filesystem status."""
    return jsonify({
        "blocks_used": sum(fs["bitmap"]),
        "blocks_total": fs["blocks_total"],
        "inodes": len([i for i in fs["inodes"].values() if i["status"] == "active"]),
        "files": [i for i in fs["inodes"].values() if i["status"] == "active"],
    })

@app.route("/api/write", methods=["POST"])
def write_file():
    """Write/upload a file with optional corruption check."""
    data = request.get_json()
    filename = data.get("filename", "unnamed")
    content = data.get("content", "")
    check_corruption = request.args.get("check", "false").lower() == "true"
    
    inode = create_file_internal(filename, content)
    if not inode:
        return jsonify({"error": "Not enough space"}), 400
    
    inode_id = inode["id"]
    
    # Create backup
    backup_path = os.path.join(BACKUPS_PATH, f"{inode_id}_{filename}.bak")
    with open(backup_path, "w") as f:
        f.write(content)
    
    corruption_report = None
    if check_corruption:
        filepath = os.path.join(WORKSPACE_PATH, filename)
        with open(filepath, "wb") as f:
            f.write(content.encode() if isinstance(content, str) else content)
        
        report = check_file_corruption(filepath)
        corruption_history.log_detection(filename, report)
        
        if report["has_corruption"]:
            inode["status"] = "corrupted"
            corruption_report = report
    
    return jsonify({
        "inode_id": inode_id,
        "filename": filename,
        "size": inode["size"],
        "corruption_report": corruption_report,
    })

@app.route("/api/read/<int:inode_id>", methods=["GET"])
def read_file(inode_id):
    """Read a file by inode ID."""
    inode = fs["inodes"].get(inode_id)
    if not inode:
        return jsonify({"error": "File not found"}), 404
    
    return jsonify({
        "inode_id": inode_id,
        "filename": inode["name"],
        "size": inode["size"],
        "status": inode["status"],
        "content": inode["content"],
    })

@app.route("/api/check-corruption/<int:inode_id>", methods=["GET"])
def check_corruption_endpoint(inode_id):
    """Check a file for corruption."""
    inode = fs["inodes"].get(inode_id)
    if not inode:
        return jsonify({"error": "File not found"}), 404
    
    filepath = os.path.join(WORKSPACE_PATH, inode["name"])
    with open(filepath, "wb") as f:
        f.write(inode["content"].encode() if isinstance(inode["content"], str) else inode["content"])
    
    report = check_file_corruption(filepath)
    corruption_history.log_detection(inode["name"], report)
    
    if report["has_corruption"]:
        inode["status"] = "corrupted"
    
    return jsonify(report)

@app.route("/api/repair", methods=["POST"])
def repair_file_endpoint():
    """Repair a file with user-approved fixes."""
    data = request.get_json()
    inode_id = data.get("inode_id")
    approved_fixes = data.get("approved_fixes", [])
    
    inode = fs["inodes"].get(inode_id)
    if not inode:
        return jsonify({"error": "File not found"}), 404
    
    filepath = os.path.join(WORKSPACE_PATH, inode["name"])
    with open(filepath, "wb") as f:
        f.write(inode["content"].encode() if isinstance(inode["content"], str) else inode["content"])
    
    result = repair_file_corruption(filepath, approved_fixes)
    
    if result.get("success"):
        with open(filepath, "rb") as f:
            inode["content"] = f.read().decode(errors="replace")
        inode["status"] = "repaired"
        inode["repaired"] = True
        inode["repair_fixes"] = approved_fixes
        inode["modified"] = time.time()
        
        corruption_history.log_repair(inode["name"], result)
        log_event(f"File repaired: {inode['name']} with {len(approved_fixes)} fixes")
    
    return jsonify(result)

@app.route("/api/list", methods=["GET"])
def list_files():
    """List all files."""
    return jsonify({
        "files": [i for i in fs["inodes"].values() if i["status"] != "deleted"],
    })

@app.route("/api/delete/<int:inode_id>", methods=["DELETE"])
def delete_file(inode_id):
    """Delete a file."""
    if delete_file_internal(inode_id):
        return jsonify({"success": True})
    return jsonify({"error": "File not found"}), 404

# Phase 4: Advanced Features Endpoints

@app.route("/api/backups", methods=["GET"])
def get_backups():
    """List all available backups."""
    backups = recovery_manager.list_backups()
    stats = corruption_history.get_stats()
    
    return jsonify({
        "backups": backups,
        "backup_count": len(backups),
        "stats": stats,
    })

@app.route("/api/recover-from-backup/<filename>", methods=["POST"])
def recover_backup(filename):
    """Recover a file from backup."""
    data = request.get_json()
    inode_id = data.get("inode_id")
    
    inode = fs["inodes"].get(inode_id)
    if not inode:
        return jsonify({"error": "Inode not found"}), 404
    
    target_path = os.path.join(WORKSPACE_PATH, inode["name"])
    result = recovery_manager.recover_from_backup(filename, target_path)
    
    if result["success"]:
        with open(target_path, "rb") as f:
            inode["content"] = f.read().decode(errors="replace")
        inode["status"] = "active"
        inode["repaired"] = False
        inode["modified"] = time.time()
        log_event(f"File recovered from backup: {filename}")
    
    return jsonify(result)

@app.route("/api/batch-check", methods=["POST"])
def batch_check():
    """Check multiple files for corruption."""
    data = request.get_json()
    inode_ids = data.get("inode_ids", [])
    
    results = []
    corrupted_count = 0
    failed_checks = 0
    
    for inode_id in inode_ids:
        inode = fs["inodes"].get(inode_id)
        if not inode:
            failed_checks += 1
            continue
        
        filepath = os.path.join(WORKSPACE_PATH, inode["name"])
        with open(filepath, "wb") as f:
            f.write(inode["content"].encode() if isinstance(inode["content"], str) else inode["content"])
        
        report = check_file_corruption(filepath)
        if report["has_corruption"]:
            corrupted_count += 1
            inode["status"] = "corrupted"
        
        results.append({
            "inode_id": inode_id,
            "filename": inode["name"],
            "has_corruption": report["has_corruption"],
            "corruption_score": report["corruption_score"],
        })
    
    return jsonify({
        "batch_result": {
            "total_files": len(inode_ids),
            "corrupted_count": corrupted_count,
            "failed_checks": failed_checks,
            "results": results,
        }
    })

@app.route("/api/batch-repair", methods=["POST"])
def batch_repair():
    """Repair multiple corrupted files."""
    data = request.get_json()
    inode_ids = data.get("inode_ids", [])
    approved_fixes = data.get("approved_fixes", [])
    
    repaired = []
    failed = []
    
    for inode_id in inode_ids:
        inode = fs["inodes"].get(inode_id)
        if not inode:
            failed.append({"inode_id": inode_id, "error": "Not found"})
            continue
        
        filepath = os.path.join(WORKSPACE_PATH, inode["name"])
        with open(filepath, "wb") as f:
            f.write(inode["content"].encode() if isinstance(inode["content"], str) else inode["content"])
        
        result = repair_file_corruption(filepath, approved_fixes)
        
        if result.get("success"):
            with open(filepath, "rb") as f:
                inode["content"] = f.read().decode(errors="replace")
            inode["status"] = "repaired"
            inode["repaired"] = True
            inode["repair_fixes"] = approved_fixes
            inode["modified"] = time.time()
            repaired.append({"inode_id": inode_id, "filename": inode["name"]})
        else:
            failed.append({"inode_id": inode_id, "error": result.get("error", "Repair failed")})
    
    return jsonify({
        "repaired": repaired,
        "failed": failed,
        "summary": {
            "total": len(inode_ids),
            "successful": len(repaired),
            "failed": len(failed),
        }
    })

@app.route("/api/corruption-stats", methods=["GET"])
def get_stats():
    """Get corruption history and statistics."""
    history = corruption_history.get_stats()
    
    # Count current file states
    active_count = sum(1 for i in fs["inodes"].values() if i["status"] == "active")
    corrupted_count = sum(1 for i in fs["inodes"].values() if i["status"] == "corrupted")
    repaired_count = sum(1 for i in fs["inodes"].values() if i["status"] == "repaired")
    
    return jsonify({
        "history": history,
        "current": {
            "active": active_count,
            "corrupted": corrupted_count,
            "repaired": repaired_count,
        }
    })

@app.route("/api/system-status", methods=["GET"])
def system_status():
    """Get comprehensive system status."""
    backups = recovery_manager.list_backups()
    stats = corruption_history.get_stats()
    
    return jsonify({
        "filesystem": {
            "blocks_used": sum(fs["bitmap"]),
            "blocks_total": fs["blocks_total"],
            "usage_percent": (sum(fs["bitmap"]) / fs["blocks_total"]) * 100,
        },
        "corruption": {
            "total_detections": stats.get("total_detections", 0),
            "total_repaired": stats.get("total_repaired", 0),
            "most_common_issues": stats.get("most_common_issues", []),
        },
        "backups": {
            "count": len(backups),
            "total_size": sum(b.get("size", 0) for b in backups),
        },
        "files": {
            "active": sum(1 for i in fs["inodes"].values() if i["status"] == "active"),
            "corrupted": sum(1 for i in fs["inodes"].values() if i["status"] == "corrupted"),
            "repaired": sum(1 for i in fs["inodes"].values() if i["status"] == "repaired"),
            "deleted": sum(1 for i in fs["inodes"].values() if i["status"] == "deleted"),
        },
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)
