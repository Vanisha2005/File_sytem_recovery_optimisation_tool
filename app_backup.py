from flask import Flask, render_template, jsonify, request
import time, math
import random
import base64

app = Flask(__name__)

# ─── DISK CONFIGURATION ───────────────────────────────────────────────────────
TOTAL_BLOCKS = 512   
BLOCK_SIZE   = 256   # bytes per block

# ─── FILESYSTEM STATE ─────────────────────────────────────────────────────────
fs = {
    "bitmap":     [0] * TOTAL_BLOCKS,   # 0=free  1=used  2=deleted
    "inodes":     {},                    # id → inode dict
    "next_inode": 1,
    "log":        [],
}

def seed_files():
    create_file_internal("readme.txt", "Welcome to DiskOS v2 🚀\nThis is a virtual file system.")
    create_file_internal("notes.md", "# Notes\n- Learn OS\n- Understand file systems")
    create_file_internal("system.log", "[BOOT] System initialized\n[OK] All services running")
    create_file_internal("script.js", "console.log('Hello DiskOS');")

    create_file_internal("bigdata.bin", "A" * 800)

    inode1 = create_file_internal("old_notes.txt", "This file was deleted")
    delete_file_internal(inode1["id"])

    inode2 = create_file_internal("temp.log", "Temporary logs")
    delete_file_internal(inode2["id"])

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def log_event(msg, level="info"):
    """Prepend a timestamped event to the system log (max 60 entries)."""
    fs["log"].insert(0, {
        "msg":   msg,
        "level": level,
        "time":  time.strftime("%H:%M:%S"),
    })
    if len(fs["log"]) > 60:
        fs["log"].pop()


def blocks_needed(content: str) -> int:
    return max(1, math.ceil(len(content.encode("utf-8")) / BLOCK_SIZE))


def allocate_blocks(n: int):
    """Return the first n free block indices, or None if disk is full."""
    free = [i for i, v in enumerate(fs["bitmap"]) if v == 0]
    return free[:n] if len(free) >= n else None


def calc_fragmentation() -> int:
    """Percentage of gaps between active file blocks (0–100)."""
    used = sorted(
        b for inode in fs["inodes"].values()
        if inode["status"] == "active"
        for b in inode["blocks"]
    )
    if len(used) < 2:
        return 0
    gaps = sum(1 for i in range(1, len(used)) if used[i] - used[i - 1] > 1)
    return round(gaps / (len(used) - 1) * 100)


def build_stats() -> dict:
    used   = fs["bitmap"].count(1)
    free   = fs["bitmap"].count(0)
    deleted_blocks = fs["bitmap"].count(2)
    return {
        "total":          TOTAL_BLOCKS,
        "block_size":     BLOCK_SIZE,
        "used":           used,
        "free":           free,
        "deleted_blocks": deleted_blocks,
        "capacity_pct":   round(used / TOTAL_BLOCKS * 100),
        "files":          sum(1 for i in fs["inodes"].values() if i["status"] == "active"),
        "deleted_files":  sum(1 for i in fs["inodes"].values() if i["status"] == "deleted"),
        "fragmentation":  calc_fragmentation(),
    }

def create_file_internal(name, content):
    n = max(1, math.ceil(len(content.encode("utf-8")) / BLOCK_SIZE))
    blocks = allocate_blocks(n)

    if blocks is None:
        return None

    for b in blocks:
        fs["bitmap"][b] = 1

    iid = fs["next_inode"]
    fs["next_inode"] += 1

    ext = name.split(".")[-1].lower()

    inode = {
        "id": iid,
        "name": name,
        "content": content,
        "ext": ext,
        "blocks": blocks,
        "size": len(content.encode()),
        "created": "boot",
        "modified": "boot",
        "status": "active",
    }

    fs["inodes"][iid] = inode
    return inode


