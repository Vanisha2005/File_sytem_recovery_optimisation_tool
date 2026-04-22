from flask import Flask, render_template, jsonify, request
import time, math
import random
import base64
import os
import shutil
import hashlib

app = Flask(__name__)

# Disk Configuration
TOTAL_BLOCKS = 2560   
BLOCK_SIZE = 4096
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Workspace Paths  
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_PATH = os.path.join(BASE_PATH, 'workspace')
TRASH_PATH = os.path.join(WORKSPACE_PATH, '.trash')
BACKUPS_PATH = os.path.join(WORKSPACE_PATH, '.backups')
REPAIR_SOURCES_PATH = os.path.join(BACKUPS_PATH, 'clean')

# Ensure directories exist
for path in [WORKSPACE_PATH, TRASH_PATH, BACKUPS_PATH, REPAIR_SOURCES_PATH]:
    os.makedirs(path, exist_ok=True)

# File System Registry
file_system = {
    "bitmap": [0] * TOTAL_BLOCKS,
    "registry": {},
    "next_inode": 1,
    "log": [],
}

def log_event(msg, level="info"):
    """Log an event."""
    file_system["log"].insert(0, {
        "msg": msg,
        "level": level,
        "time": time.strftime("%H:%M:%S"),
    })
    if len(file_system["log"]) > 60:
        file_system["log"].pop()

def blocks_needed(size_bytes):
    """Calculate blocks needed."""
    return max(1, math.ceil(size_bytes / BLOCK_SIZE))

def allocate_blocks(n):
    """Allocate n free blocks."""
    free = [i for i, v in enumerate(file_system["bitmap"]) if v == 0]
    return free[:n] if len(free) >= n else None

def calc_fragmentation():
    """Calculate fragmentation percentage."""
    used = sorted(
        b for inode in file_system["registry"].values()
        if inode["status"] == "active"
        for b in inode["blocks"]
    )
    if len(used) < 2:
        return 0
    gaps = sum(1 for i in range(1, len(used)) if used[i] - used[i - 1] > 1)
    return round(gaps / (len(used) - 1) * 100)

def build_stats():
    """Build filesystem stats."""
    used = file_system["bitmap"].count(1)
    free = file_system["bitmap"].count(0)
    deleted_blocks = file_system["bitmap"].count(2)
    
    return {
        "total": TOTAL_BLOCKS,
        "block_size": BLOCK_SIZE,
        "used": used,
        "free": free,
        "deleted_blocks": deleted_blocks,
        "capacity_pct": round(used / TOTAL_BLOCKS * 100),
        "files": sum(1 for i in file_system["registry"].values() if i["status"] == "active"),
        "deleted_files": sum(1 for i in file_system["registry"].values() if i["status"] == "deleted"),
        "fragmentation": calc_fragmentation(),
    }

def file_exists_active(filename, exclude_iid=None):
    """Check if filename exists and is active (optionally exclude an inode)."""
    return any(inode["name"] == filename and inode["status"] == "active" and inode["id"] != (exclude_iid or -1)
               for inode in file_system["registry"].values())

def get_safe_filename(original_name, exclude_iid=None):
    """Return filename, adding (1), (2) etc if conflicts exist."""
    if not file_exists_active(original_name, exclude_iid):
        return original_name
    
    name_parts = original_name.rsplit(".", 1)
    base = name_parts[0]
    ext = "." + name_parts[1] if len(name_parts) > 1 else ""
    
    counter = 1
    while True:
        new_name = f"{base}({counter}){ext}"
        if not file_exists_active(new_name, exclude_iid):
            return new_name
        counter += 1



TEXT_EXTENSIONS = {"txt", "md", "log", "json", "csv", "py", "js", "html", "css", "xml", "yml", "yaml"}


def compute_hash(raw_data):
    """Return SHA256 hash for bytes."""
    return hashlib.sha256(raw_data).hexdigest()


