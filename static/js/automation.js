/* ==========================================
   Smart Irrigation — Automation UI
   ========================================== */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
  const socket = window.socket;
  const $ = (s) => document.querySelector(s);
  const escHtml = (s) => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };

  // State
  let _autos = {};       // id -> automation
  let _selectedId = null;
  let _editId = null;    // null = create, string = edit
  let _aiLatLonTargetId = null;
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  // DOM
  const autoList = $("#auto-list");
  const detailContent = $("#irr-detail-content");
  const detailEmpty = $(".irr-detail-empty");
  const btnAdd = $("#btn-add-automation");
  const modalOverlay = $("#auto-modal-overlay");

  // ─── Helpers ───
  function populateTimezones() {
    const tzSelect = $("#auto-f-tz");
    if (!tzSelect) return;
    let options = "";
    for (let h = -12; h <= 14; h++) {
      for (let m of [0, 30, 45]) {
        if (h === 14 && m > 0) continue;
        if (h === -12 && m > 0) continue;
        let offsetMins = -(h * 60 + (h < 0 ? -m : m));
        if (h === 0) offsetMins = -m;
        let sign = offsetMins <= 0 ? "+" : "-";
        let displayH = Math.floor(Math.abs(offsetMins) / 60).toString().padStart(2, '0');
        let displayM = (Math.abs(offsetMins) % 60).toString().padStart(2, '0');
        let label = `UTC${sign}${displayH}:${displayM}`;
        if (offsetMins === 0) label = "UTC±00:00";
        options += `<option value="${offsetMins}">${label}</option>`;
      }
    }
    tzSelect.innerHTML = options;
  }
  populateTimezones();
  function stateClass(auto) {
    if (!auto) return "off";
    const s = auto.status;
    const rs = auto.runtime?.state || "IDLE";
    if (s !== "ON") return "off";
    if (rs === "ERROR") return "error";
    if (rs.startsWith("PAUSED")) return "paused";
    if (rs === "COMPLETED") return "completed";
    if (rs === "WAIT_CONDITION") return "waiting";
    if (rs === "IDLE") return "off";
    return "running";
  }

  function stateLabel(auto) {
    if (!auto) return "Off";
    const rs = auto.runtime?.state || "IDLE";
    if (auto.status !== "ON") return "Off";
    const map = {
      IDLE: "Off", WAIT_CONDITION: "Waiting", INIT_SET: "Initializing", INIT_VERIFY: "Verifying Init",
      ACTION_SET: "Setting Action", ACTION_VERIFY: "Verifying", ACTION_RUN: "Running",
      OVERLAP_NEXT_SET: auto.runtime?.loopingToFirst ? "Init & Setting Next" : "Setting Next",
      OVERLAP_NEXT_VERIFY: auto.runtime?.loopingToFirst ? "Verify Init & Next" : "Verifying Next",
      ACTION_REVERT: "Reverting", ACTION_VERIFY_REVERT: "Verifying Revert", BUFFER: "Buffer",
      PAUSED_CONDITION: "Paused (Condition)", PAUSED_SCHEDULE: "Paused (Schedule)", PAUSED_USER: "Paused (User)", PAUSED_ENFORCE: "Pausing for Schedule",
      COMPLETED: "Completed", ERROR_SET: "Error Recovery", ERROR_VERIFY: "Error Verify", ERROR: "Error"
    };
    return map[rs] || rs;
  }

  function getSwitchEntities() {
    const ents = [];
    if (window._dashboardEntities) {
      for (const eid in window._dashboardEntities) {
        const e = window._dashboardEntities[eid];
        if (e.type === "switch" && e.cmdTopic) ents.push(e);
      }
    }
    return ents;
  }

  function getSensorEntities() {
    const ents = [];
    if (window._dashboardEntities) {
      for (const eid in window._dashboardEntities) {
        const e = window._dashboardEntities[eid];
        if ((e.type === "binary_sensor" || e.type === "sensor") && e.stateTopic) ents.push(e);
      }
    }
    return ents;
  }

  function switchOptions(selectedCmd) {
    return getSwitchEntities().map(e => {
      const devName = window._dashboardDevices && window._dashboardDevices[e.deviceSerial] ? window._dashboardDevices[e.deviceSerial].name : "Unknown";
      const dName = `${e.name} (${devName})`;
      return `<option value="${escHtml(e.cmdTopic)}" data-state="${escHtml(e.stateTopic || "")}" data-name="${escHtml(dName)}" ${e.cmdTopic === selectedCmd ? "selected" : ""}>${escHtml(dName)}</option>`;
    }).join("");
  }

  function sensorOptions(selectedTopic) {
    return getSensorEntities().map(e => {
      const devName = window._dashboardDevices && window._dashboardDevices[e.deviceSerial] ? window._dashboardDevices[e.deviceSerial].name : "Unknown";
      const dName = `${e.name} (${devName})`;
      return `<option value="${escHtml(e.stateTopic)}" data-name="${escHtml(dName)}" ${e.stateTopic === selectedTopic ? "selected" : ""}>${escHtml(dName)}</option>`;
    }).join("");
  }

  // ─── Render List ───
  function calculateAutoProgress(auto) {
    if (!auto || !auto.actions || auto.actions.length === 0) return { pct: 0, text: "0s / 0s" };
    const rt = auto.runtime || {};
    const actions = auto.actions;
    const idx = rt.currentActionIndex || 0;
    const bufTime = auto.bufferTime ?? 5;

    let totalAutoSec = 0;
    for (let i = 0; i < actions.length; i++) {
      totalAutoSec += (actions[i].duration || 0);
    }
    if (actions.length > 1) {
      totalAutoSec += actions.length * bufTime;
    }

    let elapsedPreviousSec = 0;
    for (let i = 0; i < idx && i < actions.length; i++) {
      elapsedPreviousSec += (actions[i].duration || 0);
    }
    if (idx > 0) {
      elapsedPreviousSec += idx * bufTime;
    }

    const curAction = idx < actions.length ? actions[idx] : actions[actions.length - 1];
    const dur = curAction.duration || 0;

    let elapsedCurSec = 0;
    if (rt.state === "ACTION_RUN") {
      const timerStart = rt.timerStart || (Date.now() / 1000);
      const elapsedSec = Math.max(0, (Date.now() / 1000) - timerStart);
      elapsedCurSec = Math.min(dur, elapsedSec);
    } else if (rt.state === "BUFFER") {
      const bufStart = rt.bufferStart || (Date.now() / 1000);
      const bufElapsed = Math.max(0, Math.min(bufTime, (Date.now() / 1000) - bufStart));
      elapsedCurSec = dur + bufElapsed;
    } else if (rt.state === "COMPLETED") {
      elapsedPreviousSec = totalAutoSec;
    } else if (rt.state && rt.state.includes("OVERLAP")) {
      elapsedCurSec = dur;
    } else if (rt.state && rt.state.includes("REVERT")) {
      elapsedCurSec = idx < actions.length - 1 || rt.loopingToFirst ? dur + bufTime : dur;
    }

    const totalElapsedSec = elapsedPreviousSec + elapsedCurSec;
    let pct = totalAutoSec > 0 ? Math.min(100, Math.round((totalElapsedSec / totalAutoSec) * 100)) : 0;
    if (rt.state === "COMPLETED") pct = 100;

    const formatTimeShort = (sec) => {
      if (sec < 60) return Math.round(sec) + "s";
      return Math.round(sec / 60) + "m";
    };

    const progText = `${formatTimeShort(totalElapsedSec)} / ${formatTimeShort(totalAutoSec)}`;
    return { pct, text: progText };
  }

  function renderList() {
    const autos = Object.values(_autos);
    if (autos.length === 0) {
      autoList.innerHTML = '<div class="ha-empty-row">No automations yet. Click "Add New" to create one.</div>';
      return;
    }
    autoList.innerHTML = autos.map(a => {
      const cls = stateClass(a);
      const sel = a.id === _selectedId ? " active" : "";
      const isRunning = a.status === "ON" && a.runtime && a.runtime.state !== "IDLE" && a.runtime.state !== "ERROR";
      const prog = isRunning ? calculateAutoProgress(a) : { pct: 0, text: "" };

      return `<div class="irr-auto-item${sel}" data-id="${a.id}">
        <div style="display:flex; flex-direction:column; flex:1; min-width:0;">
          <div style="display:flex; align-items:center; gap:10px; width:100%;">
            <div class="irr-auto-item-info">
              <div class="irr-auto-item-name">${escHtml(a.name)}</div>
              <div class="irr-auto-item-status">
                <span class="irr-status-dot ${cls}"></span> ${stateLabel(a)}
              </div>
            </div>
            <label class="ha-toggle" style="pointer-events:auto; margin-left: 4px;"><input type="checkbox" ${a.status === "ON" ? "checked" : ""} data-id="${a.id}"><span class="ha-toggle-track"></span><span class="ha-toggle-thumb"></span></label>
          </div>
          <div style="display:flex; align-items:center; gap:12px; margin-top:8px;">
            <div class="irr-auto-item-cycle" style="font-size:11px; color:var(--ha-text-disabled); white-space:nowrap; min-width:45px;">
               Cycle ${a.runtime?.cycles_today || 0}${a.maxCyclesPerDay > 0 ? `/${a.maxCyclesPerDay}` : ''}
            </div>
            <div class="irr-auto-item-progress-container" style="flex:1; display:${isRunning ? 'flex' : 'none'}; align-items:center; gap:8px;">
              <div class="irr-auto-item-progress" style="flex:1; margin-top:0; display:block;">
                <div class="irr-auto-item-progress-fill" style="width: ${prog.pct}%"></div>
              </div>
              <div class="irr-auto-item-pct" style="font-size:11px; color:var(--ha-text-secondary); font-weight:500;">${prog.pct}%</div>
            </div>
          </div>
        </div>
      </div>`;
    }).join("");

    autoList.querySelectorAll(".irr-auto-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest(".ha-toggle") || e.target.closest(".irr-list-playpause")) return;
        _selectedId = el.dataset.id;
        renderList();
        renderDetail();
      });
    });
    autoList.querySelectorAll(".irr-list-playpause").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const a = _autos[btn.dataset.id];
        if (a) socket.emit("pause_automation", { id: a.id, isPaused: !a.isPaused });
      });
    });
    autoList.querySelectorAll(".ha-toggle input").forEach(inp => {
      inp.addEventListener("change", (e) => {
        e.stopPropagation();
        socket.emit("toggle_automation", { id: inp.dataset.id, status: inp.checked ? "ON" : "OFF" });
      });
    });
  }

  // ─── Live Timer Update ───
  function updateLiveTimers() {
    const auto = _autos[_selectedId];
    if (!auto || detailContent.classList.contains("hidden")) return;

    const rt = auto.runtime || {};
    const actions = auto.actions || [];
    const idx = rt.currentActionIndex || 0;

    // Helper to format seconds nicely
    function formatTime(sec) {
      if (sec < 60) return Math.round(sec) + " sec";
      return Math.round(sec / 60) + " min";
    }

    // State bar
    $("#irr-cur-state").textContent = (rt.state || "IDLE").replace(/_/g, " ");

    let curSub = "—";
    let pct = 0;
    let progText = actions.length > 0 ? `Action ${Math.min(idx + 1, actions.length)} of ${actions.length}` : "No actions";
    let nextStep = "—";
    let nextSub = "—";

    if (actions.length > 0) {
      const prog = calculateAutoProgress(auto);
      pct = prog.pct;
      progText = prog.text;
      
      // We still need nextStep/nextSub calculation here since calculateAutoProgress only does pct/text
      const curAction = idx < actions.length ? actions[idx] : actions[actions.length - 1];
      const dur = curAction.duration || 0;
      
      if (rt.state === "ACTION_RUN") {
        const timerStart = rt.timerStart || (Date.now() / 1000);
        const elapsedSec = Math.max(0, (Date.now() / 1000) - timerStart);
        const remainingCurSec = Math.max(0, dur - elapsedSec);
        curSub = `${curAction.switchName || 'Switch'} ${curAction.state} for ${formatTime(elapsedSec)}`;

        if (idx + 1 < actions.length) {
          const nextAction = actions[idx + 1];
          nextStep = `${nextAction.switchName || 'Switch'} ${nextAction.state}`;
        } else {
          if (rt.loopingToFirst || (rt.state === "ACTION_RUN" && !rt.pauseReason)) {
            const nextAction = actions[0];
            nextStep = `Initialization & ${nextAction.switchName || 'Switch'} ${nextAction.state}`;
          } else {
            const revertState = curAction.state === "ON" ? "OFF" : "ON";
            nextStep = `${curAction.switchName || 'Switch'} ${revertState}`;
          }
        }
        nextSub = `In ${formatTime(remainingCurSec)}`;

      } else if (rt.state === "BUFFER") {
        const bufTime = auto.bufferTime ?? 5;
        const bufStart = rt.bufferStart || (Date.now() / 1000);
        const bufElapsed = Math.max(0, Math.min(bufTime, (Date.now() / 1000) - bufStart));
        curSub = `Waiting for buffer`;
        nextSub = `In ${formatTime(Math.max(0, bufTime - bufElapsed))}`;
        nextStep = `Revert ${curAction.switchName || 'Switch'}`;

      } else if (rt.state === "IDLE" || rt.state === "COMPLETED") {
        curSub = rt.state === "COMPLETED" ? "Finished cycle" : "Waiting for start";
      } else {
        curSub = `Action ${Math.min(idx + 1, actions.length)}: ${curAction.switchName || 'Switch'} ${curAction.state}`;
        if (rt.loopingToFirst && rt.state.includes("OVERLAP")) {
          curSub = `Looping: Initialization & Action 1`;
        }
      }
    }

    $("#irr-cur-state-sub").textContent = curSub;
    $("#irr-progress-pct").textContent = pct + "%";
    $("#irr-progress-bar").style.width = pct + "%";
    $("#irr-progress-text").textContent = progText;
    $("#irr-next-step").textContent = nextStep;
    $("#irr-next-step-sub").textContent = nextSub;

    // Also update progress bars in the list for ALL running automations
    document.querySelectorAll(".irr-auto-item").forEach(el => {
      const a = _autos[el.dataset.id];
      if (!a) return;
      const isRunning = a.status === "ON" && a.runtime && a.runtime.state !== "IDLE" && a.runtime.state !== "ERROR";
      const progContainer = el.querySelector(".irr-auto-item-progress-container");
      const progFill = el.querySelector(".irr-auto-item-progress-fill");
      const pctLabel = el.querySelector(".irr-auto-item-pct");
      const cycleLabel = el.querySelector(".irr-auto-item-cycle");

      if (cycleLabel) {
        cycleLabel.textContent = `Cycle ${a.runtime?.cycles_today || 0}${a.maxCyclesPerDay > 0 ? `/${a.maxCyclesPerDay}` : ''}`;
      }

      if (isRunning) {
        const prog = calculateAutoProgress(a);
        if (progContainer) progContainer.style.display = "flex";
        if (progFill) progFill.style.width = prog.pct + "%";
        if (pctLabel) pctLabel.textContent = prog.pct + "%";
      } else {
        if (progContainer) progContainer.style.display = "none";
        if (pctLabel) pctLabel.textContent = "";
      }
    });
  }

  setInterval(() => {
    if (_selectedId && _autos && _autos[_selectedId]) {
      updateLiveTimers();
      updateLivePanels();
    }
  }, 1000);

  function updateLivePanels() {
    const auto = _autos[_selectedId];
    if (!auto) return;

    const inits = auto.initialization || [];
    const actions = auto.actions || [];
    const errs = auto.errorState || [];
    const conds = auto.condition || [];
    const schedTrue = auto.schedule?.setIfTrue || [];
    const schedFalse = auto.schedule?.setIfFalse || [];

    // Live Switches
    const liveSw = $("#irr-live-switches");
    if (liveSw) {
      const switchMap = new Map();
      [...inits, ...actions, ...errs, ...schedTrue, ...schedFalse].forEach(s => {
        if (s.switchName) switchMap.set(s.switchName, s.switchStateTopic || s.switchCmdTopic);
      });
      if (switchMap.size > 0) {
        liveSw.innerHTML = `<div style="display:flex;gap:12px;flex-wrap:wrap;">${Array.from(switchMap.entries()).map(([name, topic]) => {
          let liveState = "Unknown";
          if (window._dashboardEntities) {
            for (const eid in window._dashboardEntities) {
              const e = window._dashboardEntities[eid];
              if (e.stateTopic === topic || e.cmdTopic === topic) { liveState = (e.state || "Unknown").toUpperCase(); break; }
            }
          }
          const isOn = liveState === "ON";
          return `<div class="irr-live-sw">
              <span class="material-symbols-outlined irr-live-sw-icon ${isOn ? 'on' : ''}">${isOn ? 'water_drop' : 'water_drop'}</span>
              <span class="irr-live-sw-name">${escHtml(name)}</span>
              <span class="irr-live-sw-state" style="color:${isOn ? '#4caf50' : '#f44336'}">${liveState}</span>
            </div>`;
        }).join("")}</div>`;
      } else {
        liveSw.innerHTML = '<span style="color:var(--ha-text-disabled);font-size:12px">No switches</span>';
      }
    }

    // Live Sensors
    const liveSen = $("#irr-live-sensors");
    if (liveSen) {
      const sensorMap = new Map();
      conds.forEach(c => {
        if (c.sensorName || c.sensorStateTopic) sensorMap.set(c.sensorName || "Sensor", c.sensorStateTopic);
      });
      if (sensorMap.size > 0) {
        liveSen.innerHTML = `<div style="display:flex;gap:12px;flex-wrap:wrap;">${Array.from(sensorMap.entries()).map(([name, topic]) => {
          let liveState = "Unknown";
          if (window._dashboardEntities) {
            for (const eid in window._dashboardEntities) {
              const e = window._dashboardEntities[eid];
              if (e.stateTopic === topic || e.cmdTopic === topic) { liveState = (e.state || "Unknown").toUpperCase(); break; }
            }
          }
          const isOn = liveState === "ON";
          return `<div class="irr-live-sw">
              <span class="material-symbols-outlined irr-live-sw-icon ${isOn ? 'on' : ''}">sensors</span>
              <span class="irr-live-sw-name">${escHtml(name)}</span>
              <span class="irr-live-sw-state" style="color:${isOn ? '#4caf50' : (liveState === 'OFF' ? '#f44336' : 'var(--ha-text-secondary)')}">${liveState}</span>
            </div>`;
        }).join("")}</div>`;
      } else {
        liveSen.innerHTML = '<span style="color:var(--ha-text-disabled);font-size:12px">No sensors</span>';
      }
    }

    // Also update condition live state text if visible
    const condBody = $("#irr-cond-body");
    if (condBody) {
      const rows = condBody.querySelectorAll(".irr-cond-live");
      rows.forEach((span, i) => {
        if (conds[i]) {
          const topic = conds[i].sensorStateTopic || "";
          let lState = "Unknown";
          if (window._dashboardEntities) {
            for (const eid in window._dashboardEntities) {
              const e = window._dashboardEntities[eid];
              if (e.stateTopic === topic || e.cmdTopic === topic) { lState = (e.state || "Unknown").toUpperCase(); break; }
            }
          }
          const lColor = lState === "ON" ? "color:var(--ha-state-on)" : (lState === "OFF" ? "color:var(--ha-state-off)" : "");
          span.innerHTML = `(Live: <span style="font-weight:600; ${lColor}">${lState}</span>)`;
        }
      });
    }

    // Also update initialization live state text if visible
    const initBody = $("#irr-init-body");
    if (initBody) {
      const rows = initBody.querySelectorAll(".irr-init-live");
      rows.forEach((span, i) => {
        if (inits[i]) {
          const topic = inits[i].switchStateTopic || inits[i].switchCmdTopic || "";
          let lState = "Unknown";
          if (window._dashboardEntities) {
            for (const eid in window._dashboardEntities) {
              const e = window._dashboardEntities[eid];
              if (e.stateTopic === topic || e.cmdTopic === topic) { lState = (e.state || "Unknown").toUpperCase(); break; }
            }
          }
          const lColor = lState === "ON" ? "color:var(--ha-state-on)" : (lState === "OFF" ? "color:var(--ha-state-off)" : "");
          span.innerHTML = `(Live: <span style="font-weight:600; ${lColor}">${lState}</span>)`;
        }
      });
    }
  }

  // ─── Render Detail ───
  function renderDetail() {
    const auto = _autos[_selectedId];
    if (!auto) { detailContent.classList.add("hidden"); detailEmpty.style.display = ""; return; }
    detailEmpty.style.display = "none";
    detailContent.classList.remove("hidden");

    const cls = stateClass(auto);
    const rt = auto.runtime || {};
    const actions = auto.actions || [];
    const idx = rt.currentActionIndex || 0;

    // Header
    $("#irr-detail-name").textContent = auto.name;
    const btnPlayPause = $("#irr-btn-playpause");
    const iconPlayPause = $("#irr-icon-playpause");
    if (auto.status === "ON") {
      btnPlayPause.style.display = "flex";
      iconPlayPause.textContent = auto.isPaused ? "play_arrow" : "pause";
      btnPlayPause.style.background = auto.isPaused ? "var(--ha-warning)" : "var(--ha-primary)";
      btnPlayPause.onclick = () => {
        socket.emit("pause_automation", { id: auto.id, isPaused: !auto.isPaused });
      };
    } else {
      btnPlayPause.style.display = "none";
    }

    $("#irr-detail-desc").textContent = auto.description || "";
    const badge = $("#irr-detail-badge");
    badge.textContent = stateLabel(auto);
    badge.className = "irr-status-badge " + cls;

    // Run the live timer update logic
    updateLiveTimers();

    const toggle = $("#irr-status-toggle");
    toggle.checked = auto.status === "ON";
    $(".toggle-text-on").textContent = auto.status === "ON" ? "ON" : "OFF";
    toggle.onchange = () => socket.emit("toggle_automation", { id: auto.id, status: toggle.checked ? "ON" : "OFF" });

    // Init
    const initBody = $("#irr-init-body");
    const inits = auto.initialization || [];
    initBody.innerHTML = inits.map(i => `<div class="irr-sw-row"><span>${escHtml(i.switchName || i.switchCmdTopic || "Switch")} <span class="irr-init-live" style="margin-left:12px; font-size:12px; color:var(--ha-text-secondary);"></span></span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">None configured</span>';

    // Condition
    const condBody = $("#irr-cond-body");
    const conds = auto.condition || [];
    condBody.innerHTML = conds.map((c, i) => {
      const logicBadge = c.logic && i < conds.length - 1 ? `<span class="irr-cond-logic">${c.logic}</span>` : "";
      return `<div class="irr-cond-row"><span class="irr-cond-sensor">${escHtml(c.sensorName || c.sensorStateTopic || "Sensor")}</span><span class="irr-cond-op">=</span><span class="irr-cond-val">${escHtml(c.value || "")}</span><span class="irr-cond-live"></span>${logicBadge}</div>`;
    }).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">No conditions (always true)</span>';

    // Actions
    const maxCycles = auto.maxCyclesPerDay || 0;
    const cyclesToday = rt.cycles_today || 0;
    const cyclesBadge = maxCycles > 0 ? `<span style="margin-left:8px;font-size:11px;padding:2px 8px;border-radius:10px;background:var(--ha-surface-alt, #1e293b);color:var(--ha-primary, #03a9f4);">🔄 ${cyclesToday}/${maxCycles} cycles today</span>` : '';
    const actionsHeading = $("#main-actions-heading");
    if (actionsHeading) actionsHeading.innerHTML = `Actions (Sequential)${cyclesBadge}`;
    const actBody = $("#irr-actions-body");
    actBody.innerHTML = actions.length > 0 ? `<table class="irr-actions-table"><thead><tr><th>#</th><th>Switch</th><th>State</th><th>Duration</th><th>Status</th></tr></thead><tbody>${actions.map((a, i) => {
      const isActive = i === idx && (rt.state || "").startsWith("ACTION");
      const durStr = a.duration > 0 ? (a.duration >= 60 ? Math.round(a.duration / 60) + " min" : a.duration + "s") : "—";
      let status = "⏳ Pending";
      if (i < idx) status = "✔ Done";
      if (isActive) status = "▶ " + (rt.state === "ACTION_RUN" ? "Running" : "Processing");
      return `<tr class="${isActive ? "active-action" : ""}"><td>${i + 1}</td><td>${escHtml(a.switchName || "Switch")}</td><td><span class="irr-sw-state ${a.state === 'ON' ? 'on' : 'off'}">${a.state}</span></td><td>${durStr}</td><td class="irr-action-status">${status}</td></tr>`;
    }).join("")}</tbody></table>` : '<span style="color:var(--ha-text-disabled);font-size:12px">No actions configured</span>';

    // Error state
    const errBody = $("#irr-error-body");
    const errs = auto.errorState || [];
    errBody.innerHTML = errs.map(e => `<div class="irr-sw-row"><span>${escHtml(e.switchName || "Switch")}</span><span class="irr-sw-state off">${e.state || "OFF"}</span></div>`).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">None configured</span>';

    // Scheduler
    const schedBody = $("#irr-sched-body");
    const sched = auto.schedule || {};
    const days = sched.days || [];
    const is24hr = !!sched.is24hr;
    const aiEnabled = sched.ai_enabled || false;

    const schedHeading = $("#main-sched-heading");
    if (schedHeading) schedHeading.textContent = aiEnabled ? "🤖 AI Scheduler" : "Scheduler";

    let timeStr = "";
    if (is24hr) {
      timeStr = "24-Hour Active";
    } else {
      const ranges = sched.timeRanges || [];
      if (ranges.length > 0) {
        timeStr = ranges.map(r => `${r.start || "?"} → ${r.end || "?"}`).join(", ");
      } else if (sched.startTime || sched.endTime) {
        timeStr = `${sched.startTime || "?"} → ${sched.endTime || "?"}`;
      } else {
        timeStr = "No time range";
      }
    }

    schedBody.innerHTML = `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;width:100%;">
      <div><span class="irr-label">Active Days</span><div class="irr-day-chips" style="margin-top:6px; ${aiEnabled ? 'pointer-events:none; border: 1px dashed var(--ha-primary); padding: 4px; border-radius: 8px;' : ''}">${DAY_NAMES.map(d => `<span class="irr-day-chip ${days.includes(d) ? 'active' : ''}">${d}</span>`).join("")}</div></div>
      <div><span class="irr-label">Time Range</span><div class="irr-time-display" style="margin-top:6px">${timeStr}</div></div>
      <div style="margin-left: auto; display: flex; align-items: center; gap: 8px;">
        <span style="font-size: 14px; font-weight: bold; color: var(--ha-primary, #03a9f4); letter-spacing: 0.5px;">🤖 AI</span>
        <label class="ha-toggle irr-custom-toggle" style="cursor: pointer; margin: 0;" title="Enable AI Dynamic Scheduling">
            <input type="checkbox" id="irr-main-ai-toggle" ${aiEnabled ? 'checked' : ''}>
            <span class="ha-toggle-track"></span>
            <span class="ha-toggle-thumb"></span>
        </label>
      </div>
    </div>`;

    const setTrueHTML = (sched.setIfTrue || []).map(i => `<div class="irr-sw-row"><span>${escHtml(i.switchName || i.switchCmdTopic || "Switch")}</span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`).join("");
    const setFalseHTML = (sched.setIfFalse || []).map(i => `<div class="irr-sw-row"><span>${escHtml(i.switchName || i.switchCmdTopic || "Switch")}</span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`).join("");

    if (setTrueHTML || setFalseHTML) {
      schedBody.innerHTML += `<div style="display:flex;gap:20px;margin-top:16px;">
        ${setTrueHTML ? `<div style="flex:1;"><span class="irr-label">During Schedule</span><div style="margin-top:6px;">${setTrueHTML}</div></div>` : ''}
        ${setFalseHTML ? `<div style="flex:1;"><span class="irr-label">Outside Schedule</span><div style="margin-top:6px;">${setFalseHTML}</div></div>` : ''}
      </div>`;
    }

    // Use event delegation on the parent body to guarantee the click is captured regardless of CSS
    schedBody.onclick = (e) => {
      const toggleWrap = e.target.closest(".ha-toggle");
      if (toggleWrap && toggleWrap.querySelector("#irr-main-ai-toggle")) {
        const toggleInput = toggleWrap.querySelector("#irr-main-ai-toggle");
        setTimeout(() => {
          try {
            const isChecked = toggleInput.checked;
            const lat = sched.lat;
            const lon = sched.lon;
            const isMissing = !lat || !lon || isNaN(parseFloat(lat)) || isNaN(parseFloat(lon));
            
            if (isChecked && isMissing) {
              // Revert toggle visually
              toggleInput.checked = false;
              // Simple, foolproof browser alert just like the Name Validation
              alert("Location is missing!\n\nPlease click the 'Edit' button and enter your Latitude and Longitude to enable the AI Agronomist.");
            } else if (isChecked && !isMissing) {
              auto.schedule.ai_enabled = true;
              socket.emit("update_automation", auto);
            } else if (!isChecked) {
              auto.schedule.ai_enabled = false;
              socket.emit("update_automation", auto);
            }
          } catch (err) {
            console.error("AI Toggle Error:", err);
          }
        }, 50);
      }
    };

    updateLivePanels();

    // Activity log
    const logEl = $("#irr-activity-log");
    const logs = auto.logs || [];
    logEl.innerHTML = logs.length > 0 ? `<div class="irr-log-list">${logs.slice(0, 15).map(l => {
      const t = l.ts ? new Date(l.ts).toLocaleTimeString() : "";
      return `<div class="irr-log-entry"><span class="irr-log-dot ${l.level || 'info'}"></span><span class="irr-log-time">${escHtml(t)}</span><span>${escHtml(l.msg || "")}</span></div>`;
    }).join("")}</div>` : '<span style="color:var(--ha-text-disabled);font-size:12px">No activity yet</span>';

    // Button handlers
    $("#irr-btn-reset").onclick = () => socket.emit("reset_automation", { id: auto.id });
    $("#irr-btn-delete").onclick = () => { if (confirm("Delete " + auto.name + "?")) socket.emit("delete_automation", { id: auto.id }); };
    $("#irr-btn-edit").onclick = () => openModal(auto.id);
  }

  // ─── Modal ───
  function openModal(editId) {
    _editId = editId || null;
    const auto = _editId ? _autos[_editId] : null;
    $("#auto-modal-title").textContent = auto ? "Edit Automation" : "New Automation";
    $("#auto-f-name").value = auto?.name || "";
    $("#auto-f-desc").value = auto?.description || "";

    // Days
    const daysEl = $("#auto-f-days");
    const selDays = new Set(auto?.schedule?.days || []);
    daysEl.innerHTML = DAY_NAMES.map(d => `<button type="button" class="irr-day-btn ${selDays.has(d) ? 'active' : ''}" data-day="${d}">${d}</button>`).join("");
    daysEl.querySelectorAll(".irr-day-btn").forEach(b => b.addEventListener("click", () => b.classList.toggle("active")));



    $("#auto-f-lat").value = auto?.schedule?.lat || "";
    $("#auto-f-lon").value = auto?.schedule?.lon || "";

    const cb24 = $("#auto-f-24hr");
    if (cb24) {
      // Default to 24hr checked for new automations, respect saved value for edits
      const is24hr = editId ? !!(auto?.schedule?.is24hr) : (auto?.schedule?.is24hr !== undefined ? !!(auto?.schedule?.is24hr) : true);
      cb24.checked = is24hr;
      cb24.onchange = (e) => {
        const list = $("#auto-f-times-list");
        const addBtn = $("#auto-f-time-add");
        if (e.target.checked) {
          list.style.opacity = "0.5";
          list.style.pointerEvents = "none";
          addBtn.style.display = "none";
        } else {
          list.style.opacity = "1";
          list.style.pointerEvents = "auto";
          addBtn.style.display = "block";
        }
      };
      // trigger initial state
      cb24.onchange({ target: cb24 });
    }

    // Auto-detect browser offset if no offset is configured yet
    const tzSelect = $("#auto-f-tz");
    if (tzSelect) {
      tzSelect.value = auto?.schedule?.utcOffset ?? new Date().getTimezoneOffset();
      // Fallback to first if somehow the browser offset isn't in the list
      if (!tzSelect.value) tzSelect.selectedIndex = 0;
    }

    $("#auto-f-buffer").value = auto?.bufferTime ?? 5;
    $("#auto-f-max-cycles").value = auto?.maxCyclesPerDay ?? 0;

    // Time ranges
    let tRanges = auto?.schedule?.timeRanges || [];
    // Migration: if old single range exists, use it
    if (tRanges.length === 0 && (auto?.schedule?.startTime || auto?.schedule?.endTime)) {
      tRanges = [{ start: auto.schedule.startTime, end: auto.schedule.endTime }];
    }
    // If empty and not 24hr, add one empty row
    if (tRanges.length === 0 && !auto?.schedule?.is24hr) tRanges = [{ start: "", end: "" }];
    renderFormRows("auto-f-times-list", tRanges, "timeRange");

    // Init rows
    renderFormRows("auto-f-init", auto?.initialization || [], "switch");
    // Set if True / Set if False rows
    renderFormRows("auto-f-set-true", auto?.schedule?.setIfTrue || [], "switch");
    renderFormRows("auto-f-set-false", auto?.schedule?.setIfFalse || [], "switch");
    // Condition rows
    renderFormRows("auto-f-cond", auto?.condition || [], "condition");
    // Action rows
    renderFormRows("auto-f-actions", auto?.actions || [], "action");
    // Error rows
    renderFormRows("auto-f-error", auto?.errorState || [], "switch");

    modalOverlay.classList.remove("hidden");
  }

  function closeModal() { modalOverlay.classList.add("hidden"); }

  function renderFormRows(containerId, items, type) {
    const container = $(`#${containerId}`);
    container.innerHTML = "";
    items.forEach((item, i) => addFormRow(container, type, item));
  }

  function addFormRow(container, type, data) {
    const row = document.createElement("div");
    row.className = "irr-form-row";
    if (type === "switch") {
      row.innerHTML = `<select class="f-switch">${switchOptions(data?.switchCmdTopic || "")}</select>
        <select class="f-state"><option value="ON" ${data?.state === "ON" ? "selected" : ""}>ON</option><option value="OFF" ${data?.state !== "ON" ? "selected" : ""}>OFF</option></select>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    } else if (type === "condition") {
      row.innerHTML = `<select class="f-sensor">${sensorOptions(data?.sensorStateTopic || "")}</select>
        <span class="irr-cond-op">=</span>
        <select class="f-state"><option value="ON" ${data?.value === "ON" ? "selected" : ""}>ON</option><option value="OFF" ${data?.value !== "ON" ? "selected" : ""}>OFF</option><option value="HIGH" ${data?.value === "HIGH" ? "selected" : ""}>HIGH</option><option value="LOW" ${data?.value === "LOW" ? "selected" : ""}>LOW</option></select>
        <select class="f-logic"><option value="AND" ${data?.logic !== "OR" ? "selected" : ""}>AND</option><option value="OR" ${data?.logic === "OR" ? "selected" : ""}>OR</option></select>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    } else if (type === "action") {
      const dur = data?.duration || 0;
      const h = Math.floor(dur / 3600);
      const m = Math.floor((dur % 3600) / 60);
      const s = dur % 60;
      row.innerHTML = `<select class="f-switch">${switchOptions(data?.switchCmdTopic || "")}</select>
        <select class="f-state"><option value="ON" ${data?.state === "ON" ? "selected" : ""}>ON</option><option value="OFF" ${data?.state !== "ON" ? "selected" : ""}>OFF</option></select>
        <div style="display:flex;gap:4px;align-items:center;">
          <input type="number" class="f-dur-h" value="${h}" min="0" max="99" style="width:48px;text-align:center;padding:8px 4px;" oninput="if(this.value.length > 2) this.value = this.value.slice(0,2)">
          <span style="font-size:13px;color:var(--ha-text-secondary);margin-right:4px;">h</span>
          <input type="number" class="f-dur-m" value="${m}" min="0" max="59" style="width:48px;text-align:center;padding:8px 4px;" oninput="if(this.value.length > 2) this.value = this.value.slice(0,2)">
          <span style="font-size:13px;color:var(--ha-text-secondary);margin-right:4px;">m</span>
          <input type="number" class="f-dur-s" value="${s}" min="0" max="59" style="width:48px;text-align:center;padding:8px 4px;" oninput="if(this.value.length > 2) this.value = this.value.slice(0,2)">
          <span style="font-size:13px;color:var(--ha-text-secondary);">s</span>
        </div>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    } else if (type === "timeRange") {
      row.innerHTML = `<div class="ha-field" style="flex:1"><input type="time" class="f-start" value="${data?.start || ""}" placeholder=" "><label>Start</label></div>
        <span style="padding-top:12px">→</span>
        <div class="ha-field" style="flex:1"><input type="time" class="f-end" value="${data?.end || ""}" placeholder=" "><label>End</label></div>
        <button type="button" class="irr-remove-btn material-symbols-outlined" style="margin-top:12px">close</button>`;
    }
    row.querySelector(".irr-remove-btn")?.addEventListener("click", () => row.remove());
    container.appendChild(row);
  }

  // Add row buttons
  ["auto-f-init-add", "auto-f-error-add", "auto-f-set-true-add", "auto-f-set-false-add"].forEach(id => {
    $(`#${id}`)?.addEventListener("click", () => addFormRow($(`#${id.replace("-add", "")}`), "switch", {}));
  });
  $("#auto-f-cond-add")?.addEventListener("click", () => addFormRow($("#auto-f-cond"), "condition", {}));
  $("#auto-f-actions-add")?.addEventListener("click", () => addFormRow($("#auto-f-actions"), "action", {}));
  $("#auto-f-time-add")?.addEventListener("click", () => addFormRow($("#auto-f-times-list"), "timeRange", {}));

  function collectFormData() {
    const name = $("#auto-f-name").value.trim();
    if (!name) { alert("Name is required"); return null; }
    const days = [...$("#auto-f-days").querySelectorAll(".irr-day-btn.active")].map(b => b.dataset.day);

    const collectSwitchRows = (containerId) => [...$(`#${containerId}`).querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-switch");
      const opt = sel?.selectedOptions[0];
      return { switchCmdTopic: sel?.value || "", switchStateTopic: opt?.dataset.state || "", switchName: opt?.dataset.name || "", state: r.querySelector(".f-state")?.value || "OFF" };
    });

    const conds = [...$("#auto-f-cond").querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-sensor");
      const opt = sel?.selectedOptions[0];
      return { sensorStateTopic: sel?.value || "", sensorName: opt?.dataset.name || "", value: r.querySelector(".f-state")?.value || "OFF", logic: r.querySelector(".f-logic")?.value || "AND" };
    });

    const actions = [...$("#auto-f-actions").querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-switch");
      const opt = sel?.selectedOptions[0];
      const h = parseInt(r.querySelector(".f-dur-h")?.value || "0", 10);
      const m = parseInt(r.querySelector(".f-dur-m")?.value || "0", 10);
      const s = parseInt(r.querySelector(".f-dur-s")?.value || "0", 10);
      return { switchCmdTopic: sel?.value || "", switchStateTopic: opt?.dataset.state || "", switchName: opt?.dataset.name || "", state: r.querySelector(".f-state")?.value || "ON", duration: (h * 3600) + (m * 60) + s };
    });

    const timeRanges = [...$("#auto-f-times-list").querySelectorAll(".irr-form-row")].map(r => ({
      start: r.querySelector(".f-start")?.value || "",
      end: r.querySelector(".f-end")?.value || ""
    }));

    const is24hr = $("#auto-f-24hr").checked;

    // Validate: if 24hr is off, at least one complete time range is required
    if (!is24hr) {
      const validRanges = timeRanges.filter(r => r.start && r.end);
      if (validRanges.length === 0) {
        alert("Time range required!\n\nEither enable '24-Hour Active' or add at least one time range with both start and end times.");
        return null;
      }
    }

    const editAuto = _editId ? _autos[_editId] : null;
    const schedObj = {
      days,
      timeRanges,
      is24hr,
      utcOffset: parseInt($("#auto-f-tz").value, 10) || 0,
      ai_enabled: editAuto?.schedule?.ai_enabled || false,
      setIfTrue: collectSwitchRows("auto-f-set-true"),
      setIfFalse: collectSwitchRows("auto-f-set-false")
    };
    const latStr = $("#auto-f-lat").value;
    const lonStr = $("#auto-f-lon").value;
    if (latStr !== "" && lonStr !== "") {
      schedObj.lat = parseFloat(latStr);
      schedObj.lon = parseFloat(lonStr);
    }
    return {
      name, description: $("#auto-f-desc").value.trim(),
      schedule: schedObj,
      condition: conds,
      initialization: collectSwitchRows("auto-f-init"),
      actions,
      errorState: collectSwitchRows("auto-f-error"),
      bufferTime: parseInt($("#auto-f-buffer")?.value || "5", 10),
      maxCyclesPerDay: parseInt($("#auto-f-max-cycles")?.value || "0", 10),
    };
  }

  // Save
  $("#auto-modal-save")?.addEventListener("click", () => {
    const data = collectFormData();
    if (!data) return;
    if (_editId) {
      data.id = _editId;
      socket.emit("update_automation", data);
    } else {
      socket.emit("create_automation", data);
    }
    closeModal();
  });

  // Modal controls
  btnAdd?.addEventListener("click", () => openModal(null));
  $("#auto-modal-close")?.addEventListener("click", closeModal);
  $("#auto-modal-cancel")?.addEventListener("click", closeModal);
  modalOverlay?.addEventListener("click", (e) => { if (e.target === modalOverlay) closeModal(); });

  // ─── Socket events ───
  socket.on("automations_list", (list) => {
    _autos = {};
    (list || []).forEach(a => { _autos[a.id] = a; });
    renderList();
    renderDetail();
  });

  socket.on("automation_update", (data) => {
    const auto = data.automation;
    if (!auto) return;
    auto.logs = data.logs || [];
    _autos[auto.id] = auto;
    renderList();
    if (_selectedId === auto.id) renderDetail();
  });

  socket.on("automation_created", (data) => {
    _selectedId = data.id;
    socket.emit("get_automations");
  });

  socket.on("automation_deleted", (data) => {
    delete _autos[data.id];
    if (_selectedId === data.id) { _selectedId = null; }
    renderList();
    renderDetail();
  });

  // Request automations when tab is shown
  document.querySelectorAll(".ha-nav-item").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "automations") socket.emit("get_automations");
    });
  });

  // Initial load
  socket.on("mqtt_status", (data) => {
    if (data.connected) setTimeout(() => socket.emit("get_automations"), 1000);
  });

  // ─── AI Location Modal ───
  $("#ai-latlon-save")?.addEventListener("click", () => {
    const lat = parseFloat($("#ai-f-lat").value);
    const lon = parseFloat($("#ai-f-lon").value);
    if (isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      alert("Please enter valid Latitude (-90 to 90) and Longitude (-180 to 180).");
      return;
    }
    $("#ai-latlon-overlay").classList.add("hidden");

    if (_aiLatLonTargetId && _autos[_aiLatLonTargetId]) {
      const auto = _autos[_aiLatLonTargetId];
      if (!auto.schedule) auto.schedule = {};
      auto.schedule.lat = lat;
      auto.schedule.lon = lon;
      auto.schedule.ai_enabled = true;
      socket.emit("update_automation", auto);
      _aiLatLonTargetId = null;
    }
  });
  $("#ai-latlon-cancel")?.addEventListener("click", () => {
    $("#ai-latlon-overlay").classList.add("hidden");
    _aiLatLonTargetId = null;
  });
  $("#ai-latlon-overlay")?.addEventListener("click", (e) => {
    if (e.target === $("#ai-latlon-overlay")) {
      $("#ai-latlon-overlay").classList.add("hidden");
      _aiLatLonTargetId = null;
    }
  });

  // Global Button Handlers
  btnAdd?.addEventListener("click", () => openModal(null));
  $("#auto-modal-cancel")?.addEventListener("click", closeModal);
  $("#auto-modal-close")?.addEventListener("click", closeModal);

  // ─── Analytics Modal ───
  let _durationChart = null;
  let _cyclesChart = null;
  let _weatherChart = null;

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#irr-btn-analytics");
    if (btn) {
      if (!_selectedId || !_autos[_selectedId]) return;
      openAnalyticsModal(_autos[_selectedId]);
    }
  });

  $("#auto-analytics-close")?.addEventListener("click", () => {
    $("#auto-analytics-modal").classList.add("hidden");
  });

  $("#auto-analytics-modal")?.addEventListener("click", (e) => {
    if (e.target === $("#auto-analytics-modal")) {
      $("#auto-analytics-modal").classList.add("hidden");
    }
  });

  function openAnalyticsModal(auto) {
    $("#auto-analytics-modal").classList.remove("hidden");
    
    // AI Reasoning
    const aiContainer = $("#ai-agronomist-report-container");
    const aiText = $("#ai-agronomist-report-text");
    const sched = auto.schedule || {};
    if (sched.ai_enabled && auto.ai_last_reasoning) {
        aiContainer.classList.remove("hidden");
        aiText.textContent = auto.ai_last_reasoning;
    } else {
        aiContainer.classList.add("hidden");
    }

    // Fetch Weather Insights
    const et0El = document.getElementById("auto-analytics-et0-snapshot");
    const windEl = document.getElementById("auto-analytics-wind-snapshot");
    
    if (et0El) et0El.textContent = "...";
    if (windEl) windEl.textContent = "...";
    
    if (sched.lat && sched.lon) {
        socket.emit("get_weather_insights", { auto_id: auto.id, lat: sched.lat, lon: sched.lon });
    } else {
        if (et0El) et0El.textContent = "-";
        if (windEl) windEl.textContent = "-";
    }

    // Parse data
    const runtime = auto.runtime || {};
    const cycHist = runtime.cycles_history || {};
    const durHist = runtime.duration_history || {};
    
    // Create an array of the last 7 days (including days with no data)
    const dates = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      dates.push(d.toISOString().split('T')[0]);
    }
    
    // Calculate summaries
    let totalCycles = 0;
    let totalSecs = 0;
    const cycData = [];
    const durData = [];
    
    dates.forEach(d => {
      const c = cycHist[d] || 0;
      const s = durHist[d] || 0;
      totalCycles += c;
      totalSecs += s;
      cycData.push(c);
      durData.push(Math.round(s / 60)); // Minutes
    });
    
    // Format total time
    const tH = Math.floor(totalSecs / 3600);
    const tM = Math.floor((totalSecs % 3600) / 60);
    $("#auto-analytics-total-time").textContent = tH > 0 ? `${tH}h ${tM}m` : `${tM}m`;
    $("#auto-analytics-total-cycles").textContent = totalCycles;
    
    const lastRun = runtime.last_irrigated || "Never";
    $("#auto-analytics-last-run").textContent = lastRun;
    
    // Render Charts
    if (_durationChart) _durationChart.destroy();
    if (_cyclesChart) _cyclesChart.destroy();
    
    const ctxDur = document.getElementById("analytics-chart-duration").getContext("2d");
    const ctxCyc = document.getElementById("analytics-chart-cycles").getContext("2d");
    
    Chart.defaults.color = "rgba(255, 255, 255, 0.7)";
    Chart.defaults.font.family = "'Roboto', sans-serif";
    
    _durationChart = new Chart(ctxDur, {
      type: "line",
      data: {
        labels: dates,
        datasets: [{
          label: "Minutes",
          data: durData,
          borderColor: "#03a9f4",
          backgroundColor: "rgba(3, 169, 244, 0.1)",
          borderWidth: 2,
          fill: true,
          tension: 0.3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(255, 255, 255, 0.05)" } },
          x: { grid: { display: false } }
        }
      }
    });
    
    _cyclesChart = new Chart(ctxCyc, {
      type: "bar",
      data: {
        labels: dates,
        datasets: [{
          label: "Cycles",
          data: cycData,
          backgroundColor: "#4caf50",
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, ticks: { stepSize: 1 }, grid: { color: "rgba(255, 255, 255, 0.05)" } },
          x: { grid: { display: false } }
        }
      }
    });
  }

  socket.on("weather_insights_data", (data) => {
    const auto_id = data.auto_id;
    const weather = data.weather;
    if (!weather) return;

    // Build 14-day labels and data arrays
    const labels = [];
    const rainData = [];
    const et0Data = [];

    // Parse past days
    Object.values(weather.past_days || {}).forEach(day => {
        labels.push(day.date);
        rainData.push(day.rain_mm || 0);
        et0Data.push(day.et0_mm || 0);
    });

    // Parse today
    if (weather.today) {
        labels.push("Today");
        rainData.push(weather.today.rain_mm || 0);
        et0Data.push(weather.today.et0_mm || 0);
        
        // Update Snapshot
        const et = weather.today.et0_mm || 0;
        const wind = weather.today.max_wind_kmh || 0;
        
        const et0El = document.getElementById("auto-analytics-et0-snapshot");
        const windEl = document.getElementById("auto-analytics-wind-snapshot");
        
        if (et0El) {
            et0El.innerHTML = `${et} <span style="font-size:12px;color:var(--ha-text-secondary);">mm</span>`;
        }
        if (windEl) {
            windEl.innerHTML = `${wind} <span style="font-size:12px;color:var(--ha-text-secondary);">km/h</span>`;
        }
    }

    // Parse forecast
    Object.values(weather.forecast || {}).forEach(day => {
        labels.push(day.date);
        rainData.push(day.rain_mm || 0);
        et0Data.push(day.et0_mm || 0);
    });

    // Render Timeline Overlay
    const timelineEl = document.getElementById("auto-analytics-timeline");
    if (timelineEl) {
        let timelineHTML = "";
        const auto = _autos[auto_id] || {};
        const schedDays = (auto.schedule && auto.schedule.days) || [];
        const cycHist = (auto.runtime && auto.runtime.cycles_history) || {};
        const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

        const allDays = [];
        Object.values(weather.past_days || {}).forEach(d => allDays.push({...d, is_past: true}));
        if (weather.today) allDays.push({...weather.today, is_today: true});
        Object.values(weather.forecast || {}).forEach(d => allDays.push({...d, is_future: true}));

        allDays.forEach(day => {
            const dateObj = new Date(day.date);
            const dayNum = dateObj.getDate();
            const dayStr = dayNames[dateObj.getDay()];
            
            let isWaterDay = false;
            if (day.is_past || day.is_today) {
                if (cycHist[day.date] && cycHist[day.date] > 0) isWaterDay = true;
            }
            if (day.is_future || day.is_today) {
                if (schedDays.includes(dayStr)) isWaterDay = true;
            }

            let iconHTML = "";
            if (isWaterDay) {
                iconHTML = `
                <div style="background: rgba(3, 169, 244, 0.1); border: 1px solid rgba(3,169,244,0.3); border-radius: 8px; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; box-shadow: 0 0 10px rgba(3,169,244,0.2);">
                    <span class="material-symbols-outlined" style="color: #03a9f4; font-size: 20px;">water_drop</span>
                </div>`;
            } else if (day.rain_mm > 0.5) {
                iconHTML = `<span class="material-symbols-outlined" style="color: #e1e1e1; font-size: 24px;">rainy</span>`;
            } else {
                iconHTML = `<span class="material-symbols-outlined" style="color: #ff9800; font-size: 24px;">sunny</span>`;
            }

            let colorStyle = "color: var(--ha-text-secondary);";
            let containerStyle = "display: flex; flex-direction: column; align-items: center; gap: 8px; min-width: 32px;";
            
            if (day.is_today) {
                colorStyle = "color: #4CAF50; font-weight: bold;";
                containerStyle = "display: flex; flex-direction: column; align-items: center; gap: 8px; min-width: 40px; padding: 8px 4px; border-radius: 20px; background: rgba(76, 175, 80, 0.15); border: 1px solid rgba(76, 175, 80, 0.3);";
            }

            timelineHTML += `
            <div style="${containerStyle}">
                <div style="font-size: 13px; ${colorStyle}">${dayNum}</div>
                ${iconHTML}
            </div>`;
        });
        timelineEl.innerHTML = timelineHTML;
    }

    const ctxWeather = document.getElementById("analytics-chart-weather").getContext("2d");
    if (_weatherChart) _weatherChart.destroy();

    _weatherChart = new Chart(ctxWeather, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Rainfall (mm)',
                    data: rainData,
                    backgroundColor: '#03a9f4',
                    borderRadius: 4,
                    order: 2
                },
                {
                    label: 'Evaporation (ET0 mm)',
                    data: et0Data,
                    backgroundColor: '#e67e22',
                    borderRadius: 4,
                    order: 3
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top', labels: { color: 'rgba(255,255,255,0.7)' } }
            },
            scales: {
                y: { 
                    beginAtZero: true, 
                    title: { display: true, text: 'Water (mm)', color: 'rgba(255,255,255,0.7)' },
                    grid: { color: "rgba(255, 255, 255, 0.05)" } 
                },
                x: { grid: { display: false } }
            }
        }
    });
  });

  // Start live timer loop
  setInterval(updateLiveTimers, 1000);
  });
})();
