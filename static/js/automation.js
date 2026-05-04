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
    return getSwitchEntities().map(e =>
      `<option value="${escHtml(e.cmdTopic)}" data-state="${escHtml(e.stateTopic||"")}" data-name="${escHtml(e.name)}" ${e.cmdTopic===selectedCmd?"selected":""}>${escHtml(e.name)}</option>`
    ).join("");
  }

  function sensorOptions(selectedTopic) {
    return getSensorEntities().map(e =>
      `<option value="${escHtml(e.stateTopic)}" data-name="${escHtml(e.name)}" ${e.stateTopic===selectedTopic?"selected":""}>${escHtml(e.name)}</option>`
    ).join("");
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
    const progress = actions.length > 0 ? Math.round(((idx) / actions.length) * 100) : 0;

    // Header
    $("#irr-detail-name").textContent = auto.name;
    $("#irr-detail-desc").textContent = auto.description || "";
    const badge = $("#irr-detail-badge");
    badge.textContent = stateLabel(auto);
    badge.className = "irr-status-badge " + cls;

    // State bar
    $("#irr-cur-state").textContent = stateLabel(auto).toUpperCase();
    
    let curSub = "—";
    let pct = 0;
    let progText = actions.length > 0 ? `Action ${Math.min(idx+1,actions.length)} of ${actions.length}` : "No actions";
    let nextStep = "—";
    let nextSub = "—";

    if (idx < actions.length) {
      const curAction = actions[idx];
      const dur = curAction.duration || 0;
      
      if (rt.state === "ACTION_RUN") {
         const totalMin = Math.round(dur / 60);
         curSub = `${curAction.switchName||'Switch'} ${curAction.state} for ${totalMin} min`;
         
         const timerStart = rt.timerStart || Date.now();
         const elapsedSec = Math.max(0, (Date.now() - timerStart) / 1000);
         const elapsedMin = Math.floor(elapsedSec / 60);
         
         pct = dur > 0 ? Math.min(100, Math.round((elapsedSec / dur) * 100)) : 100;
         progText = `${elapsedMin} min / ${totalMin} min`;
         
         if (idx + 1 < actions.length) {
             const nextAction = actions[idx+1];
             nextStep = `${nextAction.switchName||'Switch'} ${nextAction.state}`;
             nextSub = `After ${totalMin} min`;
         } else {
             const revertState = curAction.state === "ON" ? "OFF" : "ON";
             nextStep = `${curAction.switchName||'Switch'} ${revertState}`;
             nextSub = `After ${totalMin} min`;
         }
      } else {
         pct = actions.length > 0 ? Math.round((idx / actions.length) * 100) : 0;
         if (rt.state === "IDLE" || rt.state === "COMPLETED") {
             pct = rt.state === "COMPLETED" ? 100 : 0;
             curSub = rt.state === "COMPLETED" ? "Finished cycle" : "Waiting for start";
         } else if (rt.state === "BUFFER") {
             curSub = "Waiting for buffer";
         } else {
             curSub = `Action ${idx+1}: ${curAction.switchName||'Switch'} ${curAction.state}`;
         }
      }
    }
    
    $("#irr-cur-state-sub").textContent = curSub;
    $("#irr-progress-pct").textContent = pct + "%";
    $("#irr-progress-bar").style.width = pct + "%";
    $("#irr-progress-text").textContent = progText;
    $("#irr-next-step").textContent = nextStep;
    $("#irr-next-step-sub").textContent = nextSub;

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
      return `<div class="irr-cond-row"><span class="irr-cond-sensor">${escHtml(c.sensorName||c.sensorStateTopic||"Sensor")}</span><span class="irr-cond-op">=</span><span class="irr-cond-val">${escHtml(c.value||"")}</span>${logicBadge}</div>`;
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
    schedBody.innerHTML = `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
      <div><span class="irr-label">Active Days</span><div class="irr-day-chips" style="margin-top:6px">${DAY_NAMES.map(d => `<span class="irr-day-chip ${days.includes(d)?'active':''}">${d}</span>`).join("")}</div></div>
      <div><span class="irr-label">Time Range</span><div class="irr-time-display" style="margin-top:6px">${sched.startTime||"—"} → ${sched.endTime||"—"}</div></div>
    </div>`;

    // Live switches — show actual ON/OFF from MQTT entities
    const liveSw = $("#irr-live-switches");
    const switchMap = new Map(); // name -> stateTopic
    [...inits, ...actions, ...errs].forEach(s => {
      if (s.switchName && s.switchStateTopic) switchMap.set(s.switchName, s.switchStateTopic);
    });
    if (switchMap.size > 0) {
      liveSw.innerHTML = `<div class="irr-live-grid">${[...switchMap.entries()].map(([name, stateTopic]) => {
        // Read actual state from dashboard entities
        let liveState = "?";
        if (window._dashboardEntities) {
          for (const eid in window._dashboardEntities) {
            const e = window._dashboardEntities[eid];
            if (e.stateTopic === stateTopic) {
              const st = e.state;
              if (typeof st === "object") liveState = (st?.state || "?").toUpperCase();
              else liveState = (st || "?").toString().toUpperCase();
              break;
            }
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

    $("#auto-f-start").value = auto?.schedule?.startTime || "";
    $("#auto-f-end").value = auto?.schedule?.endTime || "";
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
      schedule: { days, startTime: $("#auto-f-start").value, endTime: $("#auto-f-end").value },
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
})();