def delete_file_internal(iid):
    inode = fs["inodes"].get(iid)
    if not inode or inode["status"] == "deleted":
        return

    for b in inode["blocks"]:
        fs["bitmap"][b] = 2

    inode["status"] = "deleted"

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    """Return full filesystem snapshot."""
    return jsonify({
        "bitmap": fs["bitmap"],
        "inodes": list(fs["inodes"].values()),
        "stats":  build_stats(),
        "log":    fs["log"][:30],
    })


@app.route("/api/write", methods=["POST"])
def write_file():
    """Create a new file (supports real file upload via base64)."""
    
    name = request.form.get("name", "").strip()
    file = request.files.get("file")

    if not name:
        return jsonify({"error": "Filename required"}), 400

    if not file:
        return jsonify({"error": "No file uploaded"}), 400


    # Convert file to base64 (store safely)
    raw_data = file.read()
    n = max(1, math.ceil(len(raw_data) / BLOCK_SIZE))
    blocks = allocate_blocks(n)
    # content = base64.b64encode(raw_data).decode("utf-8")
    if len(raw_data) > 50000:   # ~20 KB limit
       return jsonify({"error": "File too large for this disk"}), 400
    content = base64.b64encode(raw_data).decode("utf-8")

# use ORIGINAL size for block calculation


    if not blocks:
       return jsonify({"error": "Disk full"}), 507

    for b in blocks:
        fs["bitmap"][b] = 1

    iid = fs["next_inode"]
    fs["next_inode"] += 1

    inode = {
        "id": iid,
        "name": name,
        "content": content,
        "ext": name.split(".")[-1].lower(),
        "blocks": blocks,
        "size": len(raw_data),
        "created": time.strftime("%H:%M:%S"),
        "modified": time.strftime("%H:%M:%S"),
        "status": "active",
    }

    fs["inodes"][iid] = inode
    # inode = create_file_internal(name, content)

    if inode is None:
     return jsonify({"error": "Disk full"}), 507

# fix size + timestamps (since internal uses default)
    inode["size"] = len(raw_data)
    inode["created"] = time.strftime("%H:%M:%S")
    inode["modified"] = time.strftime("%H:%M:%S")

    log_event(f'UPLOAD "{name}" → blocks {inode["blocks"]}', "ok")

    return jsonify({"ok": True})


@app.route("/api/read/<int:iid>")
def read_file(iid: int):
    inode = fs["inodes"].get(iid)

    if not inode:
        return jsonify({"error": "Not found"}), 404

    if inode["status"] == "deleted":
        return jsonify({"error": "File deleted"}), 410

    return jsonify({
        "content": inode["content"],
        "name": inode["name"],
        "ext": inode["ext"],
        "inode": inode
    })


@app.route("/api/update/<int:iid>", methods=["PUT"])
def update_file(iid: int):
    """Overwrite a file's content, reallocating blocks as needed."""
    inode = fs["inodes"].get(iid)
    if not inode:
        return jsonify({"error": "Inode not found"}), 404
    if inode["status"] == "deleted":
        return jsonify({"error": "Cannot update a deleted file"}), 410

    data       = request.json or {}
    new_name   = (data.get("name") or "").strip()
    new_content = data.get("content", "")

    if not new_name:
        return jsonify({"error": "Filename is required"}), 400

    # Check name collision (exclude self)
    if new_name != inode["name"]:
        if any(i["name"] == new_name and i["status"] == "active" and i["id"] != iid
               for i in fs["inodes"].values()):
            return jsonify({"error": f'"{new_name}" already exists'}), 409

    n_needed = blocks_needed(new_content)
    n_current = len(inode["blocks"])

    # Free old blocks
    for b in inode["blocks"]:
        fs["bitmap"][b] = 0

    # Allocate new blocks
    new_blocks = allocate_blocks(n_needed)
    if new_blocks is None:
        # Restore old allocation on failure
        for b in inode["blocks"]:
            fs["bitmap"][b] = 1
        return jsonify({"error": "Disk full — cannot reallocate blocks"}), 507

    for b in new_blocks:
        fs["bitmap"][b] = 1

    old_name = inode["name"]
    inode.update({
        "name":     new_name,
        "content":  new_content,
        "ext":      new_name.rsplit(".", 1)[-1].lower() if "." in new_name else "",
        "blocks":   new_blocks,
        "size":     len(new_content.encode("utf-8")),
        "modified": time.strftime("%H:%M:%S"),
    })

    log_event(
        f'UPDATE "{old_name}"→"{new_name}" | {n_current}→{n_needed} block(s) {new_blocks}',
        "ok",
    )
    return jsonify({"ok": True, "inode": inode})


