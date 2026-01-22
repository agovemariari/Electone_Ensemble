const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
const themeToggle = document.getElementById("theme-toggle");

const THEME_KEY = "ee_theme";

const applyTheme = (mode) => {
  const resolved = mode === "auto" ? getSystemTheme() : mode;
  if (resolved === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  if (themeToggle) {
    const label = mode === "dark" ? "Theme: Dark" : mode === "light" ? "Theme: Light" : "Theme: Auto";
    themeToggle.textContent = label;
  }
};

const getSystemTheme = () =>
  window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";

const savedTheme = localStorage.getItem(THEME_KEY);
const initialTheme = savedTheme || "auto";
applyTheme(initialTheme);

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const current = localStorage.getItem(THEME_KEY) || "auto";
    const next = current === "auto" ? "light" : current === "light" ? "dark" : "auto";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });
}

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const current = localStorage.getItem(THEME_KEY) || "auto";
    if (current === "auto") {
      applyTheme("auto");
    }
  });
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    panels.forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    const id = `tab-${tab.dataset.tab}`;
    const target = document.getElementById(id);
    if (target) {
      target.classList.add("active");
    }
  });
});

const staffBoard = document.getElementById("staff-board");
const linkLayer = document.getElementById("link-layer");
const timeNumInput = document.getElementById("time-num");
const timeDenInput = document.getElementById("time-den");
const barCountInput = document.getElementById("bar-count");
const trackCountInput = document.getElementById("track-count");
const rebuildBtn = document.getElementById("rebuild-score");
const exportBtn = document.getElementById("export-score");
const linkStatus = document.getElementById("link-status");
const durationButtons = document.querySelectorAll(".duration-btn");
const toolButtons = document.querySelectorAll(".tool-btn");

const state = {
  bars: 2,
  beats: 4,
  denom: 4,
  trackCount: 6,
  stepWidth: 28,
  duration: "1/1",
  tool: "pen",
  linking: null,
  drag: null,
  linkId: 1,
  notes: new Map(),
  links: [],
};

const history = [];

const pushHistory = (action) => {
  history.push(action);
};

const toInt = (value, fallback) => {
  const v = parseInt(value, 10);
  return Number.isFinite(v) && v > 0 ? v : fallback;
};

const getStepsPerBeat = () => {
  const denom = state.denom;
  return Math.max(1, Math.round(16 / denom));
};

const getStepsPerBar = () => state.beats * getStepsPerBeat();

const getTotalSteps = () => state.bars * getStepsPerBar();

const updateStatus = (text) => {
  if (linkStatus) {
    linkStatus.textContent = text;
  }
};

const clearLinkTargets = () => {
  document.querySelectorAll(".note.link-target").forEach((n) => n.classList.remove("link-target"));
};

const getNoteCenter = (note) => {
  const rect = note.getBoundingClientRect();
  const boardRect = staffBoard.getBoundingClientRect();
  return {
    x: rect.left - boardRect.left + rect.width / 2 + staffBoard.scrollLeft,
    y: rect.top - boardRect.top + rect.height / 2 + staffBoard.scrollTop,
  };
};

const drawLinks = () => {
  if (!linkLayer) {
    return;
  }
  document.querySelectorAll(".note.linked").forEach((n) => n.classList.remove("linked"));
  const width = staffBoard.scrollWidth;
  const height = staffBoard.scrollHeight;
  linkLayer.setAttribute("width", width);
  linkLayer.setAttribute("height", height);
  linkLayer.innerHTML = "";
  state.links.forEach((link) => {
    const source = state.notes.get(link.sourceId);
    const target = state.notes.get(link.targetId);
    if (!source || !target) {
      return;
    }
    source.el.classList.add("linked");
    target.el.classList.add("linked");
    const a = getNoteCenter(source.el);
    const b = getNoteCenter(target.el);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const midX = (a.x + b.x) / 2;
    const d = `M ${a.x} ${a.y} C ${midX} ${a.y - 20}, ${midX} ${b.y + 20}, ${b.x} ${b.y}`;
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "rgba(23, 125, 107, 0.8)");
    path.setAttribute("stroke-width", "2.2");
    linkLayer.appendChild(path);
  });
};

