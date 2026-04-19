// ═══════════════════════════════════════════════════════════════════════════
//  DiskOS v2 — Frontend Logic
// ═══════════════════════════════════════════════════════════════════════════

// ── STATE ────────────────────────────────────────────────────────────────────
let state          = { bitmap: [], inodes: [], stats: {}, log: [] };
let filterMode     = 'all';
let selectedId     = null;          // currently selected inode id
let currentEditId  = null;          // inode being edited (null = write mode)
let writing        = false;         // debounce flag
let prevLogLen     = 0;

const EDITABLE_TEXT_EXTS = new Set(['txt','md','log','json','csv','xml','yml','yaml','toml','ini','cfg','conf','py','js','ts','html','css','sql','sh','bat','ps1','java','c','cpp','h','hpp','go','rs','php','rb','swift','kt','scala','dart','r','m','tex','rst']);

function bytesFromBase64(base64) {
  const binary = atob(base64 || '');
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function decodeBytesToText(bytes) {
  const decoder = new TextDecoder('utf-8', { fatal: false });
  return decoder.decode(bytes);
}

function isLikelyTextBytes(bytes) {
  if (!bytes || bytes.length === 0) return true;
  let control = 0;
  const sample = Math.min(bytes.length, 4096);
  for (let i = 0; i < sample; i++) {
    const b = bytes[i];
    if (b === 0) return false;
    if (b < 9 || (b > 13 && b < 32)) control++;
  }
  return control / sample < 0.08;
}

function escapeHtmlAttr(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function closePreviewLightbox() {
  const lightbox = q('#preview-lightbox');
  const content = q('#preview-lightbox-content');
  if (!lightbox || !content) return;
  lightbox.classList.remove('is-open');
  lightbox.setAttribute('aria-hidden', 'true');
  content.innerHTML = '';
  document.body.classList.remove('lightbox-open');
}

function openPreviewLightbox(type, src, title) {
  const lightbox = q('#preview-lightbox');
  const content = q('#preview-lightbox-content');
  const titleEl = q('#preview-lightbox-title');
  if (!lightbox || !content || !titleEl) return;

  let markup = '';
  if (type === 'image') {
    markup = `<img src="${src}" alt="${escapeHtmlAttr(title || 'Preview image')}">`;
  } else if (type === 'video') {
    markup = `<video controls autoplay><source src="${src}"></video>`;
  } else if (type === 'pdf') {
    markup = `<iframe src="${src}" title="${escapeHtmlAttr(title || 'PDF preview')}"></iframe>`;
  }

  if (!markup) return;
  titleEl.textContent = title || 'PREVIEW';
  content.innerHTML = markup;
  lightbox.classList.add('is-open');
  lightbox.setAttribute('aria-hidden', 'false');
  document.body.classList.add('lightbox-open');
}


function toggleFullscreen() {
  const elem = q('#preview-lightbox-content').firstElementChild;
  if (!elem) return;
  if (!document.fullscreenElement) {
    if (elem.requestFullscreen) {
      elem.requestFullscreen().catch(err => {
        console.error("Error attempting to enable fullscreen:", err);
      });
    }
  } else {
    if (document.exitFullscreen) {
      document.exitFullscreen();
    }
  }
}

function setupPreviewLightboxEvents() {
  const lightbox = q('#preview-lightbox');
  const fsBtn = q('#preview-lightbox-fullscreen');
  if (fsBtn) {
    fsBtn.addEventListener('click', toggleFullscreen);
  }

  const closeBtn = q('#preview-lightbox-close');
  if (!lightbox || !closeBtn || lightbox.dataset.bound === '1') return;

  closeBtn.addEventListener('click', closePreviewLightbox);
  lightbox.addEventListener('click', e => {
    if (e.target && e.target.dataset && e.target.dataset.closeLightbox === '1') closePreviewLightbox();
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && lightbox.classList.contains('is-open')) closePreviewLightbox();
  });

  lightbox.dataset.bound = '1';
}

function bindViewerPreviewOpen(type, src, title) {
  const viewer = q('#viewer-body');
  if (!viewer) return;
  const target = viewer.querySelector('.preview-img, .preview-video, .preview-pdf');
  if (!target) return;

  viewer.classList.add('has-rich-preview');
  target.setAttribute('tabindex', '0');
  target.setAttribute('role', 'button');
  target.setAttribute('aria-label', `Open larger preview for ${title || 'file'}`);

  const open = () => openPreviewLightbox(type, src, title);
  target.addEventListener('click', open);
  target.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  });
}

function clearViewerPreviewMode() {
  q('#viewer-body')?.classList.remove('has-rich-preview');
}

