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
  let _activeAnalyticsId = null; // Tracks which automation analytics is currently open
  let _searchQuery = "";  // Current automation name filter (lowercased)
  let _statusFilter = new Set(["all"]);  // Active status/state filters (multi-select, OR)
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  // DOM
  const autoList = $("#auto-list");
  const detailContent = $("#irr-detail-content");
  const detailEmpty = $(".irr-detail-empty");
  const btnAdd = $("#btn-add-automation");
  const modalOverlay = $("#auto-modal-overlay");
  const searchInput = $("#auto-search");
  const filterDD = $("#auto-filter-dd");
  const filterDDTrigger = $("#auto-filter-dd-trigger");
  const filterDDMenu = $("#auto-filter-dd-menu");
  const filterDDLabel = $("#auto-filter-dd-label");
  const filterClearBtn = $("#auto-filter-clear");
  const clearFiltersBtn = $("#auto-clear-filters");

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
    if (s !== "ON") {
      // OFF but waiting in the deinit queue
      if (auto.runtime?._deinit_waiting) return "deinit-waiting";
      // OFF but actively deiniting
      if (rs.startsWith("DEINIT_")) return "running";
      return "off";
    }
    if (rs === "ERROR") return "error";
    if (rs === "PAUSED_NETWORK") return "error";
    if (rs.startsWith("PAUSED")) return "paused";
    if (rs === "COMPLETED") return "completed";
    if (rs === "WAIT_CONDITION") return "waiting";
    if (rs === "IDLE") return "off";
    return "running";
  }

  // True if an automation belongs to a single named filter category.
  function autoInCategory(auto, filter) {
    if (!filter || filter === "all") return true;
    const isOn = auto?.status === "ON";
    const rs = auto?.runtime?.state || "IDLE";
    switch (filter) {
      case "on": return isOn;
      case "off": return !isOn;
      case "running": return isOn && stateClass(auto) === "running";
      case "idle": return isOn && ["IDLE", "WAIT_CONDITION", "COMPLETED"].includes(rs);
      case "paused": return isOn && rs.startsWith("PAUSED");
      case "scheduler": return (!auto.actions || auto.actions.length === 0);
      case "error": return isOn && (rs.startsWith("ERROR") || rs === "PAUSED_NETWORK" || auto.runtime?.isNetworkRetry);
      default: return true;
    }
  }

  // Decide whether an automation matches the active filter set (OR across filters).
  // An empty set or a set containing "all" means: show everything.
  function matchesStatusFilter(auto, filters) {
    if (!filters || filters.size === 0 || filters.has("all")) return true;
    for (const f of filters) {
      if (autoInCategory(auto, f)) return true;
    }
    return false;
  }

  function stateLabel(auto) {
    if (!auto) return "Off";
    const rs = auto.runtime?.state || "IDLE";
    const waiting = auto.runtime?._deinit_waiting;

    // DEINIT WAITING — queued behind an earlier automation
    if (waiting) return "Deinit Waiting";

    // Status OFF means it's off, unless it's running a DEINIT or in a network pause for DEINIT
    if (auto.status !== "ON" && !rs.startsWith("DEINIT_") && rs !== "PAUSED_NETWORK") return "Off";

    // If the backend is actively retrying a failed network command, keep showing the error state to avoid flicker
    if (auto.runtime?.isNetworkRetry) return "Network Error Paused";

    const map = {
      IDLE: "Off", WAIT_CONDITION: "Waiting", INIT_SET: "Initializing", INIT_VERIFY: "Verifying Init",
      INIT_VERIFY_INDIVIDUAL: "Verify Init", INIT_VERIFY_ALL: "Verify Init All",
      ACTION_SET: "Setting Action", ACTION_VERIFY: "Verifying", ACTION_RUN: "Running",
      ACTION_DRIFT_VERIFY: "Drift Check", BUFFER_DRIFT_VERIFY: "Buffer Check",
      OVERLAP_NEXT_SET: auto.runtime?.loopingToFirst ? "Init & Setting Next" : "Setting Next",
      OVERLAP_NEXT_VERIFY: auto.runtime?.loopingToFirst ? "Verify Init & Next" : "Verifying Next",
      ACTION_REVERT: "Reverting", ACTION_VERIFY_REVERT: "Verifying Revert", BUFFER: "Buffer",
      PAUSED_CONDITION: "Paused (Condition)", PAUSED_SCHEDULE: "Paused (Schedule)", PAUSED_USER: "Paused (User)", PAUSED_ENFORCE: "Pausing for Schedule",
      PAUSED_NETWORK: "Network Error Paused", SCHEDULER_PING_VERIFY: "Ping Check",
      DEINIT_SET: "Deinitializing", DEINIT_VERIFY_INDIVIDUAL: "Verify Deinit",
      COMPLETED: "Completed", ERROR_SET: "Error Recovery", ERROR_VERIFY: "Error Verify", ERROR: "Error"
    };
    if (rs === "PAUSED_NETWORK") {
      return "Network Error Paused";
    }
    if (rs === "ACTION_PING_VERIFY" || rs === "SCHEDULER_PING_VERIFY") {
      const pre = auto.runtime?.prePingState;
      if (pre === "PAUSED_NETWORK") return "Network Error Paused";
      return map[pre] || (pre || "Running");
    }
    if (rs.startsWith("ERROR") && auto.runtime?.errorReason === "missing") {
      return "Error (Device Not Found)";
    }
    return map[rs] || rs;
  }

  function getSwitchEntities() {
    const ents = [];
    if (window._dashboardEntities) {
      for (const eid in window._dashboardEntities) {
        const e = window._dashboardEntities[eid];
        if (e.type === "switch" && e.cmdTopic) {
           const wd = (window.watchdogs || {})[e.deviceSerial];
           if (wd && (e.cmdTopic === wd || e.stateTopic === wd)) {
             continue; // Skip watchdog switches
           }
           ents.push(e);
        }
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
      return `<option value="${escHtml(e.stateTopic)}" data-name="${escHtml(dName)}" data-type="${escHtml(e.type || "")}" ${e.stateTopic === selectedTopic ? "selected" : ""}>${escHtml(dName)}</option>`;
    }).join("");
  }

  // Numeric comparison operators offered for non-binary (analog) sensors.
  const COND_NUMERIC_OPS = ["==", "!=", ">", ">=", "<", "<="];
  // Equality operators offered for binary sensors.
  const COND_BINARY_OPS = ["==", "!="];

  // Resolve whether a sensor state topic belongs to a binary_sensor or an analog sensor.
  function sensorTypeForTopic(topic) {
    const e = getSensorEntities().find(x => x.stateTopic === topic);
    return e ? (e.type || "binary_sensor") : "binary_sensor";
  }

  // Build the operator + value portion of a condition row based on the sensor type.
  // Binary sensors → ==/!= operator with an ON/OFF selector.
  // Analog sensors → operator dropdown (==, !=, >, >=, <, <=) with a manual numeric input.
  function condValueHtml(sensorType, data) {
    if (sensorType === "sensor") {
      const selOp = COND_NUMERIC_OPS.includes(data?.op) ? data.op : "==";
      const opOptions = COND_NUMERIC_OPS.map(o => `<option value="${o}" ${o === selOp ? "selected" : ""}>${o}</option>`).join("");
      const val = (data?.value === undefined || data?.value === null) ? "" : data.value;
      return `<select class="f-op">${opOptions}</select>
        <input type="number" class="f-val" step="any" value="${escHtml(String(val))}" placeholder="Value" style="width:90px;">`;
    }
    const selBinOp = COND_BINARY_OPS.includes(data?.op) ? data.op : "==";
    const binOpOptions = COND_BINARY_OPS.map(o => `<option value="${o}" ${o === selBinOp ? "selected" : ""}>${o}</option>`).join("");
    return `<select class="f-op">${binOpOptions}</select>
      <select class="f-state"><option value="ON" ${data?.value === "ON" ? "selected" : ""}>ON</option><option value="OFF" ${data?.value !== "ON" ? "selected" : ""}>OFF</option></select>`;
  }

  // Operator symbol shown in the read-only condition summary.
  function condOpDisplay(c) {
    const o = c?.op;
    return (!o || o === "==" || o === "=") ? "=" : o;
  }

  // ─── Render List ───
  const _lastProgress = {}; // Track high-water-mark per automation to prevent bar dips
  
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
      if (rt.remainingTime !== undefined && rt.remainingTime !== null) {
        const currentRemaining = Math.max(0, rt.remainingTime - elapsedSec);
        elapsedCurSec = Math.max(0, dur - currentRemaining);
      } else {
        elapsedCurSec = Math.min(dur, elapsedSec);
      }
    } else if (rt.state && (rt.state.startsWith("PAUSED_") || rt.state === "ACTION_PING_VERIFY" || rt.state === "SCHEDULER_PING_VERIFY" || rt.state === "ACTION_DRIFT_VERIFY")) {
      if (rt.remainingTime !== undefined && rt.remainingTime !== null) {
        elapsedCurSec = Math.max(0, dur - rt.remainingTime);
      }
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
    } else if (rt.state && (rt.state.includes("INIT") || rt.state.includes("VERIFY") || rt.state === "ACTION_SET")) {
      // During re-initialization between cycles (looping), hold bar at 100%
      // to prevent the visual dip when idx resets to 0.
      // On first startup (no loopingToFirst), keep at 0%.
      if (rt.loopingToFirst) {
        elapsedPreviousSec = totalAutoSec;
        elapsedCurSec = 0;
      }
    }

    const totalElapsedSec = elapsedPreviousSec + elapsedCurSec;
    let pct = totalAutoSec > 0 ? Math.min(100, Math.round((totalElapsedSec / totalAutoSec) * 100)) : 0;
    if (rt.state === "COMPLETED") pct = 100;
    
    // Enforce monotonic increase: never let bar go backwards during a running cycle
    const autoId = auto.id;
    // A new cycle begins at index 0 with loopingToFirst cleared. This happens both on
    // a fresh startup (state INIT_SET) and when looping back for another cycle, where the
    // backend jumps straight to ACTION_SET (skipping INIT_SET). Reset the high-water-mark
    // in both cases so the bar restarts from 0% instead of sticking at 100%.
    const isFreshCycleStart = (rt.state === "INIT_SET" || rt.state === "ACTION_SET" || (rt.state === "ACTION_RUN" && idx === 0)) && !rt.loopingToFirst;
    if (rt.state === "IDLE" || rt.state === "COMPLETED" || !rt.state || isFreshCycleStart) {
      // Reset high-water-mark when cycle ends, automation is idle, or fresh startup/reset
      delete _lastProgress[autoId];
    } else {
      const lastPct = _lastProgress[autoId] || 0;
      if (pct < lastPct) {
        pct = lastPct; // Hold at previous high
      }
      _lastProgress[autoId] = pct;
    }

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
    const filtered = autos.filter(a => {
      if (_searchQuery && !(a.name || "").toLowerCase().includes(_searchQuery)) return false;
      return matchesStatusFilter(a, _statusFilter);
    });
    if (filtered.length === 0) {
      autoList.innerHTML = `<div class="ha-empty-row">No automations match your filters.</div>`;
      return;
    }
    autoList.innerHTML = filtered.map(a => {
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
      inp.onchange = (e) => {
        e.stopPropagation();
        const a = _autos[inp.dataset.id];
        if (!a) return;
        const isTurningOn = inp.checked;
        if (!isTurningOn) {
          socket.emit("toggle_automation", { id: a.id, status: "OFF" });
          return;
        }

        inp.disabled = true; // disable while checking
        
        // Check if automation uses any switch that is currently a watchdog
        let conflictWatchdog = null;
        const allItems = [...(a.actions||[]), ...(a.initialization||[]), ...(a.deinitialization||[]), ...(a.schedule?.setIfTrue||[]), ...(a.schedule?.setIfFalse||[])];
        for (const i of allItems) {
            const topic = i.switchCmdTopic || i.switchStateTopic || "";
            if (!topic) continue;
            for (const [unit, wdTopic] of Object.entries(window.watchdogs || {})) {
                if (wdTopic && topic === wdTopic) {
                    conflictWatchdog = wdTopic;
                    break;
                }
            }
            if (conflictWatchdog) break;
        }
        
        if (conflictWatchdog) {
            inp.disabled = false;
            inp.checked = false; // revert visual toggle
            const msg = "Cannot turn ON: this automation uses a switch that is currently set as a Watchdog. Please change the watchdog switch first.";
            if (window.showToastNotification) window.showToastNotification("Watchdog Conflict", msg, "error");
            else alert(msg);
            return;
        }
        
        const conflictsMap = validateRunConflicts(a);
        inp.disabled = false;

        if (conflictsMap.size > 0) {
          inp.checked = false; // revert visual toggle
          const parts = [...conflictsMap.entries()].map(([swName, otherAutos]) => 
             `"${swName}" is actively used by ${[...otherAutos].join(", ")}`
          );
          if (window.showToastNotification) {
             window.showToastNotification("Hardware Conflict", `Cannot run: ${parts.join("; ")}`, "error");
          } else {
             alert(`Cannot run: ${parts.join("; ")}`);
          }
        } else {
          socket.emit("toggle_automation", { id: a.id, status: "ON" });
        }
      };
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
    $("#irr-cur-state").textContent = (rt.state === "PAUSED_NETWORK" || rt.isNetworkRetry)
      ? "NETWORK ERROR PAUSED"
      : (rt.state || "IDLE").replace(/_/g, " ");

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
      
      if (rt.state === "ACTION_RUN" || (rt.state && (rt.state.startsWith("PAUSED_") || rt.state === "ACTION_PING_VERIFY" || rt.state === "SCHEDULER_PING_VERIFY" || rt.state === "ACTION_DRIFT_VERIFY"))) {
        const timerStart = rt.timerStart || (Date.now() / 1000);
        const elapsedSecSinceResume = Math.max(0, (Date.now() / 1000) - timerStart);
        
        let remainingCurSec = Math.max(0, dur - elapsedSecSinceResume);
        let trueElapsedSec = elapsedSecSinceResume;
        
        if (rt.remainingTime !== undefined && rt.remainingTime !== null) {
          if (rt.state.startsWith("PAUSED_") || rt.state === "ACTION_PING_VERIFY" || rt.state === "SCHEDULER_PING_VERIFY" || rt.state === "ACTION_DRIFT_VERIFY") {
            remainingCurSec = rt.remainingTime;
          } else {
            remainingCurSec = Math.max(0, rt.remainingTime - elapsedSecSinceResume);
          }
          trueElapsedSec = Math.max(0, dur - remainingCurSec);
        }
        
        let actionStr = 'for';
        if (rt.state.startsWith("PAUSED_") || rt.state === "ACTION_PING_VERIFY" || rt.state === "SCHEDULER_PING_VERIFY") actionStr = 'Paused at';
        if (rt.state === "ACTION_DRIFT_VERIFY") actionStr = 'Verifying at';
        
        curSub = `${curAction.switchName || 'Switch'} ${curAction.state} ${actionStr} ${formatTime(trueElapsedSec)}`;

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

    // Shrink the state/next-step text as it gets longer so long labels
    // (e.g. "NETWORK ERROR PAUSED", "Initialization & Switch ON") stay inside the cell.
    fitStateText($("#irr-cur-state"));
    fitStateText($("#irr-next-step"));
    fitStateText($("#irr-cur-state-sub"), true);
    fitStateText($("#irr-next-step-sub"), true);

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
        liveSw.innerHTML = `<div class="irr-live-grid">${Array.from(switchMap.entries()).map(([name, topic]) => {
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
        liveSen.innerHTML = `<div class="irr-live-grid">${Array.from(sensorMap.entries()).map(([name, topic]) => {
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

    // Also update scheduler condition live state text if visible
    const schedBody = $("#irr-sched-body");
    if (schedBody) {
      const schedCondLive = schedBody.querySelectorAll(".irr-sched-cond-live");
      const schedConds = auto.schedule?.conditions || [];
      schedCondLive.forEach((span, i) => {
        if (schedConds[i]) {
          const topic = schedConds[i].sensorStateTopic || "";
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

    // Also update deinitialization live state text if visible
    const deinitBody = $("#irr-deinit-body");
    if (deinitBody) {
      const deinits = auto.deinitialization || [];
      const rows = deinitBody.querySelectorAll(".irr-deinit-live");
      rows.forEach((span, i) => {
        if (deinits[i]) {
          const topic = deinits[i].switchStateTopic || deinits[i].switchCmdTopic || "";
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
    const fullName = auto.name || "";
    const nameEl = $("#irr-detail-name");
    nameEl.textContent = fullName.length > 35 ? fullName.slice(0, 35).trimEnd() + "…" : fullName;
    nameEl.title = fullName;
    const btnPlayPause = $("#irr-btn-playpause");
    const iconPlayPause = $("#irr-icon-playpause");
    // Always keep the button's slot reserved (visibility, not display) so it
    // appearing when the automation is enabled never shifts the header layout.
    btnPlayPause.style.display = "flex";
    if (auto.status === "ON") {
      btnPlayPause.style.visibility = "visible";
      iconPlayPause.textContent = auto.isPaused ? "play_arrow" : "pause";
      btnPlayPause.style.background = auto.isPaused ? "var(--ha-warning)" : "var(--ha-primary)";
      btnPlayPause.onclick = () => {
        socket.emit("pause_automation", { id: auto.id, isPaused: !auto.isPaused });
      };
    } else {
      btnPlayPause.style.visibility = "hidden";
      btnPlayPause.onclick = null;
    }

    $("#irr-detail-desc").textContent = auto.description || "";
    setupDetailDesc(auto);
    const badge = $("#irr-detail-badge");
    badge.textContent = stateLabel(auto);
    badge.className = "irr-status-badge " + cls;

    // Run the live timer update logic
    updateLiveTimers();

    const toggle = $("#irr-status-toggle");
    toggle.checked = auto.status === "ON";
    $(".toggle-text-on").textContent = auto.status === "ON" ? "ON" : "OFF";
    // Clear previous errors/highlights
    const errEl = $("#irr-run-error");
    if (errEl) errEl.style.display = "none";
    document.querySelectorAll(".irr-sw-row.conflict").forEach(el => el.classList.remove("conflict"));

    const loadingEl = $("#irr-status-loading");
    if (loadingEl) {
      const rs = auto.runtime?.state || "";
      const isDeinit = rs.startsWith("DEINIT_") || (rs === "PAUSED_NETWORK" && auto.runtime?.prePauseNetwork?.startsWith("DEINIT_"));
      loadingEl.style.display = isDeinit ? "block" : "none";
    }

    toggle.onchange = (e) => {
      const isTurningOn = toggle.checked;
      if (!isTurningOn) {
        // Turning OFF is always safe
        if (auto.deinitialization && auto.deinitialization.length > 0 && loadingEl) {
          loadingEl.style.display = "block";
        }
        socket.emit("toggle_automation", { id: auto.id, status: "OFF" });
        return;
      }

      // Turning ON - run validation
      if (loadingEl) loadingEl.style.display = "block";
      toggle.disabled = true;

      const conflictsMap = validateRunConflicts(auto);
      if (loadingEl) loadingEl.style.display = "none";
      toggle.disabled = false;

      if (conflictsMap.size > 0) {
        toggle.checked = false; // revert toggle visually
        const switchTopics = conflictsMap.topics;
        
        const parts = [...conflictsMap.entries()].map(([swName, otherAutos]) => 
            `“${escHtml(swName)}” is actively used by ${escHtml([...otherAutos].join(", "))}`
        );
        if (errEl) {
          errEl.style.display = "flex";
          errEl.innerHTML = `<span class="material-symbols-outlined">error</span><span>Cannot run: ${parts.join("; ")}</span>`;
        }
        
        // Apply red borders to the rows
        switchTopics.forEach(t => {
            document.querySelectorAll(`.irr-sw-row[data-topic="${t}"], tr[data-topic="${t}"]`).forEach(el => el.classList.add("conflict"));
        });
        
      } else {
        if (errEl) errEl.style.display = "none";
        socket.emit("toggle_automation", { id: auto.id, status: "ON" });
      }
    };
    toggle.onclick = null;

    // Init
    const initBody = $("#irr-init-body");
    const inits = auto.initialization || [];
    initBody.innerHTML = inits.map(i => `<div class="irr-sw-row" data-topic="${escHtml(i.switchCmdTopic || "")}"><span>${escHtml(i.switchName || i.switchCmdTopic || "Switch")} <span class="irr-init-live" style="margin-left:12px; font-size:12px; color:var(--ha-text-secondary);"></span></span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">None configured</span>';

    // Deinit
    const deinitBody = $("#irr-deinit-body");
    if (deinitBody) {
      const deinits = auto.deinitialization || [];
      deinitBody.innerHTML = deinits.map(i => {
        const topic = i.switchCmdTopic || "";
        const isDeinitPhase = (rt.state || "").startsWith("DEINIT_");
        const isYielded = isDeinitPhase && (auto.runtime?.yielded_switches || []).includes(topic);
        const yieldIcon = isYielded ? `<span class="material-symbols-outlined" style="font-size:14px;color:var(--ha-yellow);margin-left:4px;vertical-align:middle;" title="Yielding priority to another active sequence/schedule">warning</span>` : "";
        return `<div class="irr-sw-row" data-topic="${escHtml(topic)}"><span><span style="display:inline-flex;align-items:center;">${escHtml(i.switchName || topic || "Switch")}${yieldIcon}</span> <span class="irr-deinit-live" style="margin-left:12px; font-size:12px; color:var(--ha-text-secondary);"></span></span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`;
      }).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">None configured</span>';
    }

    // Condition
    const condBody = $("#irr-cond-body");
    const conds = auto.condition || [];
    condBody.innerHTML = conds.map((c, i) => {
      const logicBadge = c.logic && i < conds.length - 1 ? `<span class="irr-cond-logic">${c.logic}</span>` : "";
      return `<div class="irr-cond-row"><span class="irr-cond-sensor">${escHtml(c.sensorName || c.sensorStateTopic || "Sensor")}</span><span class="irr-cond-op">${escHtml(condOpDisplay(c))}</span><span class="irr-cond-val">${escHtml(c.value || "")}</span><span class="irr-cond-live"></span>${logicBadge}</div>`;
    }).join("") || '<span style="color:var(--ha-text-disabled);font-size:12px">No conditions (always true)</span>';

    // Actions
    const maxCycles = auto.maxCyclesPerDay || 0;
    const cyclesToday = rt.cycles_today || 0;
    const cyclesBadge = maxCycles > 0 ? `<span style="margin-left:8px;font-size:11px;padding:2px 8px;border-radius:10px;background:var(--ha-surface-alt, #1e293b);color:var(--ha-primary, #03a9f4);">🔄 ${cyclesToday}/${maxCycles} cycles today</span>` : '';
    const actionsHeading = $("#main-actions-heading");
    if (actionsHeading) actionsHeading.innerHTML = `▶️ Actions (Sequential)${cyclesBadge}`;
    const actBody = $("#irr-actions-body");
    actBody.innerHTML = actions.length > 0 ? `<table class="irr-actions-table"><thead><tr><th>#</th><th>Switch</th><th>State</th><th>Duration</th><th>Status</th></tr></thead><tbody>${actions.map((a, i) => {
      const isActive = i === idx && (rt.state || "").startsWith("ACTION");
      const durStr = a.duration > 0 ? (a.duration >= 60 ? Math.round(a.duration / 60) + " min" : a.duration + "s") : "—";
      let status = "⏳ Pending";
      if (i < idx) status = "✔ Done";
      if (isActive) status = "▶ " + (rt.state === "ACTION_RUN" ? "Running" : "Processing");
      const topic = a.switchCmdTopic || "";
      const isActionPhase = (rt.state || "").startsWith("ACTION_") || (rt.state || "").startsWith("OVERLAP_");
      const isYielded = isActionPhase && (auto.runtime?.yielded_switches || []).includes(topic);
      const yieldIcon = isYielded ? `<span class="material-symbols-outlined" style="font-size:14px;color:var(--ha-yellow);margin-left:4px;vertical-align:middle;" title="Yielding priority to another active sequence/schedule">warning</span>` : "";

      return `<tr class="${isActive ? "active-action" : ""}" data-topic="${escHtml(topic)}"><td>${i + 1}</td><td><span style="display:inline-flex;align-items:center;">${escHtml(a.switchName || "Switch")}${yieldIcon}</span></td><td><span class="irr-sw-state ${a.state === 'ON' ? 'on' : 'off'}">${a.state}</span></td><td>${durStr}</td><td class="irr-action-status">${status}</td></tr>`;
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
    if (schedHeading) schedHeading.textContent = aiEnabled ? "🤖 AI Scheduler" : "📅 Scheduler";

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

    // Build scheduler conditions display
    const schedConds = sched.conditions || [];
    const schedCondHTML = schedConds.length > 0 ? `<div style="margin-top:12px;"><span class="irr-label">Conditions</span><div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;">${schedConds.map((c, i) => {
      const logicBadge = c.logic && i < schedConds.length - 1 ? `<span class="irr-cond-logic">${c.logic}</span>` : "";
      return `<div class="irr-cond-row" style="margin:0;"><span class="irr-cond-sensor">${escHtml(c.sensorName || c.sensorStateTopic || "Sensor")}</span><span class="irr-cond-op">${escHtml(condOpDisplay(c))}</span><span class="irr-cond-val">${escHtml(c.value || "")}</span><span class="irr-sched-cond-live"></span>${logicBadge}</div>`;
    }).join("")}</div></div>` : '';

    schedBody.innerHTML = `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;width:100%;">
      <div><span class="irr-label">Active Days</span><div class="irr-day-chips" style="margin-top:6px; ${aiEnabled ? 'pointer-events:none; border: 1px dashed var(--ha-primary); padding: 4px; border-radius: 8px;' : ''}">${DAY_NAMES.map(d => `<span class="irr-day-chip ${days.includes(d) ? 'active' : ''}">${d}</span>`).join("")}</div></div>
      <div><span class="irr-label">Time Range</span><div class="irr-time-display" style="margin-top:6px">${timeStr}</div></div>
      <div style="margin-left: auto; display: flex; align-items: center; gap: 8px;">
        <span style="font-size: 14px; font-weight: bold; color: var(--ha-primary, #03a9f4); letter-spacing: 0.5px; display: flex; align-items: center;">
          🤖 AI
          ${auto.ai_running ? '<span class="irr-ai-calculating" style="font-size: 11px; font-weight: normal; color: var(--ha-yellow); margin-left: 6px;">(Calculating...)</span>' : ''}
        </span>
        <label class="ha-toggle irr-custom-toggle" style="cursor: ${auto.ai_running ? 'not-allowed' : 'pointer'}; margin: 0;" title="${auto.ai_running ? 'AI is currently busy calculating...' : 'Enable AI Dynamic Scheduling'}">
            <input type="checkbox" id="irr-main-ai-toggle" ${aiEnabled ? 'checked' : ''} ${auto.ai_running ? 'disabled' : ''}>
            <span class="ha-toggle-track" style="${auto.ai_running ? 'opacity: 0.6;' : ''}"></span>
            <span class="ha-toggle-thumb"></span>
        </label>
        <button id="irr-btn-ai-settings" class="ha-icon-btn material-symbols-outlined" style="color: var(--ha-primary); cursor: ${auto.ai_running ? 'not-allowed' : 'pointer'}; font-size: 20px; padding: 4px;" title="AI Agronomist Settings" ${auto.ai_running ? 'disabled' : ''}>settings</button>
      </div>
    </div>${schedCondHTML}`;

    const setTrueHTML = (sched.setIfTrue || []).map(i => {
      const topic = i.switchCmdTopic || "";
      return `<div class="irr-sw-row" data-topic="${escHtml(topic)}"><span style="display:flex;align-items:center;">${escHtml(i.switchName || topic || "Switch")}</span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`;
    }).join("");
    
    const setFalseHTML = (sched.setIfFalse || []).map(i => {
      const topic = i.switchCmdTopic || "";
      const isYielded = (auto.runtime?.sched_yielded_switches || []).includes(topic);
      const yieldIcon = isYielded ? `<span class="material-symbols-outlined" style="font-size:14px;color:var(--ha-yellow);margin-left:4px;vertical-align:middle;" title="Yielding priority to another active sequence/schedule">warning</span>` : "";
      return `<div class="irr-sw-row" data-topic="${escHtml(topic)}"><span style="display:flex;align-items:center;">${escHtml(i.switchName || topic || "Switch")}${yieldIcon}</span><span class="irr-sw-state ${i.state === 'ON' ? 'on' : 'off'}">${i.state}</span></div>`;
    }).join("");

    if (setTrueHTML || setFalseHTML) {
      schedBody.innerHTML += `<div style="display:flex;gap:20px;margin-top:16px;">
        ${setTrueHTML ? `<div style="flex:1;"><span class="irr-label">During Schedule</span><div style="margin-top:6px;">${setTrueHTML}</div></div>` : ''}
        ${setFalseHTML ? `<div style="flex:1;"><span class="irr-label">Outside Schedule</span><div style="margin-top:6px;">${setFalseHTML}</div></div>` : ''}
      </div>`;
    }

    // Use event delegation on the parent body to guarantee the click is captured regardless of CSS
    schedBody.onclick = (e) => {
      const settingsBtn = e.target.closest("#irr-btn-ai-settings");
      if (settingsBtn) {
        if (auto.ai_running) {
          if (window.showToastNotification) {
            window.showToastNotification("AI Agent Busy", "Please wait until the AI finishes updating your schedule.", "warning");
          }
          return;
        }
        openAiRulesModal(auto.id);
        return;
      }

      const toggleWrap = e.target.closest(".ha-toggle");
      if (toggleWrap && toggleWrap.querySelector("#irr-main-ai-toggle")) {
        const toggleInput = toggleWrap.querySelector("#irr-main-ai-toggle");
        
        if (auto.ai_running) {
          e.preventDefault();
          // Force visual state to match actual state
          toggleInput.checked = aiEnabled;
          if (window.showToastNotification) {
            window.showToastNotification("AI Agent Busy", "Please wait until the AI finishes updating your schedule.", "warning");
          }
          return;
        }

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
      let t = "";
      if (l.ts) {
        // Parse the timestamp. If the backend sent a naive string, append 'Z' so JS treats it as absolute UTC.
        // If it already has a Z or offset, it will parse correctly.
        let tsStr = l.ts;
        if (!tsStr.endsWith("Z") && !tsStr.includes("+") && !tsStr.includes("-", 10)) {
            tsStr += "Z"; 
        }
        const utcMs = new Date(tsStr).getTime();
        
        if (!isNaN(utcMs)) {
            // Apply the automation's timezone offset
            const offsetMins = auto?.schedule?.utcOffset ?? new Date().getTimezoneOffset();
            const targetMs = utcMs - (offsetMins * 60000);
            const d = new Date(targetMs);
            
            let h = d.getUTCHours();
            let m = d.getUTCMinutes();
            let s = d.getUTCSeconds();
            const ampm = h >= 12 ? 'pm' : 'am';
            h = h % 12 || 12;
            t = `${h}:${m < 10 ? '0'+m : m}:${s < 10 ? '0'+s : s} ${ampm}`;
        }
      }
      return `<div class="irr-log-entry"><span class="irr-log-dot ${l.level || 'info'}"></span><span class="irr-log-time">${escHtml(t)}</span><span>${escHtml(l.msg || "")}</span></div>`;
    }).join("")}</div>` : '<span style="color:var(--ha-text-disabled);font-size:12px">No activity yet</span>';

    // Button handlers
    $("#irr-btn-reset").onclick = () => {
      delete _lastProgress[auto.id];
      socket.emit("reset_automation", { id: auto.id });
    };
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
    // Deinitialization rows (run when the automation is turned OFF)
    renderFormRows("auto-f-deinit", auto?.deinitialization || [], "switch");
    // Set if True / Set if False rows
    renderFormRows("auto-f-set-true", auto?.schedule?.setIfTrue || [], "switch");
    renderFormRows("auto-f-set-false", auto?.schedule?.setIfFalse || [], "switch");
    // Scheduler condition rows
    renderFormRows("auto-f-sched-cond", auto?.schedule?.conditions || [], "condition");
    // Condition rows
    renderFormRows("auto-f-cond", auto?.condition || [], "condition");
    // Action rows
    renderFormRows("auto-f-actions", auto?.actions || [], "action");
    // Error rows
    renderFormRows("auto-f-error", auto?.errorState || [], "switch");

    // Reset conflict validation state (rows are freshly rendered, so any old
    // red outlines are gone; just clear the banner and the live-check flag).
    $("#auto-modal-warning")?.classList.add("hidden");
    const warnEl = $("#auto-modal-warning");
    if (warnEl) { warnEl.classList.add("hidden"); warnEl.innerHTML = ""; }

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
      const initialTopic = data?.sensorStateTopic || "";
      const sType = sensorTypeForTopic(initialTopic);
      // Each condition is one line: sensor + operator + value + × (right). The AND/OR is a
      // separate centered connector inserted BETWEEN rows (see refreshCondLogic). Logic is
      // stored on the row's data-logic so it survives add/remove re-renders.
      row.dataset.logic = data?.logic === "OR" ? "OR" : "AND";
      row.innerHTML = `<select class="f-sensor">${sensorOptions(initialTopic)}</select>
        <span class="f-cond-value-cell" style="display:flex;gap:6px;align-items:center;">${condValueHtml(sType, data)}</span>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
      // Rebuild the operator/value UI whenever the selected sensor changes so that
      // binary sensors show ON/OFF and analog sensors show numeric operators + input.
      const sensorSel = row.querySelector(".f-sensor");
      sensorSel?.addEventListener("change", () => {
        const cell = row.querySelector(".f-cond-value-cell");
        if (cell) cell.innerHTML = condValueHtml(sensorTypeForTopic(sensorSel.value), {});
      });
    } else if (type === "action") {
      const dur = data?.duration || 0;
      const h = Math.floor(dur / 3600);
      const m = Math.floor((dur % 3600) / 60);
      const s = dur % 60;
      row.classList.add("irr-form-row-action");
      row.innerHTML = `<select class="f-switch">${switchOptions(data?.switchCmdTopic || "")}</select>
        <select class="f-state"><option value="ON" ${data?.state === "ON" ? "selected" : ""}>ON</option><option value="OFF" ${data?.state !== "ON" ? "selected" : ""}>OFF</option></select>
        <div class="f-dur-group" style="display:flex;gap:4px;align-items:center;">
          <input type="number" class="f-dur-h" value="${h}" min="0" max="99" style="width:48px;text-align:center;padding:8px 4px;" oninput="if(this.value.length > 2) this.value = this.value.slice(0,2)">
          <span style="font-size:13px;color:var(--ha-text-secondary);margin-right:4px;">h</span>
          <input type="number" class="f-dur-m" value="${m}" min="0" max="59" style="width:48px;text-align:center;padding:8px 4px;" oninput="if(this.value.length > 2) this.value = this.value.slice(0,2)">
          <span style="font-size:13px;color:var(--ha-text-secondary);margin-right:4px;">m</span>
          <input type="number" class="f-dur-s" value="${s}" min="0" max="59" style="width:48px;text-align:center;padding:8px 4px;" oninput="if(this.value.length > 2) this.value = this.value.slice(0,2)">
          <span style="font-size:13px;color:var(--ha-text-secondary);">s</span>
        </div>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    } else if (type === "timeRange") {
      row.classList.add("irr-form-row-time");
      row.innerHTML = `<div class="ha-field"><input type="time" class="f-start" value="${data?.start || ""}" placeholder=" "><label>Start</label></div>
        <span class="irr-time-arrow">→</span>
        <div class="ha-field"><input type="time" class="f-end" value="${data?.end || ""}" placeholder=" "><label>End</label></div>
        <button type="button" class="irr-remove-btn material-symbols-outlined">close</button>`;
    }
    if (type === "switch") {
      const switchSelect = row.querySelector(".f-switch");
      if (switchSelect) {
        switchSelect.addEventListener("change", () => refreshSwitchOptions(container));
      }
    }

    row.querySelector(".irr-remove-btn")?.addEventListener("click", () => {
      row.remove();
      if (type === "condition") refreshCondLogic(container);
      if (type === "switch") refreshSwitchOptions(container);
    });
    container.appendChild(row);
    if (type === "condition") refreshCondLogic(container);
    if (type === "switch") refreshSwitchOptions(container);
  }

  function refreshSwitchOptions(container) {
    if (!container || !container.id) return;
    if (container.id !== "auto-f-init" && 
        container.id !== "auto-f-deinit" && 
        container.id !== "auto-f-set-true" && 
        container.id !== "auto-f-set-false") {
      return;
    }

    const selects = [...container.querySelectorAll(".f-switch")];
    const used = new Set();

    // Walk the rows in order and give each one the first switch not already taken
    // by an earlier row. Empty rows and duplicates (e.g. a new row defaulting to
    // switch 0 when switch 0 is already used) advance to the next available switch.
    selects.forEach(select => {
      const val = select.value;
      if (!val || used.has(val)) {
        const next = [...select.options].find(o => o.value && !used.has(o.value));
        if (next) select.value = next.value;
      }
      if (select.value) used.add(select.value);
    });

    // Disable switches already chosen in another row so the same one can't be picked twice.
    selects.forEach(select => {
      const currentVal = select.value;
      [...select.options].forEach(opt => {
        opt.disabled = opt.value !== currentVal && used.has(opt.value);
      });
    });
  }

  // Detail description: collapsed to one line by default with a "more" toggle that
  // expands the full text (wrapping within the frame) and switches to "show less".
  // The expanded state is remembered per-automation so live re-renders don't reset it.
  let _descExpandedId = null;
  function setupDetailDesc(auto) {
    const desc = $("#irr-detail-desc");
    const toggle = $("#irr-detail-desc-toggle");
    if (!desc || !toggle) return;
    const text = auto.description || "";
    const expanded = _descExpandedId === auto.id && text;

    const applyState = (isExpanded) => {
      desc.classList.toggle("expanded", isExpanded);
      desc.classList.toggle("collapsed", !isExpanded);
      toggle.textContent = isExpanded ? "show less" : "more";
    };

    // Measure overflow in the collapsed (single-line) state.
    applyState(false);
    const overflowing = desc.scrollWidth > desc.clientWidth + 1;
    toggle.style.display = text && (overflowing || expanded) ? "" : "none";
    if (expanded) applyState(true);

    toggle.onclick = () => {
      const nowExpanded = !desc.classList.contains("expanded");
      _descExpandedId = nowExpanded ? auto.id : null;
      applyState(nowExpanded);
    };
  }

  // Scale a state-bar text element's font size down as its content grows longer,
  // so long labels never overflow the fixed-width cell. `sub` uses a smaller base.
  function fitStateText(el, sub) {
    if (!el) return;
    const len = (el.textContent || "").length;
    const base = sub ? 12 : 15;
    let size = base;
    if (len > 26) size = base - 4;
    else if (len > 20) size = base - 3;
    else if (len > 15) size = base - 2;
    else if (len > 11) size = base - 1;
    el.style.fontSize = size + "px";
  }

  // The AND/OR operator only makes sense BETWEEN two conditions, so render it as a
  // centered connector inserted between adjacent rows: N conditions -> N-1 connectors,
  // a single condition -> none. The chosen value is stored on the preceding row's
  // data-logic so it survives add/remove re-renders.
  function refreshCondLogic(container) {
    container.querySelectorAll(".irr-cond-connector").forEach((c) => c.remove());
    const rows = [...container.querySelectorAll(".irr-form-row")];
    rows.forEach((row, i) => {
      if (i >= rows.length - 1) return;
      const logic = row.dataset.logic === "OR" ? "OR" : "AND";
      const conn = document.createElement("div");
      conn.className = "irr-cond-connector";
      conn.innerHTML = `<select class="f-logic"><option value="AND"${logic !== "OR" ? " selected" : ""}>AND</option><option value="OR"${logic === "OR" ? " selected" : ""}>OR</option></select>`;
      const sel = conn.querySelector(".f-logic");
      sel.addEventListener("change", () => { row.dataset.logic = sel.value; });
      row.after(conn);
    });
  }

  // Add row buttons
  ["auto-f-init-add", "auto-f-deinit-add", "auto-f-error-add", "auto-f-set-true-add", "auto-f-set-false-add"].forEach(id => {
    $(`#${id}`)?.addEventListener("click", () => addFormRow($(`#${id.replace("-add", "")}`), "switch", {}));
  });
  $("#auto-f-cond-add")?.addEventListener("click", () => addFormRow($("#auto-f-cond"), "condition", {}));
  $("#auto-f-sched-cond-add")?.addEventListener("click", () => addFormRow($("#auto-f-sched-cond"), "condition", {}));
  $("#auto-f-actions-add")?.addEventListener("click", () => addFormRow($("#auto-f-actions"), "action", {}));
  $("#auto-f-time-add")?.addEventListener("click", () => addFormRow($("#auto-f-times-list"), "timeRange", {}));

  let _aiRulesId = null;

  function openAiRulesModal(id) {
    const auto = _autos[id];
    if (!auto) return;
    _aiRulesId = id;
    
    const th = auto.schedule?.ai_thresholds || {};
    $("#auto-f-th-rain").value = th.rain_mm !== undefined ? th.rain_mm : "";
    $("#auto-f-th-et0").value = th.et0_mm !== undefined ? th.et0_mm : "";
    $("#auto-f-th-temp").value = th.temp_c !== undefined ? th.temp_c : "";
    $("#auto-f-th-wind").value = th.wind_kmh !== undefined ? th.wind_kmh : "";
    $("#auto-f-ai-rules").value = auto.schedule?.ai_custom_rules || "";
    
    // Farm Area
    $("#auto-f-farm-area").value = auto.schedule?.farmArea !== undefined ? auto.schedule.farmArea : "";
    $("#ai-rules-modal").classList.remove("hidden");
  }

  function closeAiRulesModal() {
    _aiRulesId = null;
    $("#ai-rules-modal").classList.add("hidden");
  }

  $("#ai-rules-modal-close")?.addEventListener("click", closeAiRulesModal);
  $("#ai-rules-modal-cancel")?.addEventListener("click", closeAiRulesModal);

  $("#ai-rules-modal-save")?.addEventListener("click", () => {
    const auto = _autos[_aiRulesId];
    if (!auto) return;
    
    const lat = auto.schedule?.lat;
    const lon = auto.schedule?.lon;
    const isMissing = !lat || !lon || isNaN(parseFloat(lat)) || isNaN(parseFloat(lon));
    if (isMissing) {
      alert("Location is missing!\n\nPlease click the 'Edit' button and configure your Latitude and Longitude first so the AI can fetch weather data to run predictions.");
      return;
    }
    
    auto.schedule = auto.schedule || {};
    auto.schedule.ai_enabled = true;
    auto.schedule.ai_thresholds = {
      rain_mm: $("#auto-f-th-rain").value !== "" ? parseFloat($("#auto-f-th-rain").value) : 5.0,
      et0_mm: $("#auto-f-th-et0").value !== "" ? parseFloat($("#auto-f-th-et0").value) : 4.0,
      temp_c: $("#auto-f-th-temp").value !== "" ? parseFloat($("#auto-f-th-temp").value) : 30.0,
      wind_kmh: $("#auto-f-th-wind").value !== "" ? parseFloat($("#auto-f-th-wind").value) : 20.0
    };
    auto.schedule.ai_custom_rules = $("#auto-f-ai-rules").value.trim();
    
    if ($("#auto-f-farm-area")) {
      const areaVal = parseFloat($("#auto-f-farm-area").value);
      if (!isNaN(areaVal)) {
          auto.schedule.farmArea = areaVal;
      } else {
          delete auto.schedule.farmArea;
      }
    }
    
    socket.emit("update_automation", auto);
    closeAiRulesModal();
  });

  // Suggest settings via AI
  $("#btn-suggest-ai-settings")?.addEventListener("click", (e) => {
    e.preventDefault();
    const auto = _autos[_aiRulesId];
    if (!auto) return;
    const name = auto.name || "";
    const desc = auto.description || "";
    
    if (!name) {
      alert("Please enter a Name for the automation first so the AI knows what crop or garden area it is!");
      return;
    }
    // Show temporary loading indicator
    const btn = $("#btn-suggest-ai-settings");
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined" style="font-size: 16px;">autorenew</span> Loading...`;
    $("#auto-f-ai-rules").value = "Asking AI Agronomist to analyze crop/soil parameters... ✨";

    socket.emit("suggest_ai_settings", { name, description: desc });

    // Save original button state reference to restore later
    btn._restore = () => {
      btn.disabled = false;
      btn.innerHTML = originalText;
    };
  });

  function collectFormData() {
    const name = $("#auto-f-name").value.trim().slice(0, 35);
    if (!name) { alert("Name is required"); return null; }
    const days = [...$("#auto-f-days").querySelectorAll(".irr-day-btn.active")].map(b => b.dataset.day);

    const collectSwitchRows = (containerId) => [...$(`#${containerId}`).querySelectorAll(".irr-form-row")].map(r => {
      const sel = r.querySelector(".f-switch");
      const opt = sel?.selectedOptions[0];
      return { switchCmdTopic: sel?.value || "", switchStateTopic: opt?.dataset.state || "", switchName: opt?.dataset.name || "", state: r.querySelector(".f-state")?.value || "OFF" };
    });

    // Collect a single condition row. The operator dropdown (.f-op) is always present.
    // Analog sensors provide a numeric input (.f-val); binary sensors an ON/OFF selector (.f-state).
    const collectCondRow = (r) => {
      const sel = r.querySelector(".f-sensor");
      const opt = sel?.selectedOptions[0];
      const valInput = r.querySelector(".f-val");
      const stateSel = r.querySelector(".f-state");
      return {
        sensorStateTopic: sel?.value || "",
        sensorName: opt?.dataset.name || "",
        op: r.querySelector(".f-op")?.value || "==",
        value: valInput ? (valInput.value ?? "") : (stateSel?.value || "OFF"),
        logic: r.dataset.logic || "AND"
      };
    };

    const conds = [...$("#auto-f-cond").querySelectorAll(".irr-form-row")].map(r => collectCondRow(r));

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
      ai_thresholds: editAuto?.schedule?.ai_thresholds || {},
      ai_custom_rules: editAuto?.schedule?.ai_custom_rules || "",
      setIfTrue: collectSwitchRows("auto-f-set-true"),
      setIfFalse: collectSwitchRows("auto-f-set-false"),
      conditions: [...$("#auto-f-sched-cond").querySelectorAll(".irr-form-row")].map(r => collectCondRow(r))
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
      deinitialization: collectSwitchRows("auto-f-deinit"),
      actions,
      errorState: collectSwitchRows("auto-f-error"),
      bufferTime: parseInt($("#auto-f-buffer")?.value || "5", 10),
      maxCyclesPerDay: parseInt($("#auto-f-max-cycles")?.value || "0", 10),
    };
  }

  // ─── Switch conflict validation ─────────────────────────────────────────
  // A physical switch must not be driven by two automations whose schedules
  // overlap in day + time — that causes state "drift". Sensors may be shared
  // freely, and the Error State section is exempt (a safety shutoff may reuse
  // any switch). Switches are considered across Initialization, Set-if-True,
  // Set-if-False and Actions.
  const SWITCH_CONTAINERS = ["auto-f-init", "auto-f-deinit", "auto-f-set-true", "auto-f-set-false", "auto-f-actions"];
  // A physical switch must not be driven by two automations whose schedules
  function _schedIntervals(sched) {
    // Return [startMin, endMin) intervals within a day (matches backend check_schedule).
    sched = sched || {};
    if (sched.is24hr) return [[0, 1440]];
    let ranges = sched.timeRanges || [];
    if (!ranges.length && (sched.startTime || sched.endTime)) {
      ranges = [{ start: sched.startTime, end: sched.endTime }];
    }
    const out = [];
    for (const r of ranges) {
      if (!r || !r.start || !r.end) continue;
      const [sh, sm] = String(r.start).split(":").map(Number);
      const [eh, em] = String(r.end).split(":").map(Number);
      const s = sh * 60 + sm, e = eh * 60 + em;
      if (isNaN(s) || isNaN(e)) continue;
      if (s < e) out.push([s, e]);
      else if (s > e) { out.push([s, 1440]); out.push([0, e]); } // wraps past midnight
      else out.push([0, 1440]); // start == end -> treat as all-day
    }
    // Backend treats a non-24hr schedule with no usable range as the whole day.
    return out.length ? out : [[0, 1440]];
  }

  // Map an automation's schedule into a set of absolute intervals over the week,
  // expressed in UTC minutes [0, 10080). This makes the overlap check correct
  // across different timezones (two automations in different UTC offsets are
  // compared at the same real-world instant) as well as across midnight and the
  // week boundary. utcOffset follows JS getTimezoneOffset (utc = local + offset,
  // e.g. UTC+5:30 = -330), matching the backend's _get_auto_now.
  const _WEEK_MINS = 7 * 1440;
  const _DAY_INDEX = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

  function _pushWeekInterval(out, start, end) {
    // start < end, length <= 1440. Rotate into [0, _WEEK_MINS) and split on wrap.
    const len = end - start;
    let s = ((start % _WEEK_MINS) + _WEEK_MINS) % _WEEK_MINS;
    let e = s + len;
    if (e <= _WEEK_MINS) out.push([s, e]);
    else { out.push([s, _WEEK_MINS]); out.push([0, e - _WEEK_MINS]); }
  }

  function _weeklyUtcIntervals(sched) {
    sched = sched || {};
    const days = sched.days || [];
    if (!days.length) return []; // never runs
    const offset = Number.isFinite(+sched.utcOffset) ? (+sched.utcOffset | 0) : 0;
    const pieces = _schedIntervals(sched); // within-day local minute pieces
    const out = [];
    for (const d of days) {
      const di = _DAY_INDEX[d];
      if (di === undefined) continue;
      for (const [ps, pe] of pieces) {
        // Local weekly minutes -> UTC weekly minutes (utc = local + offset).
        _pushWeekInterval(out, di * 1440 + ps + offset, di * 1440 + pe + offset);
      }
    }
    return out;
  }

  function schedulesOverlap(a, b) {
    const ia = _weeklyUtcIntervals(a), ib = _weeklyUtcIntervals(b);
    if (!ia.length || !ib.length) return false;
    return ia.some(([s1, e1]) => ib.some(([s2, e2]) => s1 < e2 && s2 < e1));
  }

  function switchTopicsOfAuto(auto) {
    const set = new Set();
    const add = (arr) => (arr || []).forEach(x => { if (x && x.switchCmdTopic) set.add(x.switchCmdTopic); });
    add(auto.initialization);
    // intentional: deinitialization is excluded from schedule conflicts
    add(auto.actions);
    add(auto.schedule?.setIfTrue);
    add(auto.schedule?.setIfFalse);
    return set; // Error State intentionally excluded
  }

  function _collectScheduleLite() {
    const daysEl = $("#auto-f-days");
    const days = daysEl ? [...daysEl.querySelectorAll(".irr-day-btn.active")].map(b => b.dataset.day) : [];
    const is24hr = !!$("#auto-f-24hr")?.checked;
    const listEl = $("#auto-f-times-list");
    const timeRanges = listEl ? [...listEl.querySelectorAll(".irr-form-row")].map(r => ({
      start: r.querySelector(".f-start")?.value || "",
      end: r.querySelector(".f-end")?.value || ""
    })) : [];
    const utcOffset = parseInt($("#auto-f-tz")?.value, 10) || 0;
    return { days, is24hr, timeRanges, utcOffset };
  }

  // Validates that Action Sequence switches don't overlap with During Enforcement (setIfTrue) switches
  // Returns false if there's a conflict, true if valid.
  function validateActionConflicts() {
    const errorEl = $("#auto-modal-error");
    // Clear old errors
    $("#auto-f-actions")?.querySelectorAll(".f-switch.error-conflict").forEach(el => el.classList.remove("error-conflict"));
    
    // Collect all topics used in setIfTrue (During Enforcement)
    const setIfTrueTopics = new Set();
    $("#auto-f-set-true")?.querySelectorAll(".f-switch").forEach(sel => {
      const topic = sel.value;
      if (topic) setIfTrueTopics.add(topic);
    });

    const conflicts = new Set();
    
    // Check Action Sequence (auto-f-actions) against setIfTrueTopics
    $("#auto-f-actions")?.querySelectorAll(".f-switch").forEach(sel => {
      const topic = sel.value;
      if (topic && setIfTrueTopics.has(topic)) {
        sel.classList.add("error-conflict");
        const swName = sel.selectedOptions[0]?.dataset.name || topic;
        conflicts.add(swName);
      }
    });

    if (!errorEl) return true;
    if (conflicts.size === 0) {
      errorEl.classList.add("hidden");
      errorEl.classList.remove("error", "error-action");
      errorEl.innerHTML = "";
      return true;
    }
    
    const parts = [...conflicts].map(sw => `“${escHtml(sw)}”`);
    errorEl.classList.add("error", "error-action");
    errorEl.classList.remove("hidden");
    errorEl.innerHTML = `<span class="material-symbols-outlined">error</span><span>Error: ${parts.join(", ")} cannot be used in the Action Sequence because they are also used in During Enforcement.</span>`;
    return false;
  }

  // Validates that switches in Outside Enforcement (setIfFalse) do not conflict with the state enforced by other automations.
  // Returns false if there's a conflict, true if valid.
  function validateOutsideEnforcementConflicts() {
    const errorEl = $("#auto-modal-error");
    // Clear old errors and injected icons
    $("#auto-f-set-false")?.querySelectorAll(".f-switch.error-conflict, .f-state.error-conflict").forEach(el => el.classList.remove("error-conflict"));
    
    // Map of topic -> { state, autoName } from other automations' setIfFalse
    const globalEnforceMap = new Map();
    for (const id in _autos) {
      if (id === _editId) continue;
      const other = _autos[id];
      const outsideList = other.schedule?.setIfFalse || [];
      outsideList.forEach(item => {
        if (item.switchCmdTopic) {
          globalEnforceMap.set(item.switchCmdTopic, { state: item.state, autoName: other.name });
        }
      });
    }

    const conflicts = new Set();
    
    $("#auto-f-set-false")?.querySelectorAll(".irr-form-row").forEach(row => {
      const sel = row.querySelector(".f-switch");
      const stateSel = row.querySelector(".f-state");
      const topic = sel?.value;
      const myState = stateSel?.value || "OFF";
      
      if (topic && globalEnforceMap.has(topic)) {
        const globalData = globalEnforceMap.get(topic);
        if (globalData.state !== myState) {
          sel.classList.add("error-conflict");
          if (stateSel) stateSel.classList.add("error-conflict");
          
          const swName = sel.selectedOptions[0]?.dataset.name || topic;
          conflicts.add(`“${escHtml(swName)}” (you set ${myState}, but “${escHtml(globalData.autoName || "Unnamed")}” enforces ${globalData.state})`);
        }
      }
    });

    if (!errorEl) return true;
    if (conflicts.size === 0) {
      // Don't clear errorEl if it's already showing an error from validateActionConflicts
      if (!errorEl.classList.contains("error-action")) {
        errorEl.classList.add("hidden");
        errorEl.classList.remove("error");
        errorEl.innerHTML = "";
      }
      return true;
    }
    
    errorEl.classList.add("error");
    errorEl.classList.remove("error-action"); // Clear any specific tag from the other validator
    errorEl.classList.remove("hidden");
    const parts = [...conflicts];
    errorEl.innerHTML = `<span class="material-symbols-outlined">error</span><span>Error: ${parts.join("; ")}. All automations must agree on the same Outside Enforcement state!</span>`;
    return false;
  }

  // Highlight conflicting switch selects, toggle the warning banner, and return true.
  // Saving is always allowed, this is just a warning.
  function validateSwitchConflicts() {
    const warnEl = $("#auto-modal-warning");
    SWITCH_CONTAINERS.forEach(cid => {
      $(`#${cid}`)?.querySelectorAll(".f-switch.warning-conflict").forEach(el => el.classList.remove("warning-conflict"));
    });

    const sched = _collectScheduleLite();

    // switchCmdTopic -> Set of other automation names overlapping in schedule
    const topicToAutos = new Map();
    for (const id in _autos) {
      if (id === _editId) continue;
      const other = _autos[id];
      if (!schedulesOverlap(sched, other.schedule || {})) continue;
      for (const t of switchTopicsOfAuto(other)) {
        if (!topicToAutos.has(t)) topicToAutos.set(t, new Set());
        topicToAutos.get(t).add(other.name || "Unnamed");
      }
    }

    const conflicts = new Map(); // switchName -> Set(other automation names)
    SWITCH_CONTAINERS.forEach(cid => {
      if (cid === "auto-f-deinit") return; // Ignore deinit for conflict checks
      $(`#${cid}`)?.querySelectorAll(".f-switch").forEach(sel => {
        const topic = sel.value;
        if (topic && topicToAutos.has(topic)) {
          sel.classList.add("warning-conflict");
          const swName = sel.selectedOptions[0]?.dataset.name || topic;
          if (!conflicts.has(swName)) conflicts.set(swName, new Set());
          topicToAutos.get(topic).forEach(n => conflicts.get(swName).add(n));
        }
      });
    });

    if (!warnEl) return true;
    if (conflicts.size === 0) {
      warnEl.classList.add("hidden");
      warnEl.classList.remove("warning");
      warnEl.innerHTML = "";
      return true;
    }
    const parts = [...conflicts.entries()].map(([sw, autos]) =>
      `“${escHtml(sw)}” is used by ${escHtml([...autos].join(", "))}`);
    warnEl.classList.add("warning");
    warnEl.classList.remove("hidden");
    warnEl.innerHTML = `<span class="material-symbols-outlined">warning</span><span>${parts.join("; ")} during an overlapping time window. This is allowed, but may cause conflicts if both are turned on.</span>`;
    return true;
  }

  // Validates an automation before it is turned ON (status -> ON).
  // Checks only against other automations that are already ON.
  // Returns a Map of switchCmdTopic -> Set(other automation names).
  function validateRunConflicts(autoToRun) {
    const conflicts = new Map();
    // Re-use the existing logic to calculate its schedule
    const schedToRun = autoToRun.schedule || {};
    
    // switchCmdTopic -> Set of other automation names overlapping in schedule
    for (const id in _autos) {
      if (id == autoToRun.id) continue;
      const other = _autos[id];
      if (other.status !== "ON") continue;
      
      if (!schedulesOverlap(schedToRun, other.schedule || {})) continue;
      
      const otherTopics = switchTopicsOfAuto(other);
      const myTopics = switchTopicsOfAuto(autoToRun);
      
      for (const t of myTopics) {
        if (otherTopics.has(t)) {
          if (!conflicts.has(t)) conflicts.set(t, new Set());
          conflicts.get(t).add(other.name || "Unnamed");
        }
      }
    }
    
    const conflictsByName = new Map();
    for (const t of conflicts.keys()) {
      let swName = t;
      if (window._dashboardEntities) {
        for (const eid in window._dashboardEntities) {
          const e = window._dashboardEntities[eid];
          if (e.cmdTopic === t) {
            swName = e.name || t;
            if (window._dashboardDevices && window._dashboardDevices[e.deviceSerial]) {
              swName += ` (${window._dashboardDevices[e.deviceSerial].name})`;
            }
            break;
          }
        }
      }
      conflictsByName.set(swName, conflicts.get(t));
    }
    
    conflictsByName.topics = Array.from(conflicts.keys());
    return conflictsByName;
  }

  // Keep the highlights/banner in sync as the user edits switches, days, times or the 24-hour toggle.
  const _maybeLiveValidate = () => { 
    validateActionConflicts();
    validateOutsideEnforcementConflicts();
    validateSwitchConflicts(); 
  };
  modalOverlay?.addEventListener("change", _maybeLiveValidate);
  modalOverlay?.addEventListener("input", _maybeLiveValidate);
  modalOverlay?.addEventListener("click", (e) => {
    if (e.target.classList?.contains("irr-day-btn") || e.target.closest(".irr-remove-btn") || e.target.closest(".irr-add-btn")) {
      // Small timeout to allow DOM changes (like adding a row or toggling a class) to settle
      setTimeout(_maybeLiveValidate, 0);
    }
  });

  // Save
  $("#auto-modal-save")?.addEventListener("click", () => {
    const data = collectFormData();
    if (!data) return;
    
    // Check for hard errors (blocks saving)
    if (!validateActionConflicts()) { return; }
    if (!validateOutsideEnforcementConflicts()) { return; }
    // Check for soft warnings (allows saving)
    validateSwitchConflicts();
    
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

  // Automation name search filter
  searchInput?.addEventListener("input", () => {
    _searchQuery = (searchInput.value || "").trim().toLowerCase();
    updateClearFiltersBtn();
    renderList();
  });

  // Reflect the current _statusFilter set onto the dropdown checkboxes + trigger label.
  function syncFilterDropdown() {
    if (!filterDDMenu) return;
    const isAll = _statusFilter.has("all") || _statusFilter.size === 0;
    filterDDMenu.querySelectorAll(".irr-filter-dd-cb").forEach((cb) => {
      cb.checked = cb.value === "all" ? isAll : (!isAll && _statusFilter.has(cb.value));
    });
    if (filterDDLabel) {
      if (isAll) {
        filterDDLabel.textContent = "All";
      } else if (_statusFilter.size === 1) {
        const v = [..._statusFilter][0];
        filterDDLabel.textContent = v.charAt(0).toUpperCase() + v.slice(1);
      } else {
        filterDDLabel.textContent = `${_statusFilter.size} selected`;
      }
    }
  }

  // Position the (fixed) menu directly under the trigger. Fixed positioning
  // lets it escape the list panel's overflow:hidden so every option is visible.
  function positionFilterMenu() {
    if (!filterDDTrigger || !filterDDMenu) return;
    const r = filterDDTrigger.getBoundingClientRect();
    filterDDMenu.style.top = (r.bottom + 4) + "px";
    filterDDMenu.style.left = r.left + "px";
    filterDDMenu.style.minWidth = r.width + "px";
  }
  function closeFilterMenu() {
    filterDDMenu?.classList.add("hidden");
    filterDDTrigger?.setAttribute("aria-expanded", "false");
  }

  // Open / close the filter dropdown.
  filterDDTrigger?.addEventListener("click", (e) => {
    e.stopPropagation();
    const willOpen = filterDDMenu.classList.contains("hidden");
    if (willOpen) {
      positionFilterMenu();
      filterDDMenu.classList.remove("hidden");
      filterDDTrigger.setAttribute("aria-expanded", "true");
    } else {
      closeFilterMenu();
    }
  });
  // Close on outside click.
  document.addEventListener("click", (e) => {
    if (filterDD && !filterDD.contains(e.target)) closeFilterMenu();
  });
  // A fixed menu doesn't follow scroll — close it instead (capture inner scrolls too).
  window.addEventListener("scroll", () => {
    if (!filterDDMenu?.classList.contains("hidden")) closeFilterMenu();
  }, true);
  window.addEventListener("resize", closeFilterMenu);

  // Automation status/state filter (multi-select checkboxes, OR logic).
  // "All" is exclusive: it clears the specific filters and shows everything.
  filterDDMenu?.addEventListener("change", (e) => {
    const cb = e.target;
    if (!cb.classList.contains("irr-filter-dd-cb")) return;
    if (cb.value === "all") {
      _statusFilter = new Set(["all"]);
    } else {
      const checked = [...filterDDMenu.querySelectorAll(".irr-filter-dd-cb")]
        .filter((x) => x.checked && x.value !== "all")
        .map((x) => x.value);
      // No specific selection falls back to "All" so the list stays populated.
      _statusFilter = checked.length ? new Set(checked) : new Set(["all"]);
    }
    syncFilterDropdown();
    updateClearFiltersBtn();
    renderList();
  });

  // Clear button next to the dropdown: reset the filter and select all.
  filterClearBtn?.addEventListener("click", () => {
    _statusFilter = new Set(["all"]);
    syncFilterDropdown();
    updateClearFiltersBtn();
    renderList();
  });

  // Show the clear button only when a search term or non-default filter is active.
  function updateClearFiltersBtn() {
    if (!clearFiltersBtn) return;
    const active = _searchQuery !== "" || !_statusFilter.has("all");
    clearFiltersBtn.classList.toggle("hidden", !active);
  }

  // Clear all automation filters (search box + status filter).
  clearFiltersBtn?.addEventListener("click", () => {
    _searchQuery = "";
    _statusFilter = new Set(["all"]);
    if (searchInput) searchInput.value = "";
    syncFilterDropdown();
    updateClearFiltersBtn();
    renderList();
    searchInput?.focus();
  });

  // ─── Socket events ───
  socket.on("automations_list", (list) => {
    _autos = {};
    (list || []).forEach(a => { _autos[a.id] = a; });
    window.automations = _autos;
    renderList();
    renderDetail();
  });

  socket.on("automation_update", (data) => {
    const auto = data.automation;
    if (!auto) return;
    auto.logs = data.logs || [];
    _autos[auto.id] = auto;
    window.automations = _autos;
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

  socket.on("automation_error", (data) => {
    if (window.showToastNotification) {
      window.showToastNotification("Automation Conflict", data.error, "error");
    } else {
      alert(data.error);
    }
  });

  socket.on("suggested_ai_settings_response", (res) => {
    const btn = $("#btn-suggest-ai-settings");
    if (btn && typeof btn._restore === "function") {
      btn._restore();
    }
    
    if (res.status === "success") {
      const d = res.data;
      $("#auto-f-ai-rules").value = d.suggested_rules || "";
      $("#auto-f-th-rain").value = d.rain_mm ?? 5.0;
      $("#auto-f-th-et0").value = d.et0_mm ?? 4.0;
      $("#auto-f-th-temp").value = d.temp_c ?? 30.0;
      $("#auto-f-th-wind").value = d.wind_kmh ?? 20.0;
    } else {
      $("#auto-f-ai-rules").value = "";
      alert("AI settings suggestion failed: " + (res.message || "Unknown error"));
    }
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
    _activeAnalyticsId = null;
  });

  $("#auto-analytics-modal")?.addEventListener("click", (e) => {
    if (e.target === $("#auto-analytics-modal")) {
      $("#auto-analytics-modal").classList.add("hidden");
      _activeAnalyticsId = null;
    }
  });

  function openAnalyticsModal(auto) {
    _activeAnalyticsId = auto.id;
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
    
    // Create localized labels for charts (e.g. "May 12" and "Today")
    const chartLabels = dates.map(dStr => {
      const todayStr = new Date().toISOString().split('T')[0];
      if (dStr === todayStr) return "Today";
      const parts = dStr.split('-');
      if (parts.length !== 3) return dStr;
      const d = new Date(parts[0], parts[1] - 1, parts[2]);
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    });
    
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
    const lastRunEl = $("#auto-analytics-last-run");
    if (lastRunEl) lastRunEl.textContent = lastRun;
    
    // Render Charts after DOM layout finishes rendering the modal container
    setTimeout(() => {
      if (_durationChart) _durationChart.destroy();
      if (_cyclesChart) _cyclesChart.destroy();
      
      const durCanvas = document.getElementById("analytics-chart-duration");
      const cycCanvas = document.getElementById("analytics-chart-cycles");
      
      if (!durCanvas || !cycCanvas) return;
      
      // Force parent to compute layout before chart init
      const durParent = durCanvas.parentElement;
      const cycParent = cycCanvas.parentElement;
      
      // Explicitly set canvas pixel dimensions from parent
      durCanvas.width = durParent.clientWidth;
      durCanvas.height = durParent.clientHeight;
      cycCanvas.width = cycParent.clientWidth;
      cycCanvas.height = cycParent.clientHeight;
      
      console.log("[CHART DEBUG] durCanvas:", durCanvas.width, "x", durCanvas.height, "data:", durData);
      console.log("[CHART DEBUG] cycCanvas:", cycCanvas.width, "x", cycCanvas.height, "data:", cycData);
      console.log("[CHART DEBUG] cycHist raw:", JSON.stringify(cycHist));
      console.log("[CHART DEBUG] durHist raw:", JSON.stringify(durHist));
      
      const ctxDur = durCanvas.getContext("2d");
      const ctxCyc = cycCanvas.getContext("2d");
      
      Chart.defaults.color = "rgba(255, 255, 255, 0.7)";
      Chart.defaults.font.family = "'Roboto', sans-serif";
      
      _durationChart = new Chart(ctxDur, {
        type: "line",
        data: {
          labels: chartLabels,
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
          labels: chartLabels,
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
      
      // Force Chart.js to recalculate
      _durationChart.resize();
      _cyclesChart.resize();
    }, 300);
  }

  socket.on("weather_insights_data", (data) => {
    const auto_id = data.auto_id;
    // Prevent old/delayed background fetch data from overwriting currently open modal
    if (auto_id !== _activeAnalyticsId) return;
    
    const weather = data.weather;
    if (!weather) return;

    // Build 14-day labels and data arrays
    const labels = [];
    const rainData = [];
    const et0Data = [];

    // Format date string (YYYY-MM-DD) to a readable day format (e.g., "May 12")
    const formatLabel = (dateStr) => {
        if (!dateStr) return "";
        const parts = dateStr.split('-');
        if (parts.length !== 3) return dateStr;
        const d = new Date(parts[0], parts[1] - 1, parts[2]);
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    };

    // Parse past days
    Object.values(weather.past_days || {}).forEach(day => {
        labels.push(formatLabel(day.date));
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
        labels.push(formatLabel(day.date));
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
                    title: { display: true, text: 'Rain / ET0 (mm)', color: 'rgba(255,255,255,0.7)' },
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