def validate_by_extension(raw_data, ext):
    """Best-effort format validation for common file types."""
    ext = (ext or "").lower()

    if not raw_data:
        return "ok", "Empty file"

    if ext in TEXT_EXTENSIONS:
        try:
            raw_data.decode("utf-8")
            return "ok", "Valid UTF-8 text"
        except UnicodeDecodeError:
            return "corrupted", "Invalid UTF-8 encoding"

    if ext == "pdf":
        return ("ok", "Valid PDF header") if raw_data.startswith(b"%PDF-") else ("corrupted", "Invalid PDF header")

    if ext == "png":
        return ("ok", "Valid PNG signature") if raw_data.startswith(b"\x89PNG\r\n\x1a\n") else ("corrupted", "Invalid PNG signature")

    if ext in {"jpg", "jpeg"}:
        valid = len(raw_data) > 4 and raw_data[:2] == b"\xff\xd8" and raw_data[-2:] == b"\xff\xd9"
        return ("ok", "Valid JPEG markers") if valid else ("corrupted", "Invalid JPEG markers")

    if ext == "wav":
        valid = len(raw_data) >= 12 and raw_data[:4] == b"RIFF" and raw_data[8:12] == b"WAVE"
        return ("ok", "Valid WAV header") if valid else ("corrupted", "Invalid WAV header")

    if ext == "mp3":
        valid = raw_data.startswith(b"ID3") or (len(raw_data) > 1 and raw_data[0] == 0xFF and (raw_data[1] & 0xE0) == 0xE0)
        return ("ok", "Valid MP3 header") if valid else ("corrupted", "Invalid MP3 header")

    if ext == "mp4":
        probe = raw_data[:64]
        return ("ok", "Valid MP4 ftyp box") if b"ftyp" in probe else ("corrupted", "Missing MP4 ftyp box")

    return "ok", "Basic validation"


def salvage_bytes(raw_data, ext):
    """Best-effort salvage when no clean source exists."""
    ext = (ext or "").lower()

    if not raw_data:
        return "unrecoverable", raw_data, "File is empty"

    if ext in TEXT_EXTENSIONS:
        decoded = raw_data.decode("utf-8", errors="replace")
        repaired = decoded.encode("utf-8")
        return "partially_recovered", repaired, "Recovered text with replacement for damaged bytes"

    if ext == "pdf":
        start = raw_data.find(b"%PDF-")
        if start == -1:
            return "unrecoverable", raw_data, "PDF header not found"
        repaired = raw_data[start:]
        if b"%%EOF" not in repaired[-128:]:
            repaired += b"\n%%EOF\n"
        return "partially_recovered", repaired, "Rebuilt PDF boundaries"

    if ext in {"jpg", "jpeg"}:
        soi = raw_data.find(b"\xff\xd8")
        eoi = raw_data.rfind(b"\xff\xd9")
        if soi != -1 and eoi != -1 and soi < eoi:
            return "partially_recovered", raw_data[soi:eoi + 2], "Recovered JPEG frame range"
        return "unrecoverable", raw_data, "JPEG markers missing"

    if ext == "png":
        sig = b"\x89PNG\r\n\x1a\n"
        start = raw_data.find(sig)
        if start != -1:
            return "partially_recovered", raw_data[start:], "Recovered PNG data from signature"
        return "unrecoverable", raw_data, "PNG signature missing"

    if ext == "mp3":
        for i in range(max(0, len(raw_data) - 1)):
            if raw_data[i] == 0xFF and (raw_data[i + 1] & 0xE0) == 0xE0:
                return "partially_recovered", raw_data[i:], "Recovered MP3 stream from first audio frame"
        if raw_data.startswith(b"ID3"):
            return "partially_recovered", raw_data, "MP3 has metadata header"
        return "unrecoverable", raw_data, "No MP3 frame header found"

    if ext == "mp4":
        idx = raw_data.find(b"ftyp", 0, 4096)
        if idx != -1:
            start = max(0, idx - 4)
            return "partially_recovered", raw_data[start:], "Recovered MP4 container from ftyp box"
        return "unrecoverable", raw_data, "MP4 ftyp box not found"

    return "unrecoverable", raw_data, "No salvage strategy for this file type"