@app.route("/api/delete/<int:iid>", methods=["DELETE"])
def delete_file(iid: int):
    inode = fs["inodes"].get(iid)

    if not inode or inode["status"] == "deleted":
        return jsonify({"error": "File not found or already deleted"}), 404

    # ✅ use internal logic
    delete_file_internal(iid)

    log_event(f'DELETE "{inode["name"]}" | blocks {inode["blocks"]} reclaimable', "warn")

    return jsonify({"ok": True})

@app.route("/api/recover", methods=["POST"])
def recover():
    """Selective or bulk recovery.
    Body: { "inode_id": int }  →  recover single file
    Body: {}                   →  recover all recoverable files
    """
    data = request.json or {}
    iid  = data.get("inode_id")

    targets = (
        [fs["inodes"][iid]]
        if iid and iid in fs["inodes"]
        else list(fs["inodes"].values())
    )

    recovered, skipped = [], []
    for inode in targets:
        if inode["status"] != "deleted":
            continue
        conflict = any(fs["bitmap"][b] == 1 for b in inode["blocks"])
        if conflict:
            skipped.append(inode["name"])
            continue
        for b in inode["blocks"]:
            fs["bitmap"][b] = 1
        inode["status"] = "active"
        recovered.append(inode["name"])

    if recovered:
        log_event(f'RECOVER {recovered} ✓', "ok")
    if skipped:
        log_event(f'RECOVER SKIP {skipped} — blocks overwritten', "warn")
    if not recovered and not skipped:
        log_event("RECOVER — nothing to restore", "info")

    return jsonify({"ok": True, "recovered": recovered, "skipped": skipped})

@app.route("/api/repair", methods=["POST"])
def repair():
    repaired = []

    for f in fs["inodes"].values():
        if f["status"] == "corrupted":
            blocks = allocate_blocks(len(f["blocks"]))
            if not blocks:
                continue

            for b in blocks:
                fs["bitmap"][b] = 1

            f["blocks"] = blocks
            f["status"] = "active"
            repaired.append(f["name"])

    log_event(f"REPAIR — restored {repaired}", "ok")

    return jsonify({"ok": True, "repaired": repaired})


@app.route("/api/defrag", methods=["POST"])
def defrag():
    """Pack all active files into contiguous blocks from block 0."""
    active = [i for i in fs["inodes"].values() if i["status"] == "active"]
    # Reset all bitmap entries to free first
    fs["bitmap"] = [0] * TOTAL_BLOCKS
    ptr = 0
    for inode in active:
        new_blocks = list(range(ptr, ptr + len(inode["blocks"])))
        for b in new_blocks:
            fs["bitmap"][b] = 1
        inode["blocks"] = new_blocks
        ptr += len(new_blocks)
    log_event(
        f'DEFRAG — {len(active)} file(s) packed into blocks 0–{max(0, ptr - 1)}',
        "ok",
    )
    return jsonify({"ok": True, "files_moved": len(active), "last_block": ptr - 1})

@app.route('/api/format', methods=['POST'])
def format_disk():
    global fs
    fs = {
        "bitmap": [0] * TOTAL_BLOCKS,
        "inodes": {},
        "next_inode": 1, 
        "log": []
    }

    seed_files()  # 👈 ADD THIS LINE

    return jsonify({"msg": "Disk formatted"})

