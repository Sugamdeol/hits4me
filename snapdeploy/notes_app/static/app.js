/* ============================================================
   Notesly — notes app (vanilla JS, no dependencies)
   ============================================================ */

(function () {
  "use strict";

  var LS_KEY = "notesly.notes.v1";
  var API = "/api/notes";

  var state = {
    notes: [],
    currentId: null,
    query: "",
    dirty: false,
    saveTimer: null,
    online: true,
    saving: false
  };

  var els = {};

  // ----------------------------------------------------------
  // Tiny helpers
  // ----------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function timeAgo(iso) {
    if (!iso) return "";
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var diff = Math.max(0, Date.now() - then);
    var min = 60000, hour = 3600000, day = 86400000;
    if (diff < min) return "just now";
    if (diff < hour) return Math.floor(diff / min) + "m ago";
    if (diff < day) return Math.floor(diff / hour) + "h ago";
    if (diff < 7 * day) return Math.floor(diff / day) + "d ago";
    return new Date(iso).toLocaleDateString();
  }

  function wordCount(text) {
    var t = String(text || "").trim();
    return t ? t.split(/\s+/).length : 0;
  }

  function debouncedSave() {
    var self = this;
    if (!state.currentId) return;
    var note = findNote(state.currentId);
    if (!note) return;
    state.dirty = true;
    setSaveState("Saving…");
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(function () {
      persistLocal();
      saveNote(note);
    }, 600);
  }

  // ----------------------------------------------------------
  // Note store helpers
  // ----------------------------------------------------------

  function findNote(id) {
    for (var i = 0; i < state.notes.length; i++) {
      if (state.notes[i].id === id) return state.notes[i];
    }
    return null;
  }

  function visibleNotes() {
    var q = state.query.trim().toLowerCase();
    var list = state.notes.slice();
    list.sort(function (a, b) {
      if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
    if (!q) return list;
    return list.filter(function (n) {
      var hay = (n.title + " " + n.content + " " + (n.tags || []).join(" ")).toLowerCase();
      return hay.indexOf(q) !== -1;
    });
  }

  // ----------------------------------------------------------
  // Server API
  // ----------------------------------------------------------

  function fetchJSON(url, opts) {
    opts = opts || {};
    return fetch(url, opts).then(function (res) {
      if (!res.ok) {
        var err = new Error("HTTP " + res.status);
        err.status = res.status;
        return res.json().catch(function () { return {}; }).then(function (body) {
          err.body = body;
          throw err;
        });
      }
      var ct = res.headers.get("content-type") || "";
      if (ct.indexOf("json") !== -1) return res.json();
      return null;
    });
  }

  function loadFromServer() {
    return fetchJSON(API).then(function (data) {
      state.notes = data && Array.isArray(data.notes) ? data.notes : [];
      state.online = true;
      setSyncState(true);
      return state.notes;
    }).catch(function () {
      // Offline: fall back to the browser cache so the app still works.
      state.online = false;
      setSyncState(false);
      try {
        var cached = localStorage.getItem(LS_KEY);
        state.notes = cached ? JSON.parse(cached) : [];
      } catch (e) {
        state.notes = [];
      }
      return state.notes;
    });
  }

  function saveNote(note) {
    var url = API + "/" + encodeURIComponent(note.id);
    var method = "PUT";
    var isNew = note._new === true;
    if (isNew) { url = API; method = "POST"; }

    return fetchJSON(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: note.title,
        content: note.content,
        tags: note.tags,
        pinned: note.pinned
      })
    }).then(function (saved) {
      if (isNew && saved && saved.id) {
        note.id = saved.id;
        note._new = false;
      }
      note.updated_at = saved ? saved.updated_at : new Date().toISOString();
      state.saving = false;
      setSaveState("Saved");
      renderList();
      updateCounts();
      persistLocal();
    }).catch(function () {
      state.online = false;
      setSyncState(false);
      setSaveState("Saved locally");
    });
  }

  function deleteOnServer(id) {
    return fetchJSON(API + "/" + encodeURIComponent(id), { method: "DELETE" })
      .catch(function () {
        state.online = false;
        setSyncState(false);
      });
  }

  function persistLocal() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(state.notes)); } catch (e) { /* ignore */ }
  }

  // ----------------------------------------------------------
  // Rendering
  // ----------------------------------------------------------

  function renderList() {
    var list = visibleNotes();
    els.notesList.innerHTML = "";

    if (!list.length) {
      var empty = document.createElement("div");
      empty.className = "notes-list-empty";
      empty.textContent = state.query ? "No notes match your search." : "No notes yet. Create your first one!";
      els.notesList.appendChild(empty);
    }

    list.forEach(function (n) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "note-item" + (n.id === state.currentId ? " active" : "");
      item.setAttribute("role", "listitem");

      var head = document.createElement("div");
      head.className = "note-item-head";
      var title = document.createElement("span");
      title.className = "note-item-title";
      title.textContent = n.title || "Untitled note";
      head.appendChild(title);
      if (n.pinned) {
        var pin = document.createElement("span");
        pin.className = "note-item-pin";
        pin.textContent = "📌";
        pin.title = "Pinned";
        head.appendChild(pin);
      }
      item.appendChild(head);

      var snippet = document.createElement("div");
      snippet.className = "note-item-snippet";
      snippet.textContent = (n.content || "").replace(/\s+/g, " ").slice(0, 90);
      item.appendChild(snippet);

      var meta = document.createElement("div");
      meta.className = "note-item-meta";
      var when = document.createElement("span");
      when.textContent = timeAgo(n.updated_at);
      meta.appendChild(when);
      (n.tags || []).slice(0, 2).forEach(function (tag) {
        var t = document.createElement("span");
        t.className = "note-item-tag";
        t.textContent = "#" + tag;
        meta.appendChild(t);
      });
      item.appendChild(meta);

      item.addEventListener("click", function () { selectNote(n.id); });
      els.notesList.appendChild(item);
    });
  }

  function selectNote(id) {
    var note = findNote(id);
    if (!note) return;
    state.currentId = id;
    state.dirty = false;
    clearTimeout(state.saveTimer);

    els.editorEmpty.classList.add("hidden");
    els.editor.classList.remove("hidden");
    els.noteTitle.value = note.title || "";
    els.noteContent.value = note.content || "";
    els.noteTags.value = (note.tags || []).join(", ");
    els.pinBtn.classList.toggle("pinned", !!note.pinned);
    els.pinBtn.title = note.pinned ? "Unpin note" : "Pin note";
    updateMeta();
    renderList();
  }

  function updateMeta() {
    var note = findNote(state.currentId);
    if (!note) { return; }
    els.wordCount.textContent = wordCount(note.content) + " words";
    var d = note.updated_at ? new Date(note.updated_at) : null;
    els.editorDate.textContent = d && !isNaN(d.getTime())
      ? "Edited " + d.toLocaleString() : "";
  }

  function setSaveState(text) {
    els.saveState.textContent = text;
  }

  function setSyncState(online) {
    els.syncState.textContent = online ? "Synced" : "Offline mode";
    els.syncState.classList.toggle("online", online);
    els.syncState.classList.toggle("offline", !online);
  }

  function updateCounts() {
    var n = state.notes.length;
    els.notesCount.textContent = n + (n === 1 ? " note" : " notes");
  }

  function readEditorInto(note) {
    note.title = els.noteTitle.value.trim() || "Untitled note";
    note.content = els.noteContent.value;
    note.tags = els.noteTags.value.split(",").map(function (t) { return t.trim(); })
      .filter(Boolean);
  }

  // ----------------------------------------------------------
  // Actions
  // ----------------------------------------------------------

  function newNote() {
    var now = new Date().toISOString();
    var note = {
      id: "local-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7),
      _new: true,
      title: "",
      content: "",
      tags: [],
      pinned: false,
      created_at: now,
      updated_at: now
    };
    state.notes.unshift(note);
    persistLocal();
    selectNote(note.id);
    els.noteTitle.focus();
  }

  function togglePin() {
    var note = findNote(state.currentId);
    if (!note) return;
    note.pinned = !note.pinned;
    els.pinBtn.classList.toggle("pinned", note.pinned);
    els.pinBtn.title = note.pinned ? "Unpin note" : "Pin note";
    persistLocal();
    saveNote(note);
    renderList();
  }

  function deleteNote() {
    var note = findNote(state.currentId);
    if (!note) return;
    if (!window.confirm('Delete "' + (note.title || "Untitled note") + '"? This cannot be undone.')) return;
    state.notes = state.notes.filter(function (n) { return n.id !== note.id; });
    persistLocal();
    deleteOnServer(note.id);
    state.currentId = null;
    clearTimeout(state.saveTimer);
    els.editor.classList.add("hidden");
    els.editorEmpty.classList.remove("hidden");
    renderList();
    updateCounts();
  }

  // ----------------------------------------------------------
  // Stats (about section)
  // ----------------------------------------------------------

  function loadStats() {
    fetchJSON("/api/stats").then(function (s) {
      if (!s) return;
      els.statNotes.textContent = s.notes != null ? s.notes : "–";
      var up = s.uptime_seconds || 0;
      var h = Math.floor(up / 3600);
      els.statUptime.textContent = up > 0 ? h + "h" : "–";
    }).catch(function () { /* non-fatal */ });
  }

  // ----------------------------------------------------------
  // Wire up
  // ----------------------------------------------------------

  function init() {
    els.notesList = $("notes-list");
    els.editorEmpty = $("editor-empty");
    els.editor = $("editor");
    els.noteTitle = $("note-title");
    els.noteContent = $("note-content");
    els.noteTags = $("note-tags");
    els.pinBtn = $("pin-btn");
    els.deleteBtn = $("delete-btn");
    els.saveState = $("save-state");
    els.wordCount = $("word-count");
    els.editorDate = $("editor-date");
    els.notesCount = $("notes-count");
    els.syncState = $("sync-state");
    els.searchInput = $("search-input");
    els.newNoteBtn = $("new-note-btn");
    els.statNotes = $("stat-notes");
    els.statUptime = $("stat-uptime");

    els.newNoteBtn.addEventListener("click", newNote);
    els.pinBtn.addEventListener("click", togglePin);
    els.deleteBtn.addEventListener("click", deleteNote);

    els.searchInput.addEventListener("input", function () {
      state.query = els.searchInput.value;
      renderList();
    });

    els.noteTitle.addEventListener("input", function () {
      var n = findNote(state.currentId);
      if (n) { n.title = els.noteTitle.value; renderList(); debouncedSave(); }
    });

    els.noteContent.addEventListener("input", function () {
      var n = findNote(state.currentId);
      if (n) { n.content = els.noteContent.value; updateMeta(); debouncedSave(); }
    });

    els.noteTags.addEventListener("input", function () {
      var n = findNote(state.currentId);
      if (n) { n.tags = els.noteTags.value.split(",").map(function (t) { return t.trim(); }).filter(Boolean); debouncedSave(); }
    });

    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        newNote();
      }
    });

    document.getElementById("year").textContent = new Date().getFullYear();

    loadFromServer().then(function () {
      renderList();
      updateCounts();
      if (state.notes.length && !state.currentId) {
        selectNote(visibleNotes()[0].id);
      } else {
        els.editorEmpty.classList.remove("hidden");
      }
    });
    loadStats();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