const startLinking = (note) => {
  if (!note.classList.contains("cue")) {
    return;
  }
  state.linking = { source: note };
  note.classList.add("link-source");
  document.querySelectorAll(".staff:not(.cue-track) .note").forEach((n) => n.classList.add("link-target"));
  updateStatus("リンクモード: 対象の音符へドラッグしてください");
};

const finishLinking = (target) => {
  if (!state.linking || !target) {
    return;
  }
  if (target.classList.contains("cue")) {
    return;
  }
  const linkId = `L${state.linkId++}`;
  const sourceId = state.linking.source.dataset.noteId;
  const targetId = target.dataset.noteId;
  if (sourceId && targetId) {
    state.links.push({ id: linkId, sourceId, targetId });
    pushHistory({ type: "addLink", id: linkId });
  }
  updateStatus("リンク完了");
  state.linking.source.classList.remove("link-source");
  clearLinkTargets();
  state.linking = null;
  drawLinks();
};

const cancelLinking = () => {
  if (!state.linking) {
    return;
  }
  state.linking.source.classList.remove("link-source");
  clearLinkTargets();
  state.linking = null;
  updateStatus("リンク待機中");
};

const setActiveDuration = (value) => {
  state.duration = value;
  durationButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.duration === value));
};

durationButtons.forEach((btn) => {
  btn.addEventListener("click", () => setActiveDuration(btn.dataset.duration));
});

const setActiveTool = (value) => {
  state.tool = value;
  toolButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.tool === value));
};

toolButtons.forEach((btn) => {
  btn.addEventListener("click", () => setActiveTool(btn.dataset.tool));
});

const createNoteElement = (staff, step, pitch, override = {}) => {
  const note = document.createElement("div");
  const duration = override.duration || state.duration;
  const durationClass = duration === "1/1"
    ? "whole"
    : duration === "1/2"
      ? "half"
      : duration === "1/4"
        ? "quarter"
        : duration === "1/8"
          ? "eighth"
          : "sixteenth";
  note.className = `note ${durationClass}`;
  const ch = override.ch || parseInt(staff.dataset.ch, 10);
  if (ch <= 3) {
    note.classList.add("cue");
  }
  const noteId = override.id || `N${Date.now()}${Math.floor(Math.random() * 1000)}`;
  note.dataset.noteId = noteId;
  note.dataset.step = step;
  note.dataset.pitch = pitch;
  note.dataset.ch = ch;
  note.dataset.duration = duration;
  const head = document.createElement("div");
  head.className = "note-head";
  if (durationClass === "whole" || durationClass === "half") {
    note.classList.add("open");
  }
  const stem = document.createElement("div");
  stem.className = "note-stem";
  const flag = document.createElement("div");
  flag.className = "note-flag";
  const flag2 = document.createElement("div");
  flag2.className = "note-flag second";
  note.appendChild(head);
  note.appendChild(stem);
  note.appendChild(flag);
  note.appendChild(flag2);
  positionNoteElement(staff, note);
  staff.appendChild(note);
  state.notes.set(noteId, { el: note });
  attachNoteHandlers(note);
  if (!override.skipHistory) {
    pushHistory({
      type: "addNote",
      id: noteId,
      data: getNoteData(note),
    });
  }
};

const positionNoteElement = (staff, note) => {
  const step = parseInt(note.dataset.step, 10);
  const pitch = parseInt(note.dataset.pitch, 10);
  const stepWidth = state.stepWidth;
  const left = step * stepWidth + stepWidth / 2 - 8;
  const lineSpacing = 12;
  const topBase = 22;
  const pitchStep = 6;
  const y = topBase + pitch * pitchStep - 6;
  note.style.left = `${left}px`;
  note.style.top = `${y}px`;
};