def create_backup_snapshot(inode, raw_data, source_type="system"):
    """Persist a verified clean snapshot for later full repair."""
    backup_name = f"{inode['id']}_{inode['name']}.clean.bak"
    backup_path = os.path.join(REPAIR_SOURCES_PATH, backup_name)
    with open(backup_path, "wb") as f:
        f.write(raw_data)

    inode["backup_path"] = backup_path
    inode["backup_hash"] = compute_hash(raw_data)
    inode["repairable"] = True
    inode["clean_source"] = source_type
    return backup_path


def mark_inode_by_integrity(inode, status, reason):
    """Sync inode/bitmap state with integrity status."""
    inode["integrity_status"] = status
    inode["integrity_reason"] = reason

    if inode.get("status") == "deleted":
        return

    if status == "corrupted":
        inode["status"] = "corrupted"
        for b in inode["blocks"]:
            file_system["bitmap"][b] = 3
    else:
        inode["status"] = "active"
        for b in inode["blocks"]:
            file_system["bitmap"][b] = 1


def integrity_payload(inode):
    """Compact integrity payload for API responses."""
    repairable = bool(inode.get("repairable")) and bool(inode.get("backup_path"))
    return {
        "status": inode.get("integrity_status", "ok"),
        "reason": inode.get("integrity_reason", ""),
        "can_repair": repairable,
        "repairable": repairable,
        "clean_source": inode.get("clean_source"),
        "repair_attempts": inode.get("repair_attempts", 0),
        "salvage_attempts": inode.get("salvage_attempts", 0),
        "recovery_outcome": inode.get("recovery_outcome"),
        "content_hash": inode.get("content_hash"),
    }


def create_file_internal(name, content):
    """Create a file from string content."""
    size = len(content.encode("utf-8"))
    n = blocks_needed(size)
    blocks = allocate_blocks(n)

    if blocks is None:
        return None

    for b in blocks:
        file_system["bitmap"][b] = 1

    iid = file_system["next_inode"]
    file_system["next_inode"] += 1

    file_path = os.path.join(WORKSPACE_PATH, name)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        log_event(f"Error writing file {name}: {str(e)}", "err")
        for b in blocks:
            file_system["bitmap"][b] = 0
        return None

    ext = name.split(".")[-1].lower() if "." in name else ""
    inode = {
        "id": iid,
        "name": name,
        "path": file_path,
        "ext": ext,
        "blocks": blocks,
        "size": size,
        "created": time.strftime("%H:%M:%S"),
        "modified": time.strftime("%H:%M:%S"),
        "status": "active",
    }

    file_system["registry"][iid] = inode
    return inode

def delete_file_internal(iid):
    """Soft-delete a file."""
    inode = file_system["registry"].get(iid)
    if not inode or inode["status"] == "deleted":
        return

    for b in inode["blocks"]:
        file_system["bitmap"][b] = 2

    inode["status"] = "deleted"

def seed_files():
    """Seed with sample files."""
    create_file_internal("readme.txt", "Welcome to DiskOS v2" + chr(10) + "This is now a real file system!")
    create_file_internal("notes.md", "# Notes" + chr(10) + "- Learn OS" + chr(10) + "- Understand file systems")
    create_file_internal("system.log", "[BOOT] System initialized" + chr(10) + "[OK] All services running")
    create_file_internal("script.js", "console.log('Hello DiskOS');")
    create_file_internal("bigdata.bin", "A" * 800)

    inode1 = create_file_internal("old_notes.txt", "This file was deleted")
    if inode1:
        delete_file_internal(inode1["id"])

    inode2 = create_file_internal("temp.log", "Temporary logs")
    if inode2:
        delete_file_internal(inode2["id"])

    log_event("Seeded 5 sample files (2 deleted)", "ok")

# Initialize
seed_files()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    """Return filesystem snapshot."""
    inodes_list = list(file_system["registry"].values())
    return jsonify({
        "bitmap": file_system["bitmap"],
        "inodes": inodes_list,
        "stats": build_stats(),
        "log": file_system["log"][:30],
    })

