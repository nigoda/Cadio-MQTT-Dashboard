/* ==========================================
   Smart Irrigation — Automation UI
   ========================================== */
(function () {
  "use strict";
  const socket = io();
  const $ = (s) => document.querySelector(s);
  const escHtml = (s) => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };

  // State
  let _autos = {};       // id -> automation
  let _selectedId = null;
  let _editId = null;    // null = create, string = edit
  const DAY_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];

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
    const map = { IDLE:"Off", WAIT_CONDITION:"Waiting", INIT_SET:"Initializing", INIT_VERIFY:"Verifying Init",
      ACTION_SET:"Setting Action", ACTION_VERIFY:"Verifying", ACTION_RUN:"Running",
      OVERLAP_NEXT_SET:"Setting Next", OVERLAP_NEXT_VERIFY:"Verifying Next",
      ACTION_REVERT:"Reverting", ACTION_VERIFY_REVERT:"Verifying Revert", BUFFER:"Buffer",
      PAUSED_CONDITION:"Paused (Condition)", PAUSED_SCHEDULE:"Paused (Schedule)",
      COMPLETED:"Completed", ERROR_SET:"Error Recovery", ERROR_VERIFY:"Error Verify", ERROR:"Error" };
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
      return `<option value="${escHtml(e.cmdTopic)}" data-state="${escHtml(e.stateTopic||"")}" data-name="${escHtml(dName)}" ${e.cmdTopic===selectedCmd?"selected":""}>${escHtml(dName)}</option>`;
    }).join("");
  }

  function sensorOptions(selectedTopic) {
    return getSensorEntities().map(e => {
      const devName = window._dashboardDevices && window._dashboardDevices[e.deviceSerial] ? window._dashboardDevices[e.deviceSerial].name : "Unknown";
      const dName = `${e.name} (${devName})`;
      return `<option value="${escHtml(e.stateTopic)}" data-name="${escHtml(dName)}" ${e.stateTopic===selectedTopic?"selected":""}>${escHtml(dName)}</option>`;
    }).join("");
  }

  // ─── Render List ───
  function renderList() {
    const autos = Object.values(_autos);
    if (autos.length === 0) {
      autoList.innerHTML = '<div class="ha-empty-row">No automations yet. Click "Add New" to create one.</div>';
      return;
    }
    autoList.innerHTML = autos.map(a => {
      const cls = stateClass(a);
      const sel = a.id === _selectedId ? " active" : "";
      return `<div class="irr-auto-item${sel}" data-id="${a.id}">
        <div class="irr-auto-item-info">
          <div class="irr-auto-item-name">${escHtml(a.name)}</div>
          <div class="irr-auto-item-status"><span class="irr-status-dot ${cls}"></span> ${stateLabel(a)}</div>
        </div>
        <label class="ha-toggle" style="pointer-events:auto"><input type="checkbox" ${a.status==="ON"?"checked":""} data-id="${a.id}"><span class="ha-toggle-track"></span><span class="ha-toggle-thumb"></span></label>
      </div>`;
    }).join("");

    autoList.querySelectorAll(".irr-auto-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest(".ha-toggle")) return;
        _selectedId = el.dataset.id;
        renderList();
        renderDetail();
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
    let progText = actions.length > 0 ? `Action ${Math.min(idx+1,actions.length)} of ${actions.length}` : "No actions";
    let nextStep = "—";
    let nextSub = "—";

    if (actions.length > 0) {
      let totalAutoSec = 0;
      for (let i = 0; i < actions.length; i++) {
          totalAutoSec += (actions[i].duration || 0);
      }
      
      let elapsedPreviousSec = 0;
      for (let i = 0; i < idx && i < actions.length; i++) {
          elapsedPreviousSec += (actions[i].duration || 0);
      }

      const curAction = idx < actions.length ? actions[idx] : actions[actions.length - 1];
      const dur = curAction.duration || 0;
      
      let elapsedCurSec = 0;
      let remainingCurSec = dur;

      if (rt.state === "ACTION_RUN") {
         const timerStart = rt.timerStart || (Date.now() / 1000);
         const elapsedSec = Math.max(0, (Date.now() / 1000) - timerStart);
         elapsedCurSec = Math.min(dur, elapsedSec);
         remainingCurSec = Math.max(0, dur - elapsedCurSec);
         
         curSub = `${curAction.switchName||'Switch'} ${curAction.state} for ${formatTime(elapsedCurSec)}`;
         
         if (idx + 1 < actions.length) {
             const nextAction = actions[idx+1];
             nextStep = `${nextAction.switchName||'Switch'} ${nextAction.state}`;
         } else {
             const revertState = curAction.state === "ON" ? "OFF" : "ON";
             nextStep = `${curAction.switchName||'Switch'} ${revertState}`;
         }
         nextSub = `In ${formatTime(remainingCurSec)}`;
         
      } else if (rt.state === "BUFFER") {
         elapsedCurSec = dur;
         const bufTime = auto.bufferTime || 5;
         const bufStart = rt.bufferStart || (Date.now() / 1000);
         const bufElapsed = Math.max(0, Math.min(bufTime, (Date.now() / 1000) - bufStart));
         
         curSub = `Waiting for buffer`;
         nextSub = `In ${formatTime(Math.max(0, bufTime - bufElapsed))}`;
         nextStep = `Revert ${curAction.switchName||'Switch'}`;
         
      } else if (rt.state === "IDLE" || rt.state === "COMPLETED") {
         curSub = rt.state === "COMPLETED" ? "Finished cycle" : "Waiting for start";
         if (rt.state === "COMPLETED") elapsedPreviousSec = totalAutoSec;
      } else {
         if (rt.state.includes("OVERLAP") || rt.state.includes("REVERT")) elapsedCurSec = dur;
         curSub = `Action ${Math.min(idx+1, actions.length)}: ${curAction.switchName||'Switch'} ${curAction.state}`;
      }
      
      const totalElapsedSec = elapsedPreviousSec + elapsedCurSec;
      pct = totalAutoSec > 0 ? Math.min(100, Math.round((totalElapsedSec / totalAutoSec) * 100)) : 0;
      progText = `${formatTime(totalElapsedSec)} / ${formatTime(totalAutoSec)}`;
      if (rt.state === "COMPLETED") pct = 100;
    }
    
    $("#irr-cur-state-sub").textContent = curSub;
    $("#irr-progress-pct").textContent = pct + "%";
    $("#irr-progress-bar").style.width = pct + "%";
    $("#irr-progress-text").textContent = progText;
    $("#irr-next-step").textContent = nextStep;
    $("#irr-next-step-sub").textContent = nextSub;
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

    // Live Switches
    const liveSw = $("#irr-live-switches");
    if (liveSw) {
        const switchMap = new Map();
        [...inits, ...actions, ...errs].forEach(s => {
           if (s.switchName) switchMap.set(s.switchName, s.switchStateTopic || s.switchCmdTopic);
        });
        if (switchMap.size > 0) {
          liveSw.innerHTML = `<div style="display:flex;gap:12px;flex-wrap:wrap;">${Array.from(switchMap.entries()).map(([name, topic]) => {
            let liveState = "Unknown";
            if (window._dashboardEntities) {
                for (const eid in window._dashboardEntities) {
                    const e = window._dashboardEntities[eid];
                    if (e.stateTopic === topic || e.cmdTopic === topic) { liveState = (e.state||"Unknown").toUpperCase(); break; }
                }
            }
            const isOn = liveState === "ON";
            return `<div class="irr-live-sw">
              <span class="material-symbols-outlined irr-live-sw-icon ${isOn?'on':''}">${isOn?'water_drop':'water_drop'}</span>
              <span class="irr-live-sw-name">${escHtml(name)}</span>
              <span class="irr-live-sw-state" style="color:${isOn?'#4caf50':'#f44336'}">${liveState}</span>
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
                    if (e.stateTopic === topic || e.cmdTopic === topic) { liveState = (e.state||"Unknown").toUpperCase(); break; }
                }
            }
            const isOn = liveState === "ON";
            return `<div class="irr-live-sw">
              <span class="material-symbols-outlined irr-live-sw-icon ${isOn?'on':''}">sensors</span>
              <span class="irr-live-sw-name">${escHtml(name)}</span>
              <span class="irr-live-sw-state" style="color:${isOn?'#4caf50':(liveState==='OFF'?'#f44336':'var(--ha-text-secondary)')}">${liveState}</span>
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
         if(conds[i]) {
            const topic = conds[i].sensorStateTopic || "";
            let lState = "Unknown";
            if (window._dashboardEntities) {
                for (const eid in window._dashboardEntities) {
                    const e = window._dashboardEntities[eid];
                    if (e.stateTopic === topic || e.cmdTopic === topic) { lState = (e.state||"Unknown").toUpperCase(); break; }
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
    initBody.innerHTML = inits.map(i => `<div class="irr-sw-row"><span>${escHtml(i.switchName||i.switchCmdTopic||"Switch")}</span><span class="irr-sw-state ${i.state==='ON'?'on':'off'}">${i.state}</span></div>`).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">None configured</span>';

    // Condition
    const condBody = $("#irr-cond-body");
    const conds = auto.condition || [];
    condBody.innerHTML = conds.map((c,i) => {
      const logicBadge = c.logic && i < conds.length-1 ? `<span class="irr-cond-logic">${c.logic}</span>` : "";
      return `<div class="irr-cond-row"><span class="irr-cond-sensor">${escHtml(c.sensorName||c.sensorStateTopic||"Sensor")}</span><span class="irr-cond-op">=</span><span class="irr-cond-val">${escHtml(c.value||"")}</span><span class="irr-cond-live"></span>${logicBadge}</div>`;
    }).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">No conditions (always true)</span>';

    // Actions
    const actBody = $("#irr-actions-body");
    actBody.innerHTML = actions.length > 0 ? `<table class="irr-actions-table"><thead><tr><th>#</th><th>Switch</th><th>State</th><th>Duration</th><th>Status</th></tr></thead><tbody>${actions.map((a,i) => {
      const isActive = i === idx && (rt.state||"").startsWith("ACTION");
      const durStr = a.duration > 0 ? (a.duration >= 60 ? Math.round(a.duration/60)+" min" : a.duration+"s") : "—";
      let status = "⏳ Pending";
      if (i < idx) status = "✔ Done";
      if (isActive) status = "▶ " + (rt.state==="ACTION_RUN"?"Running":"Processing");
      return `<tr class="${isActive?"active-action":""}"><td>${i+1}</td><td>${escHtml(a.switchName||"Switch")}</td><td><span class="irr-sw-state ${a.state==='ON'?'on':'off'}">${a.state}</span></td><td>${durStr}</td><td class="irr-action-status">${status}</td></tr>`;
    }).join("")}</tbody></table>` : '<span style="color:var(--ha-text-disabled);font-size:12px">No actions configured</span>';

    // Error state
    const errBody = $("#irr-error-body");
    const errs = auto.errorState || [];
    errBody.innerHTML = errs.map(e => `<div class="irr-sw-row"><span>${escHtml(e.switchName||"Switch")}</span><span class="irr-sw-state off">${e.state||"OFF"}</span></div>`).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">None configured</span>';

    // Scheduler
    const schedBody = $("#irr-sched-body");
    const sched = auto.schedule || {};
    const days = sched.days || [];
    const is24Hr = !sched.startTime && !sched.endTime;
    schedBody.innerHTML = `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
      <div><span class="irr-label">Active Days</span><div class="irr-day-chips" style="margin-top:6px">${DAY_NAMES.map(d => `<span class="irr-day-chip ${days.includes(d)?'active':''}">${d}</span>`).join("")}</div></div>
      <div><span class="irr-label">Time Range</span><div class="irr-time-display" style="margin-top:6px">${is24Hr ? "24-Hour Active" : `${sched.startTime||"—"} → ${sched.endTime||"—"}`}</div></div>
    </div>`;

    updateLivePanels();

    // Activity log
    const logEl = $("#irr-activity-log");
    const logs = auto.logs || [];
    logEl.innerHTML = logs.length > 0 ? `<div class="irr-log-list">${logs.slice(0,15).map(l => {
      const t = l.ts ? new Date(l.ts).toLocaleTimeString() : "";
      return `<div class="irr-log-entry"><span class="irr-log-dot ${l.level||'info'}"></span><span class="irr-log-time">${escHtml(t)}</span><span>${escHtml(l.msg||"")}</span></div>`;
    }).join("")}</div>` : '<span style="color:var(--ha-text-disabled);font-size:12px">No activity yet</span>';

    // Button handlers
    $("#irr-btn-reset").onclick = () => socket.emit("reset_automation", { id: auto.id });
    $("#irr-btn-delete").onclick = () => { if(confirm("Delete "+auto.name+"?")) socket.emit("delete_automation",{id:auto.id}); };
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
    daysEl.innerHTML = DAY_NAMES.map(d => `<button type="button" class="irr-day-btn ${selDays.has(d)?'active':''}" data-day="${d}">${d}</button>`).join("");
    daysEl.querySelectorAll(".irr-day-btn").forEach(b => b.addEventListener("click", () => b.classList.toggle("active")));

    const sStart = auto?.schedule?.startTime || "";
    const sEnd = auto?.schedule?.endTime || "";
    $("#auto-f-start").value = sStart;
    $("#auto-f-end").value = sEnd;
    
    const cb24 = $("#auto-f-24hr");
    if (cb24) {
        cb24.checked = (!sStart && !sEnd);
        cb24.onchange = (e) => {
            if(e.target.checked) {
                $("#auto-f-start").value = "";
                $("#auto-f-end").value = "";
                $("#auto-f-start").disabled = true;
                $("#auto-f-end").disabled = true;
            } else {
                $("#auto-f-start").disabled = false;
                $("#auto-f-end").disabled = false;
            }
        };
        // trigger initial state
        cb24.onchange({target:cb24});
    }
    
    // Auto-detect browser offset if no offset is configured yet
    const tzSelect = $("#auto-f-tz");
    if (tzSelect) {
        tzSelect.value = auto?.schedule?.utcOffset ?? new Date().getTimezoneOffset();
        // Fallback to first if somehow the browser offset isn't in the list
        if (!tzSelect.value) tzSelect.selectedIndex = 0;
    }

    $("#auto-f-buffer").value = auto?.bufferTime ?? 5;

    // Init rows
    renderFormRows("auto-f-init", auto?.initialization || [], "switch");
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
      row.innerHTML = `<select class="f-switch">${switchOptions(data?.switchCmdTopic||"")}</select>
        <select class="f-state"><option value="ON" ${data?.state==="ON"?"selected":""}>ON</option><option value="OFF" ${data?.state!=="ON"?"selected":""}>OFF</option></select>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    } else if (type === "condition") {
      row.innerHTML = `<select class="f-sensor">${sensorOptions(data?.sensorStateTopic||"")}</select>
        <span class="irr-cond-op">=</span>
        <select class="f-state"><option value="ON" ${data?.value==="ON"?"selected":""}>ON</option><option value="OFF" ${data?.value!=="ON"?"selected":""}>OFF</option><option value="HIGH" ${data?.value==="HIGH"?"selected":""}>HIGH</option><option value="LOW" ${data?.value==="LOW"?"selected":""}>LOW</option></select>
        <select class="f-logic"><option value="AND" ${data?.logic!=="OR"?"selected":""}>AND</option><option value="OR" ${data?.logic==="OR"?"selected":""}>OR</option></select>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    } else if (type === "action") {
      const dur = data?.duration || 0;
      row.innerHTML = `<select class="f-switch">${switchOptions(data?.switchCmdTopic||"")}</select>
        <select class="f-state"><option value="ON" ${data?.state==="ON"?"selected":""}>ON</option><option value="OFF" ${data?.state!=="ON"?"selected":""}>OFF</option></select>
        <input type="number" class="f-duration" value="${dur}" min="0" placeholder="sec">
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    }
    row.querySelector(".irr-remove-btn")?.addEventListener("click", () => row.remove());
    container.appendChild(row);
  }

  // Add row buttons
  ["auto-f-init-add","auto-f-error-add"].forEach(id => {
    $(` #${id}`)?.addEventListener("click", () => addFormRow($(`#${id.replace("-add","")}`), "switch", {}));
  });
  $("#auto-f-cond-add")?.addEventListener("click", () => addFormRow($("#auto-f-cond"), "condition", {}));
  $("#auto-f-actions-add")?.addEventListener("click", () => addFormRow($("#auto-f-actions"), "action", {}));

  function collectFormData() {
    const name = $("#auto-f-name").value.trim();
    if (!name) { alert("Name is required"); return null; }
    const days = [...$("#auto-f-days").querySelectorAll(".irr-day-btn.active")].map(b => b.dataset.day);

    const collectSwitchRows = (containerId) => [...$(`#${containerId}`).querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-switch");
      const opt = sel?.selectedOptions[0];
      return { switchCmdTopic: sel?.value||"", switchStateTopic: opt?.dataset.state||"", switchName: opt?.dataset.name||"", state: r.querySelector(".f-state")?.value||"OFF" };
    });

    const conds = [...$("#auto-f-cond").querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-sensor");
      const opt = sel?.selectedOptions[0];
      return { sensorStateTopic: sel?.value||"", sensorName: opt?.dataset.name||"", value: r.querySelector(".f-state")?.value||"OFF", logic: r.querySelector(".f-logic")?.value||"AND" };
    });

    const actions = [...$("#auto-f-actions").querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-switch");
      const opt = sel?.selectedOptions[0];
      return { switchCmdTopic: sel?.value||"", switchStateTopic: opt?.dataset.state||"", switchName: opt?.dataset.name||"", state: r.querySelector(".f-state")?.value||"ON", duration: parseInt(r.querySelector(".f-duration")?.value||"0",10) };
    });

    return {
      name, description: $("#auto-f-desc").value.trim(),
      schedule: { 
          days, 
          startTime: $("#auto-f-start").value, 
          endTime: $("#auto-f-end").value,
          utcOffset: parseInt($("#auto-f-tz").value, 10) || 0
      },
      condition: conds,
      initialization: collectSwitchRows("auto-f-init"),
      actions,
      errorState: collectSwitchRows("auto-f-error"),
      bufferTime: parseInt($("#auto-f-buffer")?.value || "5", 10),
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

  // Start live timer loop
  setInterval(updateLiveTimers, 1000);
})();