const snapStep = (x) => {
  const totalSteps = getTotalSteps();
  const step = Math.round(x / state.stepWidth);
  return Math.max(0, Math.min(totalSteps - 1, step));
};

const snapPitch = (y) => {
  const pitchStep = 6;
  const topBase = 24;
  const idx = Math.round((y - topBase) / pitchStep);
  return Math.max(0, Math.min(8, idx));
};

const attachNoteHandlers = (note) => {
  let holdTimer = null;
  let startPos = null;
  let startData = null;
  note.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    if (state.tool === "erase") {
      removeNote(note, true);
      return;
    }
    note.setPointerCapture(e.pointerId);
    startPos = { x: e.clientX, y: e.clientY };
    startData = {
      step: parseInt(note.dataset.step, 10),
      pitch: parseInt(note.dataset.pitch, 10),
    };
    holdTimer = window.setTimeout(() => {
      holdTimer = null;
      startLinking(note);
    }, 420);
    state.drag = { note, staff: note.parentElement };
  });

  note.addEventListener("pointermove", (e) => {
    if (!state.drag || state.linking) {
      return;
    }
    const dx = Math.abs(e.clientX - startPos.x);
    const dy = Math.abs(e.clientY - startPos.y);
    if (holdTimer && (dx > 6 || dy > 6)) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
    const staff = state.drag.staff;
    const rect = staff.getBoundingClientRect();
    const step = snapStep(e.clientX - rect.left);
    const pitch = snapPitch(e.clientY - rect.top);
    note.dataset.step = step;
    note.dataset.pitch = pitch;
    positionNoteElement(staff, note);
    drawLinks();
  });

  note.addEventListener("pointerup", (e) => {
    if (holdTimer) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
    if (state.linking) {
      const target = document.elementFromPoint(e.clientX, e.clientY);
      if (target && target.classList.contains("note")) {
        finishLinking(target);
      } else {
        cancelLinking();
      }
    }
    note.releasePointerCapture(e.pointerId);
    if (startData) {
      const endStep = parseInt(note.dataset.step, 10);
      const endPitch = parseInt(note.dataset.pitch, 10);
      if (endStep !== startData.step || endPitch !== startData.pitch) {
        pushHistory({
          type: "moveNote",
          id: note.dataset.noteId,
          from: startData,
          to: { step: endStep, pitch: endPitch },
        });
      }
    }
    state.drag = null;
  });
};

const attachStaffHandlers = (staff) => {
  staff.addEventListener("click", (e) => {
    if (e.target.classList.contains("note") || state.linking || state.tool !== "pen") {
      return;
    }
    const rect = staff.getBoundingClientRect();
    const step = snapStep(e.clientX - rect.left);
    const pitch = snapPitch(e.clientY - rect.top);
    createNoteElement(staff, step, pitch);
  });
};

const buildStaffBoard = () => {
  if (!staffBoard) {
    return;
  }
  staffBoard.innerHTML = "";
  if (linkLayer) {
    staffBoard.appendChild(linkLayer);
  }
  const totalSteps = getTotalSteps();
  const barSteps = getStepsPerBar();
  for (let ch = 1; ch <= state.trackCount; ch += 1) {
    const row = document.createElement("div");
    row.className = "staff-row";
    const label = document.createElement("div");
    label.className = "staff-label";
    const clef = document.createElement("span");
    clef.className = "clef";
    clef.textContent = "𝄞";
    const labelText = document.createElement("span");
    labelText.textContent = ch <= 3 ? `CH${ch} (Cue)` : `CH${ch}`;
    label.appendChild(clef);
    label.appendChild(labelText);
    const staff = document.createElement("div");
    staff.className = "staff";
    if (ch <= 3) {
      staff.classList.add("cue-track");
    }
    staff.dataset.ch = ch.toString();
    staff.style.setProperty("--step-width", state.stepWidth);
    staff.style.setProperty("--bar-steps", barSteps);
    staff.style.width = `${totalSteps * state.stepWidth}px`;
    attachStaffHandlers(staff);
    row.appendChild(label);
    row.appendChild(staff);
    staffBoard.appendChild(row);
  }
  updateStatus("リンク待機中");
  drawLinks();
};