# ================= CRASH SIMULATION =================
@app.route("/api/crash", methods=["POST"])
def crash():
    corrupted = []

    for f in fs["inodes"].values():
        if f["status"] == "active" and random.random() < 0.3:
            f["status"] = "corrupted"

            # Mark blocks as corrupted (NEW STATE = 3)
            for b in f["blocks"]:
                fs["bitmap"][b] = 3

            corrupted.append(f["name"])

    log_event(f"CRASH — corrupted files: {corrupted}", "err")

    return jsonify({
        "ok": True,
        "msg": f"Crash simulated. Corrupted files: {corrupted}",
        "stats": build_stats(),
        "inodes": list(fs["inodes"].values())
    })
# ================= SUGGESTIONS =================

@app.route("/api/suggest")
def suggest():
    suggestions = []

    for f in fs["inodes"].values():
        if f["status"] != "active":
            continue

        if len(f["blocks"]) > 5:
            suggestions.append(f"{f['name']} is fragmented → defrag recommended")

    if calc_fragmentation() > 30:
        suggestions.append("High fragmentation → run defragmentation")

    if fs["bitmap"].count(2) > 0:
        suggestions.append("Deleted blocks exist → recovery possible")

    if any(f["status"] == "corrupted" for f in fs["inodes"].values()):
        suggestions.append("Corrupted files detected → run repair system")

    if build_stats()["capacity_pct"] > 80:
        suggestions.append("Disk almost full → consider deleting files")

    if not suggestions:
        suggestions.append("System is optimized")

    return jsonify({"suggestions": suggestions})


# ─── DEMO SEED ────────────────────────────────────────────────────────────────

def seed():
    demos = [
        ("kernel.bin",
         "ELF64 LE x86-64: core kernel. Manages memory, scheduling, IPC, and syscall dispatch."),
        ("config.yaml",
         "version: 3\nfs: diskos\nblock_size: 64\ntotal: 128\nbitmap: enabled\njournal: true\nmount: rw"),
        ("readme.txt",
         "DiskOS File System v2\nAuthor: Vanisha\n\nFeatures:\n- Bitmap allocation\n- Inode table\n- CRUD + Update\n- Selective recovery\n- Defragmentation\n- Full disk simulation"),
        ("photo.jpg",
         "FFD8FFE0 JFIF... [simulated JPEG — 2.1 KB compressed image data for block-fill demo]"),
        ("notes.md",
         "## Project TODOs\n- [x] Bitmap allocator\n- [x] Inode structure\n- [x] File CRUD\n- [x] Update/realloc\n- [x] Selective recover\n- [x] Defrag\n- [ ] Journaling\n- [ ] Permissions"),
        ("archive.zip",
         "PK\\x03\\x04 [ZIP: 3 entries — report.pdf, data.csv, screenshot.png — simulated archive]"),
    ]
    for name, content in demos:
        n      = blocks_needed(content)
        blocks = allocate_blocks(n)
        if not blocks:
            continue
        for b in blocks:
            fs["bitmap"][b] = 1
        iid = fs["next_inode"]
        fs["next_inode"] += 1
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        fs["inodes"][iid] = {
            "id": iid, "name": name, "content": content, "ext": ext,
            "blocks": blocks, "size": len(content.encode()),
            "created": "boot", "modified": "boot", "status": "active",
        }

    # Soft-delete one file to demonstrate recovery
    target = fs["inodes"].get(3)
    if target:
        for b in target["blocks"]:
            fs["bitmap"][b] = 2
        target["status"] = "deleted"

    log_event("DiskOS v2 mounted — 128 blocks @ 64 B", "ok")
    log_event('"readme.txt" soft-deleted — try Selective Recovery', "warn")


seed()
seed_files()   # 👈 ADD THIS

if __name__ == "__main__":
    app.run(debug=True, port=5000)