// ── BOOT ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  fetchState();
  setInterval(fetchState, 2000);

  document.getElementById('f-content').addEventListener('input', updateSizePreview);
  document.getElementById('f-name').addEventListener('input', updateSizePreview);

  const fileInput = document.getElementById("f-file");
  const folderInput = document.getElementById("f-folder");
  if (folderInput) {
    folderInput.addEventListener("change", e => {
      const count = e.target.files.length;
      if (count > 0) {
        document.getElementById("file-name").textContent = count + " files in folder";
        const path = e.target.files[0].webkitRelativePath || "";
        if (path) {
          document.getElementById("f-name").value = path.split("/")[0] + "/";
        }
      } else {
        document.getElementById("file-name").textContent = "No item selected";
      }
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      const name = this.files[0]?.name || "No file selected";

      const label = document.getElementById("file-name");
      if (label) label.textContent = name;

      const nameInput = document.getElementById("f-name");
      if (this.files[0] && nameInput) {
        nameInput.value = this.files[0].name;
      }
    });
  }

  document.getElementById('f-content').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.ctrlKey) submitForm();
  });

  setupPreviewLightboxEvents();
});

// ── API ───────────────────────────────────────────────────────────────────────
async function api(path, method = 'GET', body = null) {
  const opts = { method, headers: {} };

  if (body !== null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(path, opts);
  const ctype = res.headers.get('content-type') || '';

  if (ctype.includes('application/json')) {
    return res.json();
  }

  const text = await res.text();
  return {
    ok: false,
    error: text || `Request failed (${res.status})`
  };
}

async function fetchState() {
  try {
    const data = await api('/api/state');
    state = data;
    render();
  } catch (err) { console.error('fetchState:', err); }
}

// ── RENDER ────────────────────────────────────────────────────────────────────
function render() {
  renderStats();
  renderDiskMap();
  renderInodes();
  renderLog();
  updateSelBar();
}

// ── STATS ─────────────────────────────────────────────────────────────────────
function renderStats() {
  const s = state.stats || {};
  set('sr-used',    s.used    ?? '—');
  set('sr-free',    s.free    ?? '—');
  set('sr-reclaim', s.deleted_blocks ?? '—');
  set('sr-files',   s.files   ?? '—');
  set('sr-deleted', s.deleted_files ?? '—');

  const capPct  = s.capacity_pct ?? 0;
  const fragPct = s.fragmentation ?? 0;

  set('sr-capacity', capPct + '%');
  const capFill = document.getElementById('cap-fill');
  if (capFill) capFill.style.width = capPct + '%';

  set('frag-pct',   fragPct + '%');
  const ff = document.getElementById('frag-fill');
  if (ff) {
    ff.style.width = fragPct + '%';
    ff.className   = 'frag-fill' + (fragPct >= 60 ? ' high' : fragPct >= 30 ? ' med' : '');
  }
  set('frag-label',
    fragPct === 0 ? 'OPTIMAL' :
    fragPct < 30  ? 'LOW' :
    fragPct < 60  ? 'MODERATE — DEFRAG RECOMMENDED' :
                    'HIGH — DEFRAG REQUIRED'
  );
}

// ── DISK MAP ──────────────────────────────────────────────────────────────────
function renderDiskMap() {
  const grid = document.getElementById('disk-grid');
  const bm   = state.bitmap || [];

  // Build owner map: block → inode
  const owner = {};
  (state.inodes || []).forEach(nd => nd.blocks.forEach(b => owner[b] = nd));

  if (grid.children.length !== bm.length) {
    grid.innerHTML = '';
    bm.forEach((v, i) => {
      const d = document.createElement('div');
      d.className = 'blk ' + blkClass(v);
      d.dataset.i = i;
      d.addEventListener('mouseenter', () => inspectBlock(i, v, owner[i]));
      d.addEventListener('mouseleave', clearInspector);
      grid.appendChild(d);
    });
  } else {
    Array.from(grid.children).forEach((d, i) => {
      const cls = 'blk ' + blkClass(bm[i]);
      if (d.className !== cls) d.className = cls;
      d.onmouseenter = () => inspectBlock(i, bm[i], owner[i]);
    });
  }

  // Highlight selected file's blocks
  highlightSelection();
}

function blkClass(v) {
  return v === 0 ? 'free'
       : v === 1 ? 'used'
       : v === 2 ? 'ghost'
       : v === 3 ? 'corrupt'
       : 'free';
}

function highlightSelection() {
  const grid = document.getElementById('disk-grid');
  if (!grid) return;
  Array.from(grid.children).forEach(d => d.classList.remove('sel-hl'));
  if (!selectedId) return;
  const nd = (state.inodes || []).find(n => n.id === selectedId);
  if (!nd) return;
  nd.blocks.forEach(b => {
    const el = grid.children[b];
    if (el) el.classList.add('sel-hl');
  });
}

// ── BLOCK INSPECTOR ───────────────────────────────────────────────────────────
function inspectBlock(index, val, nd) {
  const box  = document.getElementById('inspector');
  const state_str = val === 0
  ? '<span class="insp-v g">FREE</span>'
  : val === 1
  ? '<span class="insp-v c">USED</span>'
  : val === 2
  ? '<span class="insp-v r">DELETED</span>'
  : val === 3
  ? '<span class="insp-v r">CORRUPTED</span>'
  : '<span class="insp-v">UNKNOWN</span>';

  let html = `
    <div class="insp-row"><span class="insp-k">Block</span><span class="insp-v a">#${index}</span></div>
    <div class="insp-row"><span class="insp-k">Byte range</span><span class="insp-v">${index*64}–${index*64+63}</span></div>
    <div class="insp-row"><span class="insp-k">State</span>${state_str}</div>`;

  if (nd) {
    html += `<hr class="insp-divider"/>
    <div class="insp-row"><span class="insp-k">File</span><span class="insp-v c">${nd.name}</span></div>
    <div class="insp-row"><span class="insp-k">Inode</span><span class="insp-v">#${nd.id}</span></div>
    <div class="insp-row"><span class="insp-k">All blocks</span><span class="insp-v">[${nd.blocks.join(',')}]</span></div>
    <div class="insp-row"><span class="insp-k">Status</span><span class="insp-v ${nd.status==='active'?'g':'r'}">${nd.status.toUpperCase()}</span></div>`;
  }
  box.innerHTML = html;
}

function clearInspector() {
  document.getElementById('inspector').innerHTML =
    '<div class="insp-empty">Hover a block to inspect</div>';
}

function searchFiles(q) {
  q = q.toLowerCase();
  document.querySelectorAll('#inode-body tr').forEach(tr => {
    tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
  });
}

// ── INODE TABLE ───────────────────────────────────────────────────────────────

let currentPath = '';

function setPath(path) {
  currentPath = path;
  selectedId = null;
  resetSacts(); // clear selections
  if (typeof stopReading === 'function') stopReading();
  q('#viewer-body').innerHTML = '<div class="viewer-empty">No file selected</div>';
  q('#viewer-name').textContent = '—';
  document.querySelectorAll('.file-row').forEach(row => row.classList.remove('active'));
  renderInodes();
}

function renderInodes() {
  const tbody = document.getElementById('inode-body');
  const allList  = (state.inodes || []).filter(
    nd => filterMode === 'all' || nd.status === filterMode
  );

  const scroll = tbody.closest('.inode-scroll')?.scrollTop || 0;
  tbody.innerHTML = '';

  const itemsToRender = [];
  const folders = new Set();
  
  allList.forEach(nd => {
      if (nd.name.startsWith(currentPath)) {
          const relativeName = nd.name.substring(currentPath.length);
          if (relativeName.includes('/')) {
              const folderName = relativeName.split('/')[0];
              folders.add(folderName);
          } else {
              itemsToRender.push(nd);
          }
      }
  });

  if (currentPath !== '') {
      const parts = currentPath.split('/').filter(p => p);
      parts.pop();
      const parentPath = parts.length ? parts.join('/') + '/' : '';
      const tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      tr.onclick = () => setPath(parentPath);
      tr.innerHTML = `<td colspan="7" style="color:var(--amber); font-weight:bold; padding-left:14px; background: rgba(255,255,255,0.03);">🔙 .. (Go Back)</td>`;
      tbody.appendChild(tr);
  }

  if (itemsToRender.length === 0 && folders.size === 0) {
      if (currentPath === '') {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td colspan="7" style="text-align:center;color:var(--t2);padding:20px 10px;font-size:10px">No files</td>`;
          tbody.appendChild(tr);
      } else {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td colspan="7" style="text-align:center;color:var(--t2);padding:20px 10px;font-size:10px">Empty Folder</td>`;
          tbody.appendChild(tr);
      }
  }

  // Folders
  Array.from(folders).sort().forEach(folder => {
      const tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      tr.onclick = () => setPath(currentPath + folder + '/');
      tr.innerHTML = `
        <td></td>
        <td style="color:var(--t2)">DIR</td>
        <td style="color:#d4a017; font-weight:600;"><span style="margin-right:6px">📁</span>${folder}</td>
        <td>-</td>
        <td style="color:var(--t2);font-size:10px">-</td>
        <td style="color:var(--t2)">-</td>
        <td><span class="badge" style="background:#332b00; color:#d4a017; border-color:#d4a017;">FOLDER</span></td>
      `;
      tbody.appendChild(tr);
  });

  // Files
  itemsToRender.sort((a,b)=>a.name.localeCompare(b.name)).forEach(nd => {
    const tr  = document.createElement('tr');
    const ext = (nd.ext || '').toLowerCase();
    const sel = nd.id === selectedId;
    tr.className = (nd.status === 'deleted' ? 'row-deleted ' : '') + (sel ? 'row-selected' : '');

    const blkShort = nd.blocks.length > 4
      ? nd.blocks.slice(0, 3).join(',') + '…'
      : nd.blocks.join(',');

    const relativeName = nd.name.substring(currentPath.length);
    let sizeDisp = Math.round(nd.size/1024) > 0 ? Math.round(nd.size/1024) + 'KB' : nd.size + 'B';

    let badgeClass = '';
    if(nd.status === 'active') badgeClass = 'badge-active';
    else if(nd.status === 'deleted') badgeClass = 'badge-deleted';
    else if(nd.status === 'corrupted') badgeClass = 'badge-corrupt';

    tr.innerHTML = `
      <td><input type="radio" class="sel-radio" name="file-sel" ${sel ? 'checked' : ''} onchange="selectFile(${nd.id})" /></td>
      <td style="color:var(--t2)">#${nd.id}</td>
      <td class="${ext ? 'ext-'+ext : ''}" style="font-weight:600">${relativeName}</td>
      <td>${sizeDisp}</td>
      <td style="color:var(--t2);font-size:10px">[${blkShort}]</td>
      <td style="color:var(--t2)">${nd.modified || nd.created}</td>
      <td><span class="badge ${badgeClass}">${nd.status.toUpperCase()}</span></td>`;

    tr.addEventListener('click', e => {
      if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON') {
          selectFile(nd.id);
      }
    });
    tbody.appendChild(tr);
  });

  const scrollWrapper = tbody.closest('.inode-scroll');
  if(scrollWrapper) {
      scrollWrapper.scrollTop = scroll;
  }
  
  // Update UI path display if you have one
  let pDisp = document.getElementById('cur-path-display');
  if(!pDisp) {
      let colLeft = document.querySelector('.col-left .panel-hdr');
      if (colLeft) {
          pDisp = document.createElement('span');
          pDisp.id = 'cur-path-display';
          pDisp.style.marginLeft = 'auto';
          pDisp.style.fontSize = '10px';
          pDisp.style.color = 'var(--cyan)';
          pDisp.style.background = 'rgba(0,255,255,0.1)';
          pDisp.style.padding = '2px 6px';
          pDisp.style.borderRadius = '3px';
          colLeft.appendChild(pDisp);
      }
  }
  if(pDisp) {
      pDisp.textContent = currentPath === '' ? '/root' : '/root/' + currentPath;
  }
}