@app.route("/api/write", methods=["POST"])
def write_file():
    """Upload a file, classify integrity, and keep a repair snapshot."""
    name = request.form.get("name", "").strip()
    file = request.files.get("file")

    if not name:
        return jsonify({"error": "Filename required"}), 400
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    raw_data = file.read()

    if len(raw_data) > MAX_FILE_SIZE:
        return jsonify({"error": f"File too large (max {MAX_FILE_SIZE / (1024*1024):.0f} MB)"}), 413

    if file_exists_active(name):
        safe_name = get_safe_filename(name)
        log_event(f'RENAME "{name}" -> "{safe_name}" (conflict)', "warn")
        name = safe_name

    n = blocks_needed(len(raw_data))
    blocks = allocate_blocks(n)

    if not blocks:
        return jsonify({"error": "Disk full - not enough blocks"}), 507

    for b in blocks:
        file_system["bitmap"][b] = 1

    iid = file_system["next_inode"]
    file_system["next_inode"] += 1

    file_path = os.path.join(WORKSPACE_PATH, name)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        with open(file_path, "wb") as f:
            f.write(raw_data)
    except Exception as e:
        for b in blocks:
            file_system["bitmap"][b] = 0
        log_event(f'WRITE FAILED "{name}": {str(e)}', "err")
        return jsonify({"error": f"Failed to write file: {str(e)}"}), 500

    ext = name.split(".")[-1].lower() if "." in name else ""
    now = time.strftime("%H:%M:%S")
    file_hash = compute_hash(raw_data)
    status, reason = validate_by_extension(raw_data, ext)

    inode = {
        "id": iid,
        "name": name,
        "path": file_path,
        "ext": ext,
        "blocks": blocks,
        "size": len(raw_data),
        "created": now,
        "modified": now,
        "status": "active",
        "content_hash": file_hash,
        "backup_path": "",
        "backup_hash": "",
        "integrity_status": status,
        "integrity_reason": reason,
        "repaired_at": None,
        "repair_attempts": 0,
        "repairable": False,
        "clean_source": None,
        "salvage_attempts": 0,
        "recovery_outcome": None,
    }

    if status == "ok":
        try:
            create_backup_snapshot(inode, raw_data, source_type="upload")
        except Exception as e:
            inode["integrity_reason"] = f"{reason}; snapshot failed: {str(e)}"
            log_event(f'SNAPSHOT FAILED "{name}": {str(e)}', "warn")
    else:
        inode["integrity_reason"] = f"{reason}; no verified clean source"

    mark_inode_by_integrity(inode, status, inode["integrity_reason"])

    file_system["registry"][iid] = inode

    if status == "corrupted":
        log_event(f'UPLOAD "{name}" flagged corrupted: {inode["integrity_reason"]}', "warn")
    else:
        log_event(f'UPLOAD "{name}" ({len(raw_data)} bytes, {len(blocks)} blocks)', "ok")

    return jsonify({
        "ok": True,
        "inode_id": iid,
        "name": name,
        "size": len(raw_data),
        "blocks_used": len(blocks),
        "integrity": integrity_payload(inode),
    })

@app.route("/api/read/<int:iid>")
def read_file(iid):
    """Read a file and return content + integrity info."""
    inode = file_system["registry"].get(iid)

    if not inode:
        return jsonify({"error": "Not found"}), 404
    if inode["status"] == "deleted":
        return jsonify({"error": "File deleted"}), 410

    try:
        with open(inode["path"], "rb") as f:
            raw_data = f.read()
            content = base64.b64encode(raw_data).decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"Read failed: {str(e)}"}), 500

    current_hash = compute_hash(raw_data)
    if inode.get("content_hash") and inode["content_hash"] != current_hash:
        mark_inode_by_integrity(inode, "corrupted", "Checksum mismatch")
        inode["repairable"] = bool(inode.get("backup_path")) and bool(inode.get("backup_hash"))
    elif inode.get("integrity_status") == "partially_recovered":
        mark_inode_by_integrity(inode, "partially_recovered", inode.get("integrity_reason", "Partially recovered"))
    elif inode.get("status") == "corrupted":
        mark_inode_by_integrity(inode, "corrupted", inode.get("integrity_reason", "Corrupted"))
    else:
        mark_inode_by_integrity(inode, "ok", inode.get("integrity_reason", "Integrity verified"))

    return jsonify({
        "content": content,
        "name": inode["name"],
        "ext": inode["ext"],
        "size": inode["size"],
        "inode": inode,
        "integrity": integrity_payload(inode),
    })