const getNoteData = (note) => ({
  id: note.dataset.noteId,
  ch: parseInt(note.dataset.ch, 10),
  step: parseInt(note.dataset.step, 10),
  pitch: parseInt(note.dataset.pitch, 10),
  duration: note.dataset.duration,
});

const removeLinkById = (id, record = true) => {
  const idx = state.links.findIndex((link) => link.id === id);
  if (idx >= 0) {
    const [removed] = state.links.splice(idx, 1);
    if (record) {
      pushHistory({ type: "removeLink", link: removed });
    }
  }
};

const removeNote = (note, record = true) => {
  const noteId = note.dataset.noteId;
  const removedLinks = state.links.filter((link) => link.sourceId === noteId || link.targetId === noteId);
  state.links = state.links.filter((link) => link.sourceId !== noteId && link.targetId !== noteId);
  note.remove();
  state.notes.delete(noteId);
  if (record) {
    pushHistory({
      type: "deleteNote",
      data: getNoteData(note),
      links: removedLinks,
    });
  }
  drawLinks();
};

const refreshStateFromInputs = () => {
  state.bars = toInt(barCountInput?.value, 2);
  state.beats = toInt(timeNumInput?.value, 4);
  state.denom = toInt(timeDenInput?.value, 4);
  state.trackCount = toInt(trackCountInput?.value, 6);
  buildStaffBoard();
};

if (rebuildBtn) {
  rebuildBtn.addEventListener("click", () => {
    cancelLinking();
    refreshStateFromInputs();
  });
}

if (staffBoard) {
  staffBoard.addEventListener("scroll", () => {
    drawLinks();
  });
}

window.addEventListener("resize", () => {
  drawLinks();
});

const exportScore = () => {
  const timeSig = { numerator: state.beats, denominator: state.denom };
  const tracks = [];
  for (let ch = 1; ch <= state.trackCount; ch += 1) {
    const notes = [];
    document.querySelectorAll(`.staff[data-ch="${ch}"] .note`).forEach((note) => {
      notes.push({
        id: note.dataset.noteId,
        step: parseInt(note.dataset.step, 10),
        pitch: parseInt(note.dataset.pitch, 10),
        duration: note.dataset.duration,
      });
    });
    tracks.push({ ch, clef: "treble", notes });
  }
  const payload = {
    timeSig,
    bars: state.bars,
    tracks,
    links: state.links,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "score.json";
  a.click();
  URL.revokeObjectURL(url);
};

if (exportBtn) {
  exportBtn.addEventListener("click", exportScore);
}

const undo = () => {
  const action = history.pop();
  if (!action) {
    return;
  }
  if (action.type === "addNote") {
    const note = state.notes.get(action.id)?.el;
    if (note) {
      removeNote(note, false);
    }
  } else if (action.type === "moveNote") {
    const note = state.notes.get(action.id)?.el;
    if (note) {
      note.dataset.step = action.from.step;
      note.dataset.pitch = action.from.pitch;
      positionNoteElement(note.parentElement, note);
      drawLinks();
    }
  } else if (action.type === "deleteNote") {
    const staff = document.querySelector(`.staff[data-ch="${action.data.ch}"]`);
    if (staff) {
      createNoteElement(staff, action.data.step, action.data.pitch, {
        id: action.data.id,
        duration: action.data.duration,
        ch: action.data.ch,
        skipHistory: true,
      });
      if (action.links && action.links.length) {
        state.links.push(...action.links);
        drawLinks();
      }
    }
  } else if (action.type === "addLink") {
    removeLinkById(action.id, false);
    drawLinks();
  } else if (action.type === "removeLink") {
    state.links.push(action.link);
    drawLinks();
  }
};

window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    undo();
  }
});

document.addEventListener("click", (e) => {
  if (state.linking && !e.target.classList.contains("note")) {
    cancelLinking();
  }
});

refreshStateFromInputs();