// ── SELECTION ─────────────────────────────────────────────────────────────────
function selectFile(id) {
  selectedId = (selectedId === id) ? null : id;
  renderInodes();
  highlightSelection();
  updateSelBar();
}

function updateSelBar() {
  const nd = selectedId ? (state.inodes || []).find(n => n.id === selectedId) : null;
  set('sel-fname', nd ? nd.name : '—');

  // Enable/disable action buttons based on status
  const isActive = nd && nd.status === 'active';
  const isDeleted = nd && nd.status === 'deleted';
  const isCorrupted = nd && nd.status === 'corrupted';

  const hasSelection = !!nd;

  q('.sact-read').disabled = !isActive;
  const canOpenEditor = nd && nd.status !== 'deleted';
  q('.sact-edit').disabled = !canOpenEditor;
  q('.sact-del').disabled = !isActive;
  q('.sact-recover').disabled = !isDeleted;
  q('.sact-salvage').disabled = !isCorrupted;
  q('.sact-remove').disabled = !hasSelection;
}

// ── SELECTION ACTIONS ─────────────────────────────────────────────────────────
async function actionRead() {
  if (!selectedId) return toast('Select a file first', 'warn');
  await readFile(selectedId);
}

async function actionEdit() {
  if (!selectedId) return toast('Select a file first', 'warn');

  const nd = (state.inodes || []).find(n => n.id === selectedId);
  if (!nd || nd.status === 'deleted') return toast('File is deleted', 'err');

  const res = await api(`/api/read/${selectedId}`);
  if (res.error) return toast(res.error, 'err');

  const ext = (res.ext || '').toLowerCase();
  const extEditable = !ext || EDITABLE_TEXT_EXTS.has(ext);

  let bytes;
  let contentText = '';
  try {
    bytes = bytesFromBase64(res.content);
    contentText = decodeBytesToText(bytes);
  } catch {
    return toast('Unable to decode file for editing', 'err');
  }

  if (!extEditable && !isLikelyTextBytes(bytes)) {
    return toast('This file is binary/media. Edit works for written/text files.', 'warn');
  }

  loadIntoEditor({ ...res.inode, name: res.name, content: contentText });
}