@app.route("/api/update/<int:iid>", methods=["PUT"])
def update_file(iid):
    """Update a text file and refresh integrity metadata."""
    inode = file_system["registry"].get(iid)
    if not inode:
        return jsonify({"error": "Inode not found"}), 404
    if inode["status"] == "deleted":
        return jsonify({"error": "Cannot update a deleted file"}), 410

    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    new_content = data.get("content", "")

    if not new_name:
        return jsonify({"error": "Filename is required"}), 400

    if new_name != inode["name"] and file_exists_active(new_name):
        return jsonify({"error": f'File "{new_name}" already exists'}), 409

    raw_data = new_content.encode("utf-8")
    new_size = len(raw_data)

    if new_size > MAX_FILE_SIZE:
        return jsonify({"error": f"File too large (max {MAX_FILE_SIZE / (1024*1024):.0f} MB)"}), 413

    n_needed = blocks_needed(new_size)
    n_current = len(inode["blocks"])
    blocks_changed = n_current != n_needed

    old_name = inode["name"]
    old_path = inode["path"]
    old_blocks = list(inode["blocks"])

    if blocks_changed:
        for b in old_blocks:
            file_system["bitmap"][b] = 0

        new_blocks = allocate_blocks(n_needed)
        if new_blocks is None:
            for b in old_blocks:
                file_system["bitmap"][b] = 1
            return jsonify({"error": "Disk full - cannot reallocate"}), 507

        for b in new_blocks:
            file_system["bitmap"][b] = 1
        inode["blocks"] = new_blocks

    new_path = os.path.join(WORKSPACE_PATH, new_name)

    try:
        if old_path != new_path and os.path.exists(old_path):
            os.rename(old_path, new_path)

        with open(new_path, "wb") as f:
            f.write(raw_data)

    except Exception as e:
        if blocks_changed:
            for b in inode["blocks"]:
                file_system["bitmap"][b] = 0
            inode["blocks"] = old_blocks
            for b in old_blocks:
                file_system["bitmap"][b] = 1

        log_event(f'UPDATE FAILED "{old_name}": {str(e)}', "err")
        return jsonify({"error": f"Update failed: {str(e)}"}), 500

    ext = new_name.rsplit(".", 1)[-1].lower() if "." in new_name else ""
    status, reason = validate_by_extension(raw_data, ext)

    inode.update({
        "name": new_name,
        "path": new_path,
        "ext": ext,
        "size": new_size,
        "modified": time.strftime("%H:%M:%S"),
        "content_hash": compute_hash(raw_data),
    })

    if status == "ok":
        try:
            create_backup_snapshot(inode, raw_data, source_type="update")
        except Exception as e:
            reason = f"{reason}; snapshot failed: {str(e)}"
            log_event(f'SNAPSHOT FAILED "{new_name}": {str(e)}', "warn")
    else:
        reason = f"{reason}; last clean source required for full repair"

    mark_inode_by_integrity(inode, status, reason)

    action = f'UPDATE "{old_name}"' if old_name == new_name else f'UPDATE "{old_name}" -> "{new_name}"'
    msg = f'{action} ({n_current}->{n_needed} blocks, {len(new_content)} chars)'

    if inode["status"] == "corrupted":
        log_event(f'{msg} - flagged corrupted: {reason}', "warn")
    else:
        log_event(msg, "ok")

    return jsonify({
        "ok": True,
        "inode": inode,
        "blocks_reallocated": blocks_changed,
        "integrity": integrity_payload(inode),
    })

@app.route("/api/delete/<int:iid>", methods=["DELETE"])
def delete_file(iid):
    """Soft-delete a file."""
    inode = file_system["registry"].get(iid)

    if not inode or inode["status"] == "deleted":
        return jsonify({"error": "Not found"}), 404

    delete_file_internal(iid)
    log_event(f'DELETE "{inode["name"]}"', "warn")

    return jsonify({"ok": True})