async function actionDelete() {
  if (!selectedId) return toast('Select a file first', 'warn');
  await deleteFile(selectedId);
}

async function actionRemoveFromDisk() {
  if (!selectedId) return toast('Select a file first', 'warn');
  const nd = (state.inodes || []).find(n => n.id === selectedId);
  if (!nd) return toast('File not found', 'err');

  const ok = window.confirm(`Permanently remove "${nd.name}" from disk? This cannot be undone.`);
  if (!ok) return;

  await removeFromDisk(selectedId);
}

async function actionRecover() {
  if (!selectedId) return toast('Select a file first', 'warn');
  const nd = (state.inodes || []).find(n => n.id === selectedId);
  if (!nd || nd.status !== 'deleted') return toast('File is not deleted', 'warn');
  await recoverFile(selectedId);
}


async function actionSalvage() {
  if (!selectedId) return toast('Select a file first', 'warn');
  const nd = (state.inodes || []).find(n => n.id === selectedId);
  if (!nd || nd.status !== 'corrupted') return toast('File is not corrupted', 'warn');

  const salvaged = await salvageSingleFile(selectedId, true);
  if (salvaged) await readFile(selectedId);
}

// ── FILE OPERATIONS ───────────────────────────────────────────────────────────
async function submitForm() {
  if (writing) return;
  writing = true;

  const rawName = q('#f-name').value.trim();
  const fileInput = document.getElementById('f-file');
  const folderInput = document.getElementById('f-folder');

  if (!rawName) {
    toast('Filename or folder path is required', 'err');
    resetWriting();
    return;
  }

  let finalName = rawName;
  if (!finalName.startsWith(currentPath)) {
      finalName = currentPath + finalName; // Always append to current folder view
  }

  if (currentEditId) {
    const content = q('#f-content').value;
    const res = await api(`/api/update/${currentEditId}`, 'PUT', { name: finalName, content });
    if (!res.error) toast('File updated', 'ok');
    else toast(res.error, 'err');
    
    cancelEdit();
    resetWriting();
    await fetchState();
    return;
  }

  // Handle Directory Upload
  if (folderInput && folderInput.files.length > 0) {
      toast(`Uploading folder (${folderInput.files.length} files)...`, 'inf');
      let errs = 0;
      const baseFolderName = rawName.endsWith('/') ? rawName : rawName + '/';

      for (const file of folderInput.files) {
          const subPath = file.webkitRelativePath || file.name;
          const pieces = subPath.split('/');
          const relativeToFolder = pieces.slice(1).join('/'); // remove top folder name injected by browser
          
          const fullPath = currentPath + baseFolderName + relativeToFolder;
          
          const fd = new FormData();
          fd.append('name', fullPath);
          fd.append('file', file);

          try {
              const rsp = await fetch('/api/write', { method: 'POST', body: fd });
              if (rsp.status !== 200) errs++;
          } catch(e) { errs++; }
      }
      
      if (errs > 0) toast(`Folder uploaded with ${errs} errors`, 'warn');
      else toast('Folder successfully written to disk', 'ok');

      folderInput.value = '';
      q('#file-name').textContent = 'No item selected';
      
      cancelEdit();
      resetWriting();
      await fetchState();
      return;
  }

  // Handle Single File
  const formData = new FormData();
  formData.append('name', finalName);

  if (fileInput && fileInput.files.length > 0) {
    formData.append('file', fileInput.files[0]);
  } else {
    // Treat as empty file or plain text if user implies creating empty folder by trailing slash
    if (finalName.endsWith('/')) {
        const blob = new Blob([''], { type: 'text/plain' });
        // The backend needs a file to track inode block, we make dummy file
        formData.append('name', finalName + '.directory_marker');
        formData.append('file', blob, '.directory_marker');
    } else {
        const content = q('#f-content').value;
        const blob = new Blob([content], { type: 'text/plain' });
        formData.append('file', blob, finalName);
    }
  }

  const response = await fetch('/api/write', { method: 'POST', body: formData });
  let res;
  try { res = await response.json(); } 
  catch { res = { error: `Upload failed (${response.status})` }; }

  if (res.error) {
    toast(res.error, 'err');
    resetWriting();
    return;
  }

  const inodeId = res.inode_id || res.inode?.id;
  selectedId = inodeId || selectedId;

  if (res.integrity?.status === 'corrupted' && !finalName.endsWith('/')) {
    toast(`"${finalName}" flagged as corrupted`, 'warn');
  } else {
    toast(`"${finalName}" successfully written`, 'ok');
    if (inodeId) await readFile(inodeId);
  }

  if (fileInput) fileInput.value = '';
  q('#file-name').textContent = 'No file selected';

  cancelEdit();
  resetWriting();
  await fetchState();
}

function resetWriting() {
  writing = false;
  const btn = q('#btn-submit');
  if (btn) btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>${currentEditId ? 'SAVE CHANGES' : 'WRITE TO DISK'}`;
}


async function readFile(id) {
  const res = await api(`/api/read/${id}`);
  if (res.error) { toast(res.error, 'err'); return; }

  const nd = res.inode;
  const integrity = res.integrity || { status: nd.integrity_status || 'ok', reason: nd.integrity_reason || '' };

  set('viewer-name', nd.name);

  clearViewerPreviewMode();

  const txtExts = ['txt', 'md', 'log', 'js', 'html', 'py', 'json', 'css', 'csv', 'yaml', 'yml', 'xml', 'sh', 'bat', 'ps1', 'ts', 'jsx', 'tsx', 'cpp', 'c', 'h', 'java', 'go', 'rs', 'php', 'rb'];
  if (txtExts.includes(res.ext)) {
    try {
      const decoded = atob(res.content);
      q('#viewer-body').textContent = decoded;
    } catch {
      q('#viewer-body').textContent = '[Unable to decode file content]';
    }
  } else if (res.ext === 'png' || res.ext === 'jpg' || res.ext === 'jpeg') {
    const src = `data:image/${res.ext};base64,${res.content}`;
    q('#viewer-body').innerHTML = `<img class="preview-img" src="${src}" alt="${escapeHtmlAttr(res.name)}">`;
    bindViewerPreviewOpen('image', src, res.name);
  } else if (res.ext === 'pdf') {
    const src = `data:application/pdf;base64,${res.content}`;
    q('#viewer-body').innerHTML = `<iframe class="preview-pdf" src="${src}" title="${escapeHtmlAttr(res.name)}"></iframe>`;
    bindViewerPreviewOpen('pdf', src, res.name);
  } else if (res.ext === 'mp3' || res.ext === 'wav') {
    q('#viewer-body').innerHTML = `<audio controls style="width:100%"><source src="data:audio/${res.ext};base64,${res.content}"></audio>`;
  } else if (res.ext === 'mp4') {
    const src = `data:video/mp4;base64,${res.content}`;
    q('#viewer-body').innerHTML = `<video class="preview-video" controls><source src="${src}"></video>`;
    bindViewerPreviewOpen('video', src, res.name);
  } else {
    q('#viewer-body').innerHTML = `
      <div style="color:cyan">
        File: ${res.name}<br>
        Type: ${(res.ext || 'unknown').toUpperCase()}<br>
        Size: ${res.inode.size} bytes<br><br>
        Preview not supported
      </div>`;
  }

  q('#viewer-meta').innerHTML = `
    <span class="vm-item">size: <span>${nd.size}B</span></span>
    <span class="vm-item">blocks: <span>[${nd.blocks.join(',')}]</span></span>
    <span class="vm-item">inode: <span>#${nd.id}</span></span>
    <span class="vm-item">created: <span>${nd.created}</span></span>
    <span class="vm-item">modified: <span>${nd.modified}</span></span>
    <span class="vm-item">integrity: <span>${(integrity.status || 'ok').toUpperCase()}</span></span>
    <span class="vm-item">repairable: <span>${integrity.can_repair ? 'YES' : 'NO'}</span></span>`;

  if (integrity.reason) {
    const reason = document.createElement('div');
    reason.className = 'vm-item';
    reason.textContent = `reason: ${integrity.reason}`;
    q('#viewer-meta').appendChild(reason);
  }

  if (integrity.status === 'corrupted') {
    const repairBtn = document.createElement('button');
    repairBtn.className = 'sact sact-recover';
    repairBtn.textContent = 'REPAIR THIS FILE';
    repairBtn.onclick = async () => {
      const fixed = await repairSingleFile(id, false);
      if (fixed) await readFile(id);
    };
    q('#viewer-meta').appendChild(repairBtn);

    const salvageBtn = document.createElement('button');
    salvageBtn.className = 'sact sact-salvage';
    salvageBtn.textContent = 'SALVAGE FILE';
    salvageBtn.onclick = async () => {
      const salvaged = await salvageSingleFile(id, false);
      if (salvaged) await readFile(id);
    };
    q('#viewer-meta').appendChild(salvageBtn);

    if (!integrity.can_repair) {
      const sourceBtn = document.createElement('button');
      sourceBtn.className = 'sact sact-edit';
      sourceBtn.textContent = 'UPLOAD CLEAN SOURCE';
      sourceBtn.onclick = async () => {
        const uploaded = await uploadRepairSourceForFile(id);
        if (uploaded) {
          const fixed = await repairSingleFile(id, false, false);
          if (fixed) await readFile(id);
        }
      };
      q('#viewer-meta').appendChild(sourceBtn);
    }
  }

  const link = document.createElement('a');
  link.href = `data:application/octet-stream;base64,${res.content}`;
  link.download = res.name;
  link.textContent = nd.repaired_at ? 'Download fixed version' : 'Download';
  q('#viewer-meta').appendChild(link);

  await fetchState();
}


async function deleteFile(id) {
  const res = await api(`/api/delete/${id}`, 'DELETE');
  if (res.error) { toast(res.error, 'err'); return; }
  if (selectedId === id) selectedId = null;
  // Reset viewer if viewing this file
  const vname = document.getElementById('viewer-name')?.textContent;
  const nd = (state.inodes || []).find(n => n.id === id);
  if (nd && vname === nd.name) resetViewer();
  toast('File deleted — blocks reclaimable', 'warn');
  await fetchState();
}

async function removeFromDisk(id) {
  const nd = (state.inodes || []).find(n => n.id === id);
  const res = await api(`/api/purge/${id}`, 'DELETE');
  if (res.error) { toast(res.error, 'err'); return; }

  if (selectedId === id) selectedId = null;
  const vname = document.getElementById('viewer-name')?.textContent;
  if (nd && vname === nd.name) resetViewer();

  toast(`"${nd?.name || 'File'}" removed from disk`, 'ok');
  await fetchState();
}

async function recoverFile(id) {
  const res = await api('/api/recover', 'POST', { inode_id: id });
  if (res.recovered?.length) toast(`"${res.recovered[0]}" recovered`, 'ok');
  else if (res.skipped?.length) toast(`Cannot recover — blocks overwritten`, 'err');
  else toast('Nothing to recover', 'info');
  await fetchState();
}

async function bulkRecover() {
  const res = await api('/api/recover', 'POST', {});
  const n = res.recovered?.length || 0;
  if (n) toast(`${n} file(s) recovered`, 'ok');
  else toast('Nothing to recover', 'info');
  await fetchState();
}

async function defragDisk() {
  setMount('DEFRAGGING…');

  const blocks = document.querySelectorAll('.blk');

  blocks.forEach((b, i) => {
    setTimeout(() => {
      b.style.transform = "scale(1.3)";
      setTimeout(() => b.style.transform = "", 150);
    }, i * 15);
  });

  await api('/api/defrag', 'POST');

  setMount('MOUNTED');
  toast('Disk defragmented — blocks packed', 'ok');
  await fetchState();
}

function openModal()  { q('#modal-bg').classList.add('open'); }
function closeModal() { q('#modal-bg').classList.remove('open'); }
async function doFormat() {
  closeModal();
  await api('/api/format', 'POST');
  selectedId    = null;
  currentEditId = null;
  cancelEdit();
  resetViewer();
  toast('Disk formatted — all data erased', 'err');
  await fetchState();
}

// ── EDIT MODE ─────────────────────────────────────────────────────────────────
function loadIntoEditor(nd) {
  currentEditId = nd.id;
  q('#f-name').value    = nd.name;
  q('#f-content').value = nd.content || '';
  updateSizePreview();

  set('form-title', 'EDIT FILE');
  q('#edit-badge').classList.remove('hidden');
  q('#btn-cancel-edit').classList.remove('hidden');
  q('#btn-submit').innerHTML = `<svg viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>SAVE CHANGES`;

  q('#f-name').focus();
  toast(`Editing "${nd.name}"`, 'info');
}

function cancelEdit() {
  currentEditId = null;
  q('#f-name').value    = '';
  q('#f-content').value = '';
  updateSizePreview();

  set('form-title', 'WRITE FILE');
  q('#edit-badge').classList.add('hidden');
  q('#btn-cancel-edit').classList.add('hidden');
  q('#btn-submit').innerHTML = `<svg viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>WRITE TO DISK`;
}

// ── SIZE PREVIEW ──────────────────────────────────────────────────────────────
function updateSizePreview() {
  const content = q('#f-content').value;
  const bytes   = new TextEncoder().encode(content).length;
  const blks    = Math.max(1, Math.ceil(bytes / 64));

  set('byte-count', bytes);
  set('blk-count',  blks);

  // If editing: show reallocation delta
  const delta = document.getElementById('blk-delta');
  if (currentEditId && delta) {
    const nd = (state.inodes || []).find(n => n.id === currentEditId);
    if (nd) {
      const diff = blks - nd.blocks.length;
      delta.textContent = diff === 0 ? 'no realloc'
        : diff > 0 ? `+${diff} block(s)` : `${diff} block(s)`;
      delta.style.color = diff > 0 ? 'var(--red)' : diff < 0 ? 'var(--green)' : 'var(--t2)';
    }
  } else if (delta) {
    delta.textContent = '';
  }
}

// ── LOG ───────────────────────────────────────────────────────────────────────
function renderLog() {
  const entries = state.log || [];
  if (entries.length === prevLogLen) return;
  prevLogLen = entries.length;
  const body = q('#log-body');
  body.innerHTML = entries.map(e => `
    <div class="log-row ${e.level}">
      <span class="log-t">[${e.time}]</span>
      <span class="log-m">${e.msg}</span>
    </div>`).join('');
}

// ── FILTERS ───────────────────────────────────────────────────────────────────
function setFilter(mode, btn) {
  filterMode = mode;
  document.querySelectorAll('.ftab').forEach(t => t.classList.remove('on'));
  btn.classList.add('on');
  renderInodes();
}

// ── HELPERS ───────────────────────────────────────────────────────────────────
function q(sel) { return document.querySelector(sel); }
function set(id, val) {
  const el = document.getElementById(id);
  if (el && String(el.textContent) !== String(val)) el.textContent = val;
}
function setMount(txt) {
  const el = document.getElementById('mount-label');
  if (el) el.textContent = txt;
}
function resetViewer() {
  set('viewer-name', '—');
  const vb = q('#viewer-body');
  if (vb) vb.innerHTML = '<div class="viewer-empty">Select a file and press READ to view its contents</div>';
  const vm = q('#viewer-meta');
  if (vm) vm.innerHTML = '';
}

let toastTimer;
function toast(msg, type = 'info') {
  const el = q('#toast');
  el.textContent = msg;
  el.className   = `toast ${type} show`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

async function simulateCrash() {
    // Start shake
    document.body.classList.add("crash-mode");

    // Add red flash overlay
    const overlay = document.createElement("div");
    overlay.className = "crash-overlay";
    document.body.appendChild(overlay);

    // Call backend
    const data = await api("/api/crash", "POST", {});

    // Stop shake after short time
    setTimeout(() => {
        document.body.classList.remove("crash-mode");
        overlay.remove();
    }, 700);

    alert(data.msg);
    await fetchState();
}
async function getSuggestions() {
    const data = await api("/api/suggest");

    let box = document.getElementById("suggestions");
    box.innerHTML = "";

    data.suggestions.forEach(s => {
        let div = document.createElement("div");
        div.className = "suggestion-card";
        div.innerText = "💡 " + s;
        box.appendChild(div);
    });
}



async function uploadRepairSourceForFile(id) {
  return new Promise((resolve) => {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = '*/*';
    picker.style.display = 'none';

    document.body.appendChild(picker);

    picker.onchange = async () => {
      const file = picker.files && picker.files[0];
      if (!file) {
        picker.remove();
        return resolve(false);
      }

      const formData = new FormData();
      formData.append('file', file);

      try {
        const response = await fetch(`/api/repair-source/${id}`, {
          method: 'POST',
          body: formData
        });

        const data = await response.json();
        if (data.error) {
          toast(data.error, 'err');
          picker.remove();
          return resolve(false);
        }

        toast('Verified clean source uploaded', 'ok');
        picker.remove();
        resolve(true);
      } catch (err) {
        toast('Failed to upload repair source', 'err');
        picker.remove();
        resolve(false);
      }
    };

    picker.click();
  });
}


async function salvageSingleFile(id, refreshViewer = true) {
  const res = await api(`/api/salvage/${id}`, 'POST', {});
  if (res.error) {
    toast(res.error, 'err');
    return false;
  }

  if (res.outcome === 'partially_recovered') {
    toast('Salvage completed (partially recovered)', 'ok');
    await fetchState();
    if (refreshViewer) await readFile(id);
    return true;
  }

  toast('Salvage failed: file is unrecoverable without clean source', 'err');
  await fetchState();
  return false;
}

async function repairSingleFile(id, refreshViewer = true, allowSourceUpload = true) {
  const res = await api('/api/repair', 'POST', { inode_id: id });
  if (res.error) {
    toast(res.error, 'err');
    return false;
  }

  if (res.repaired?.length) {
    toast(`Repaired: ${res.repaired.join(', ')}`, 'ok');
    await fetchState();
    if (refreshViewer) await readFile(id);
    return true;
  }

  const detail = (res.details || []).find(d => d.inode_id === id);
  const reason = detail?.reason || 'Repair failed';

  if (allowSourceUpload && reason.toLowerCase().includes('upload a clean source file')) {
    const proceed = window.confirm('No verified clean source exists for this file. Upload a clean source now?');
    if (proceed) {
      const uploaded = await uploadRepairSourceForFile(id);
      if (uploaded) {
        return repairSingleFile(id, refreshViewer, false);
      }
    }
  }

  toast(`Repair failed: ${reason}. Use UPLOAD CLEAN SOURCE in viewer.`, 'err');
  await fetchState();
  return false;
}

async function repairDisk() {
  const res = await api('/api/repair', 'POST', {});

  if (res.error) {
    toast(res.error, 'err');
    return;
  }

  if (res.repaired?.length) {
    toast('Repaired: ' + res.repaired.join(', '), 'ok');
  } else if (res.failed?.length) {
    toast('Repair failed for: ' + res.failed.join(', '), 'err');
  } else {
    toast('No corrupted files to repair', 'info');
  }

  await fetchState();
}


document.addEventListener('DOMContentLoaded', () => {
  const mainFsBtn = document.getElementById('main-fullscreen-btn');
  const mainCloseBtn = document.getElementById('main-close-btn');

  if (mainFsBtn) {
    mainFsBtn.addEventListener('click', () => {
      const viewerPanel = document.querySelector('.viewer-panel');
      if (!viewerPanel) return;
      if (!document.fullscreenElement) {
        if (viewerPanel.requestFullscreen) {
          viewerPanel.requestFullscreen().catch(err => {
            console.error("Error attempting to enable fullscreen:", err);
          });
        }
      } else {
        if (document.exitFullscreen) {
          document.exitFullscreen();
        }
      }
    });

    // Make the fullscreen button toggle text
    document.addEventListener('fullscreenchange', () => {
      if (document.fullscreenElement === document.querySelector('.viewer-panel')) {
        mainFsBtn.textContent = 'EXIT FULLSCREEN';
      } else {
        mainFsBtn.textContent = 'FULLSCREEN';
      }
    });
  }

  if (mainCloseBtn) {
    mainCloseBtn.addEventListener('click', () => {
      const viewerBody = document.getElementById('viewer-body');
      const viewerName = document.getElementById('viewer-name');
      const linesBody = document.getElementById('viewer-lines');
      
      if (viewerBody) viewerBody.innerHTML = '<div class="viewer-empty">No file selected</div>';
      if (viewerName) viewerName.textContent = '—';
      if (linesBody) linesBody.innerHTML = '';
      
      // also clear syntax cache if any
      document.querySelectorAll('.file-row').forEach(row => row.classList.remove('active'));
    });
  }
});