@app.route("/api/purge/<int:iid>", methods=["DELETE"])
def purge_file(iid):
    """Permanently remove file bytes + inode metadata from disk."""
    inode = file_system["registry"].get(iid)
    if not inode:
        return jsonify({"error": "Not found"}), 404

    name = inode.get("name", f"inode#{iid}")

    for b in inode.get("blocks", []):
        if 0 <= b < len(file_system["bitmap"]):
            file_system["bitmap"][b] = 0

    paths_to_remove = set()
    for key in ("path", "backup_path"):
        val = inode.get(key)
        if val:
            paths_to_remove.add(val)

    if inode.get("name"):
        paths_to_remove.add(os.path.join(BACKUPS_PATH, f"{inode['name']}.bak"))
        paths_to_remove.add(os.path.join(REPAIR_SOURCES_PATH, f"{inode['id']}_{inode['name']}.clean.bak"))

    for path in paths_to_remove:
        try:
            if path and os.path.exists(path) and os.path.isfile(path):
                os.unlink(path)
        except Exception:
            # keep purge resilient even if a sidecar backup cannot be removed
            pass

    file_system["registry"].pop(iid, None)
    log_event(f'PURGE "{name}" (permanent remove)', "err")

    return jsonify({"ok": True, "purged": name, "inode_id": iid})


@app.route("/api/recover", methods=["POST"])
def recover():
    """Recover deleted files."""
    data = request.get_json(silent=True) or {}
    iid = data.get("inode_id")

    targets = (
        [file_system["registry"][iid]]
        if iid and iid in file_system["registry"]
        else list(file_system["registry"].values())
    )

    recovered, skipped = [], []
    for inode in targets:
        if inode["status"] != "deleted":
            continue
        
        conflict = any(file_system["bitmap"][b] == 1 for b in inode["blocks"])
        if conflict:
            skipped.append(inode["name"])
            continue
        
        for b in inode["blocks"]:
            file_system["bitmap"][b] = 1
        
        inode["status"] = "active"
        recovered.append(inode["name"])

    if recovered:
        log_event(f'RECOVER {recovered}', "ok")
    
    return jsonify({"ok": True, "recovered": recovered, "skipped": skipped})

@app.route("/api/defrag", methods=["POST"])
def defrag():
    """Defragment disk."""
    active = [i for i in file_system["registry"].values() if i["status"] == "active"]
    
    file_system["bitmap"] = [0] * TOTAL_BLOCKS
    ptr = 0
    
    for inode in active:
        new_blocks = list(range(ptr, ptr + len(inode["blocks"])))
        for b in new_blocks:
            file_system["bitmap"][b] = 1
        inode["blocks"] = new_blocks
        ptr += len(new_blocks)
    
    log_event(f'DEFRAG - {len(active)} files', "ok")
    return jsonify({"ok": True, "files_moved": len(active)})

@app.route('/api/format', methods=['POST'])
def format_disk():
    """Format disk."""
    global file_system
    
    for file in os.listdir(WORKSPACE_PATH):
        path = os.path.join(WORKSPACE_PATH, file)
        if os.path.isfile(path):
            os.unlink(path)
    
    file_system = {
        "bitmap": [0] * TOTAL_BLOCKS,
        "registry": {},
        "next_inode": 1,
        "log": [],
    }
    
    seed_files()
    return jsonify({"msg": "Disk formatted"})

@app.route("/api/crash", methods=["POST"])
def crash():
    """[STEP 4] Simulate disk crash by corrupting file bytes."""
    data = request.get_json(silent=True) or {}
    corruption_level = data.get("level", 0.3)  # 30% chance per file
    
    corrupted = []

    for inode in file_system["registry"].values():
        if inode["status"] == "active" and random.random() < corruption_level:
            try:
                # Save verified clean source before corruption
                with open(inode['path'], 'rb') as f:
                    original_data = f.read()
                create_backup_snapshot(inode, original_data, source_type="pre-crash")
                
                # Corrupt the file (overwrite with garbage)
                corrupted_data = bytearray(original_data)
                # Corrupt random bytes (10-30% of file)
                corruption_count = max(1, len(corrupted_data) // random.randint(3, 10))
                for _ in range(corruption_count):
                    pos = random.randint(0, len(corrupted_data) - 1)
                    corrupted_data[pos] = random.randint(0, 255)
                
                with open(inode['path'], 'wb') as f:
                    f.write(corrupted_data)
                
                # Mark as corrupted
                inode["status"] = "corrupted"
                inode["integrity_status"] = "corrupted"
                inode["integrity_reason"] = "Crash corruption detected"
                for b in inode["blocks"]:
                    file_system["bitmap"][b] = 3
                
                corrupted.append(inode["name"])
                log_event(f'CORRUPTED "{inode["name"]}" (backup created)', "err")
                
            except Exception as e:
                log_event(f'CRASH failed on "{inode["name"]}": {str(e)}', "err")

    if corrupted:
        log_event(f'CRASH - Corrupted: {corrupted}', "err")
    
    return jsonify({
        "ok": True,
        "msg": f"Crash simulated - {len(corrupted)} file(s) corrupted",
        "corrupted_files": corrupted
    })


@app.route("/api/repair", methods=["POST"])
def repair():
    """Repair corrupted files using stored backup snapshots."""
    data = request.get_json(silent=True) or {}
    iid = data.get("inode_id")

    targets = (
        [file_system["registry"][iid]]
        if iid and iid in file_system["registry"]
        else list(file_system["registry"].values())
    )

    repaired, failed, details = [], [], []

    for inode in targets:
        if inode["status"] != "corrupted":
            continue

        inode["repair_attempts"] = inode.get("repair_attempts", 0) + 1

        try:
            backup_path = inode.get("backup_path")

            if not inode.get("repairable") or not backup_path or not os.path.exists(backup_path):
                failed.append(inode["name"])
                reason = "no verified clean source; upload a clean source file"
                details.append({"inode_id": inode["id"], "name": inode["name"], "repaired": False, "reason": reason})
                log_event(f'REPAIR FAILED "{inode["name"]}" - {reason}', "warn")
                continue

            with open(backup_path, "rb") as f:
                backup_data = f.read()

            backup_hash = compute_hash(backup_data)
            expected_backup_hash = inode.get("backup_hash")
            if expected_backup_hash and backup_hash != expected_backup_hash:
                failed.append(inode["name"])
                reason = "backup integrity mismatch"
                details.append({"inode_id": inode["id"], "name": inode["name"], "repaired": False, "reason": reason})
                log_event(f'REPAIR FAILED "{inode["name"]}" - {reason}', "err")
                continue

            with open(inode["path"], "wb") as f:
                f.write(backup_data)

            inode["status"] = "active"
            inode["integrity_status"] = "ok"
            inode["integrity_reason"] = "Fully repaired from verified clean source"
            inode["size"] = len(backup_data)
            inode["content_hash"] = backup_hash
            inode["recovery_outcome"] = "fully_fixed"
            inode["repaired_at"] = time.strftime("%H:%M:%S")
            inode["modified"] = time.strftime("%H:%M:%S")

            for b in inode["blocks"]:
                file_system["bitmap"][b] = 1

            repaired.append(inode["name"])
            details.append({
                "inode_id": inode["id"],
                "name": inode["name"],
                "repaired": True,
                "reason": "fully repaired from verified clean source",
                "fixed_hash": backup_hash,
            })
            log_event(f'REPAIRED "{inode["name"]}" from backup', "ok")

        except Exception as e:
            failed.append(inode["name"])
            details.append({"inode_id": inode["id"], "name": inode["name"], "repaired": False, "reason": str(e)})
            log_event(f'REPAIR ERROR "{inode["name"]}": {str(e)}', "err")

    return jsonify({
        "ok": True,
        "repaired": repaired,
        "failed": failed,
        "details": details,
        "msg": f"Repaired {len(repaired)}, Failed {len(failed)}"
    })


@app.route("/api/repair-source/<int:iid>", methods=["POST"])
def upload_repair_source(iid):
    """Attach a verified clean source file for a corrupted inode."""
    inode = file_system["registry"].get(iid)
    if not inode:
        return jsonify({"error": "Inode not found"}), 404

    src = request.files.get("file")
    if not src:
        return jsonify({"error": "No repair source uploaded"}), 400

    raw_data = src.read()
    if len(raw_data) > MAX_FILE_SIZE:
        return jsonify({"error": f"File too large (max {MAX_FILE_SIZE / (1024*1024):.0f} MB)"}), 413

    src_name = (src.filename or "").strip()
    if src_name and "." in src_name and inode.get("ext"):
        src_ext = src_name.rsplit(".", 1)[-1].lower()
        if src_ext != inode.get("ext"):
            return jsonify({"error": f"Repair source must be .{inode.get('ext')}"}), 400

    status, reason = validate_by_extension(raw_data, inode.get("ext", ""))
    if status != "ok":
        return jsonify({"error": f"Repair source is invalid: {reason}"}), 400

    create_backup_snapshot(inode, raw_data, source_type="user-upload")
    log_event(f'REPAIR SOURCE attached for "{inode["name"]}"', "ok")

    return jsonify({
        "ok": True,
        "inode_id": iid,
        "msg": "Verified clean source uploaded",
        "integrity": integrity_payload(inode),
    })



@app.route("/api/salvage/<int:iid>", methods=["POST"])
def salvage_file(iid):
    """Try best-effort recovery without a clean source."""
    inode = file_system["registry"].get(iid)
    if not inode:
        return jsonify({"error": "Inode not found"}), 404
    if inode.get("status") == "deleted":
        return jsonify({"error": "Cannot salvage a deleted file"}), 410

    try:
        with open(inode["path"], "rb") as f:
            raw_data = f.read()
    except Exception as e:
        return jsonify({"error": f"Read failed: {str(e)}"}), 500

    outcome, repaired_data, reason = salvage_bytes(raw_data, inode.get("ext", ""))
    inode["salvage_attempts"] = inode.get("salvage_attempts", 0) + 1

    if outcome == "unrecoverable":
        inode["status"] = "corrupted"
        inode["integrity_status"] = "corrupted"
        inode["integrity_reason"] = f"Unrecoverable without clean source: {reason}"
        inode["recovery_outcome"] = "unrecoverable"
        for b in inode["blocks"]:
            file_system["bitmap"][b] = 3

        log_event(f'SALVAGE FAILED "{inode["name"]}" - {reason}', "warn")
        return jsonify({
            "ok": True,
            "outcome": "unrecoverable",
            "reason": reason,
            "inode": inode,
            "integrity": integrity_payload(inode),
        })

    try:
        with open(inode["path"], "wb") as f:
            f.write(repaired_data)
    except Exception as e:
        return jsonify({"error": f"Write failed: {str(e)}"}), 500

    inode["status"] = "active"
    inode["integrity_status"] = "partially_recovered"
    inode["integrity_reason"] = reason
    inode["recovery_outcome"] = "partially_recovered"
    inode["size"] = len(repaired_data)
    inode["content_hash"] = compute_hash(repaired_data)
    inode["modified"] = time.strftime("%H:%M:%S")
    for b in inode["blocks"]:
        file_system["bitmap"][b] = 1

    log_event(f'SALVAGE PARTIAL "{inode["name"]}" - {reason}', "ok")
    return jsonify({
        "ok": True,
        "outcome": "partially_recovered",
        "reason": reason,
        "inode": inode,
        "integrity": integrity_payload(inode),
    })


@app.route("/api/suggest")
def suggest():
    """Get suggestions."""
    suggestions = []

    if calc_fragmentation() > 30:
        suggestions.append("High fragmentation - defrag recommended")

    if file_system["bitmap"].count(2) > 0:
        suggestions.append("Deleted files exist - recovery possible")

    if build_stats()["capacity_pct"] > 80:
        suggestions.append("Disk almost full")

    if not suggestions:
        suggestions.append("System is optimized")

    return jsonify({"suggestions": suggestions})
    

if __name__ == "__main__":
    app.run(debug=True, port=5000)
