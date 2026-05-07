/* ==========================================
   Nivixsa Dashboard — Home Assistant Style JS
   ========================================== */
(function () {
  "use strict";

  // -------------------------------------------------------
  // State
  // -------------------------------------------------------
  const entities = {};        // entityId -> { config, state, type, name, topic, ... }
  window._dashboardEntities = entities;  // Expose for automation.js
  const devices = {};         // serial -> { name, model, sw_version, serial, manufacturer }
  window._dashboardDevices = devices;    // Expose for automation.js
  const allTopics = new Set();
  const logEntries = [];
  const MAX_LOG = 500;
  const charts = {};          // topic -> Chart instance

  // -------------------------------------------------------
  // DOM refs
  // -------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const loginOverlay = $("#login-overlay");
  const loginForm    = $("#login-form");
  const loginEmail   = $("#login-email");
  const loginPass    = $("#login-password");
  const loginError   = $("#login-error");
  const appEl        = $("#app");

  const sidebar       = $("#ha-sidebar");
  const sidebarToggle = $("#sidebar-toggle");
  const navItems      = $$(".ha-nav-item");

  const statusDot     = $("#status-indicator");
  const statusDotMob  = $("#status-indicator-mobile");
  const statusText    = $("#status-text");
  const deviceCount   = $("#device-count");

  const statusBadges      = $("#status-badges");
  const overviewDevices   = $("#overview-devices");
  const lightsDevices     = $("#lights-devices");
  const switchesDevices   = $("#switches-devices");
  const sensorsDevices    = $("#sensors-devices");
  const sensorsBadges     = $("#sensors-badges");
  const sensorCharts      = $("#sensor-charts");

  const logBody       = $("#log-body");
  const logFilter     = $("#log-filter");
  const btnClearLog   = $("#btn-clear-log");
  const btnSubscribe  = $("#btn-subscribe");

  const pubTopic      = $("#pub-topic");
  const pubPayload    = $("#pub-payload");
  const btnPublish    = $("#btn-publish");
  const pubFeedback   = $("#publish-feedback");
  const topicList     = $("#topic-list");
  const allEntList    = $("#all-entities-list");

  // -------------------------------------------------------
  // Socket.IO
  // -------------------------------------------------------
  const socket = io();

  // -------------------------------------------------------
  // Login
  // -------------------------------------------------------
  loginForm.addEventListener("submit", (e) => {
    e.preventDefault();
    loginError.classList.add("hidden");
    socket.emit("login", { email: loginEmail.value, password: loginPass.value });
  });

  // -------------------------------------------------------
  // MQTT Status
  // -------------------------------------------------------
  socket.on("mqtt_status", (data) => {
    const connected = data.connected;
    [statusDot, statusDotMob].forEach((dot) => {
      if (dot) dot.classList.toggle("connected", connected);
    });
    if (statusText) statusText.textContent = data.message || (connected ? "Connected" : "Disconnected");

    if (connected && data.message === "Connected") {
      loginOverlay.classList.add("hidden");
      appEl.classList.remove("hidden");
    }
    if (!connected && data.message && !loginOverlay.classList.contains("hidden")) {
      const msg = data.message.toLowerCase();
      if (msg.includes("bad credentials") || msg.includes("not authorised")) {
        loginError.textContent = "Invalid email or password";
      } else {
        loginError.textContent = data.message;
      }
      loginError.classList.remove("hidden");
    }
  });

  // -------------------------------------------------------
  // Device Updates
  // -------------------------------------------------------
  socket.on("device_update", (msg) => {
    const topic = msg.topic;
    const payload = msg.payload;
    const ts = msg.ts;

    allTopics.add(topic);

    // Parse topic structure: prefix/component/node/objectId/suffix
    const parts = topic.split("/");
    const isConfig = topic.endsWith("/config");
    const isState = topic.endsWith("/state");

    if (isConfig && typeof payload === "object" && payload !== null) {
      handleDiscoveryConfig(topic, payload);
    } else {
      handleStateUpdate(topic, payload, ts);
    }

    addLogEntry(topic, payload, ts);
    addDevLog("RX", topic, msg.raw != null ? msg.raw : payload);
    updateTopicDatalist();
    updateEntityCount();
  });

  // -------------------------------------------------------
  // Discovery config → register entity
  // -------------------------------------------------------
  function handleDiscoveryConfig(topic, config) {
    const parts = topic.split("/");
    // prefix/component/nodeId/objectId/config
    const component = parts[1] || "unknown";
    const nodeId = parts[2] || "";
    const objectId = parts[3] || "";
    const entityId = `${component}.${nodeId}_${objectId}`;

    const name = config.name || objectId || entityId;
    const stateTopic = config.state_topic || config.stat_t || "";
    const cmdTopic = config.command_topic || config.cmd_t || "";
    const availTopic = config.availability_topic || "";

    // Extract device info (serial_number identifies the physical unit)
    const dev = config.device || {};
    const serial = dev.serial_number || objectId.split("_")[0] || "unknown";
    if (serial && !devices[serial]) {
      devices[serial] = {
        serial,
        name: dev.name || serial,
        model: dev.model || "",
        manufacturer: dev.manufacturer || "Nivixsa",
        sw_version: dev.sw_version || "",
      };
    }

    entities[entityId] = entities[entityId] || {};
    Object.assign(entities[entityId], {
      entityId,
      type: component,
      name,
      config,
      stateTopic,
      cmdTopic,
      availTopic,
      configTopic: topic,
      deviceSerial: serial,
      state: entities[entityId]?.state || null,
      stateRaw: entities[entityId]?.stateRaw || null,
      // HA: availability_mode — "latest" (default), "all", or "any"
      _availMode: config.availability_mode || config.avty_mode || "latest",
      _availTopicStates: entities[entityId]?._availTopicStates || {},
      _availLatest: entities[entityId]?._availLatest ?? true,
    });
    // HA: no avail topics configured = always available
    // If avail topics exist but no message received yet, default available=true
    // (HA also starts entities as available until a not_available message arrives)
    const e = entities[entityId];
    if (e.available === undefined) e.available = true;

    // Map state topic → entityId for fast lookups
    if (stateTopic) {
      _stateTopicMap[stateTopic] = entityId;
    }
    if (availTopic) {
      _availTopicMap[availTopic] = _availTopicMap[availTopic] || [];
      if (!_availTopicMap[availTopic].includes(entityId)) {
        _availTopicMap[availTopic].push(entityId);
      }
      // Store custom payload_available / payload_not_available per topic
      // Only set if explicitly provided in config; otherwise _checkAvail uses fallback
      if (!_availPayloads[availTopic]) {
        const plAvail = config.payload_available || config.pl_avail;
        const plNotAvail = config.payload_not_available || config.pl_not_avail;
        if (plAvail || plNotAvail) {
          _availPayloads[availTopic] = {
            pl_avail: (plAvail || "online").toString(),
            pl_not_avail: (plNotAvail || "offline").toString(),
          };
        }
      }
      // Apply any availability received before config arrived
      if (_pendingAvail[availTopic] !== undefined) {
        e._availTopicStates[availTopic] = _pendingAvail[availTopic];
        e._availLatest = _pendingAvail[availTopic];
        e.available = _computeAvail(e);
      }
    }
    if (config.availability) {
      (Array.isArray(config.availability) ? config.availability : []).forEach((a) => {
        if (a.topic) {
          _availTopicMap[a.topic] = _availTopicMap[a.topic] || [];
          if (!_availTopicMap[a.topic].includes(entityId)) {
            _availTopicMap[a.topic].push(entityId);
          }
          // Per-entry custom payloads (HA: each avail entry can override)
          const ePlAvail = a.payload_available || a.pl_avail || config.payload_available || config.pl_avail;
          const ePlNotAvail = a.payload_not_available || a.pl_not_avail || config.payload_not_available || config.pl_not_avail;
          if (ePlAvail || ePlNotAvail) {
            _availPayloads[a.topic] = {
              pl_avail: (ePlAvail || "online").toString(),
              pl_not_avail: (ePlNotAvail || "offline").toString(),
            };
          }
          if (_pendingAvail[a.topic] !== undefined) {
            e._availTopicStates[a.topic] = _pendingAvail[a.topic];
            e._availLatest = _pendingAvail[a.topic];
            e.available = _computeAvail(e);
          }
        }
      });
    }

    renderAll();
  }

  const _stateTopicMap = {};   // stateTopic -> entityId
  const _availTopicMap = {};   // availTopic -> [entityIds]
  const _availPayloads = {};   // availTopic -> { pl_avail, pl_not_avail }
  const _pendingAvail = {};    // availTopic -> bool (availability received before config)

  // Check availability using per-topic custom payloads (HA-compatible)
  // Exact string match like HA; falls back to Nivixsa YES / HA online
  function _checkAvail(topic, payload) {
    const p = _availPayloads[topic];
    const str = typeof payload === "string" ? payload : String(payload);
    if (p) return str === p.pl_avail;
    // Fallback: Nivixsa uses YES/NO, HA uses online/offline
    return str === "YES" || str === "online";
  }

  // Compute entity.available from per-topic states + availability_mode (HA logic)
  function _computeAvail(entity) {
    const states = entity._availTopicStates;
    const keys = Object.keys(states);
    if (keys.length === 0) return true; // No avail topics = always available
    const mode = entity._availMode || "latest";
    if (mode === "all") return keys.every((k) => states[k] === true);
    if (mode === "any") return keys.some((k) => states[k] === true);
    // "latest" (default)
    return entity._availLatest;
  }

  // -------------------------------------------------------
  // State / availability updates
  // -------------------------------------------------------
  function handleStateUpdate(topic, payload, ts) {
    // Ignore command echo (/set topics) — only real state updates confirm device is alive
    if (topic.endsWith("/set")) return;

    // Check if it matches a known state topic
    const eid = _stateTopicMap[topic];
    if (eid && entities[eid]) {
      // Clear pending command — real state confirmed by device
      _clearOptimistic(entities[eid]);
      entities[eid].stateRaw = payload;
      if (typeof payload === "object" && payload !== null) {
        entities[eid].state = payload.state || payload.value || JSON.stringify(payload);
        // Merge extra attributes
        if (payload.brightness !== undefined) entities[eid].brightness = payload.brightness;
        if (payload.color_mode !== undefined) entities[eid].colorMode = payload.color_mode;
        if (payload.color !== undefined) entities[eid].color = payload.color;
        if (payload.color_temp !== undefined) entities[eid].colorTemp = payload.color_temp;
      } else {
        entities[eid].state = String(payload);
      }
      renderAll();
      return;
    }

    // Availability topic
    const affectedIds = _availTopicMap[topic];
    if (affectedIds) {
      const avail = _checkAvail(topic, payload);
      _pendingAvail[topic] = avail;
      affectedIds.forEach((id) => {
        if (!entities[id]) return;
        entities[id]._availTopicStates[topic] = avail;
        entities[id]._availLatest = avail;
        entities[id].available = _computeAvail(entities[id]);
      });
      renderAll();
      return;
    }

    // Availability topic fallback: if map wasn't populated yet, match by entity.availTopic
    if (topic.endsWith("/availability")) {
      const avail = _checkAvail(topic, payload);
      _pendingAvail[topic] = avail;
      let matched = false;
      for (const eid in entities) {
        if (entities[eid].availTopic === topic) {
          entities[eid]._availTopicStates[topic] = avail;
          entities[eid]._availLatest = avail;
          entities[eid].available = _computeAvail(entities[eid]);
          matched = true;
        }
      }
      if (matched) { renderAll(); return; }
    }

    // Unknown topic — try to match entity by inspecting existing entities
    for (const eid in entities) {
      const e = entities[eid];
      if (e.stateTopic === topic) {
        if (typeof payload === "object" && payload !== null) {
          e.state = payload.state || payload.value || JSON.stringify(payload);
        } else {
          e.state = String(payload);
        }
        e.stateRaw = payload;
        renderAll();
        return;
      }
      // Ignore /set echo — don't treat command echo as state confirmation
    }
  }

  // -------------------------------------------------------
  // Render helpers
  // -------------------------------------------------------
  let _renderTimer = null;
  let _sliderActive = false;  // Block re-render while user drags slider/picker
  function renderAll() {
    // Debounce renders while bulk-loading snapshots
    if (_renderTimer) return;
    _renderTimer = setTimeout(() => {
      _renderTimer = null;
      if (_sliderActive) return;  // Don't rebuild DOM while user is dragging
      _doRenderAll();
    }, 100);
  }

  function _doRenderAll() {
    // Group entities by device serial
    const byDevice = {};   // serial -> { light:[], switch:[], binary_sensor:[], sensor:[], other:[] }
    const typeGroups = { light: [], switch: [], binary_sensor: [], sensor: [], other: [] };

    for (const eid in entities) {
      const e = entities[eid];
      if (e.available === false) continue;  // Hide offline entities
      const t = e.type;
      const serial = e.deviceSerial || "unknown";
      if (!byDevice[serial]) {
        byDevice[serial] = { light: [], switch: [], binary_sensor: [], sensor: [], other: [] };
      }
      const bucket = (t === "light" || t === "switch" || t === "binary_sensor" || t === "sensor") ? t : "other";
      byDevice[serial][bucket].push(e);
      typeGroups[bucket].push(e);
    }

    renderOverviewByDevice(byDevice);
    renderTypeTabByDevice(lightsDevices, byDevice, ["light"], true);
    renderTypeTabByDevice(switchesDevices, byDevice, ["switch"], true);
    renderTypeTabByDevice(sensorsDevices, byDevice, ["sensor", "binary_sensor"], false);
    renderBadges(statusBadges, typeGroups);
    renderSensorBadges(sensorsBadges, typeGroups.sensor);
    renderAllEntitiesList(typeGroups);
    renderCharts(typeGroups.sensor);
  }

  // -------------------------------------------------------
  // Render device-grouped cards
  // -------------------------------------------------------
  function deviceLabel(serial) {
    const d = devices[serial];
    if (!d) return serial;
    const name = d.name || serial;
    return `${name} (${serial})`;
  }

  function renderOverviewByDevice(byDevice) {
    if (!overviewDevices) return;
    const serials = Object.keys(byDevice);
    if (serials.length === 0) {
      overviewDevices.innerHTML = '<div class="ha-card"><div class="ha-empty-row">Waiting for devices…</div></div>';
      return;
    }
    overviewDevices.innerHTML = serials.map((serial) => {
      const grp = byDevice[serial];
      const allEntities = [...grp.light, ...grp.switch, ...grp.binary_sensor, ...grp.sensor, ...grp.other];
      if (allEntities.length === 0) return "";
      const dev = devices[serial] || {};
      const subtitle = [dev.model, dev.sw_version].filter(Boolean).join(" · ");
      return `
        <div class="ha-card">
          <div class="ha-card-header">
            <span class="material-symbols-outlined">developer_board</span>
            ${escHtml(deviceLabel(serial))}
          </div>
          ${subtitle ? `<div class="ha-device-subtitle">${escHtml(subtitle)}</div>` : ""}
          <div class="ha-entity-rows">
            ${allEntities.map((e) => entityRowHTML(e, e.type === "light" || e.type === "switch")).join("")}
          </div>
        </div>`;
    }).join("");
    // Bind toggle events
    overviewDevices.querySelectorAll(".ha-toggle input").forEach((input) => {
      input.addEventListener("change", onToggle);
    });
    bindLightControls(overviewDevices);
    bindEntityClicks(overviewDevices);
  }

  function renderTypeTabByDevice(container, byDevice, types, showToggle) {
    if (!container) return;
    const typeArr = Array.isArray(types) ? types : [types];
    const serials = Object.keys(byDevice).filter((s) => typeArr.some((t) => byDevice[s][t].length > 0));
    if (serials.length === 0) {
      container.innerHTML = '<div class="ha-card"><div class="ha-empty-row">No entities yet…</div></div>';
      return;
    }
    container.innerHTML = serials.map((serial) => {
      const list = typeArr.flatMap((t) => byDevice[serial][t] || []);
      if (list.length === 0) return "";
      return `
        <div class="ha-card">
          <div class="ha-card-header">
            <span class="material-symbols-outlined">developer_board</span>
            ${escHtml(deviceLabel(serial))}
          </div>
          <div class="ha-entity-rows">
            ${list.map((e) => entityRowHTML(e, showToggle)).join("")}
          </div>
        </div>`;
    }).join("");
    container.querySelectorAll(".ha-toggle input").forEach((input) => {
      input.addEventListener("change", onToggle);
    });
    bindLightControls(container);
    bindEntityClicks(container);
  }

  // -------------------------------------------------------
  // Entity row rendering
  // -------------------------------------------------------
  function entityRowHTML(entity, showToggle) {
    const isOn = entityIsOn(entity);
    const iconName = entityIcon(entity);
    const iconClass = isOn ? "on" : "";
    const stateStr = entityStateText(entity);
    const offline = entity.available === false;

    let control = "";
    if (showToggle && entity.cmdTopic) {
      control = `
        <label class="ha-toggle">
          <input type="checkbox" ${isOn ? "checked" : ""} ${offline ? "disabled" : ""} data-eid="${entity.entityId}">
          <span class="ha-toggle-track"></span>
          <span class="ha-toggle-thumb"></span>
        </label>`;
    } else {
      const valueClass = isOn ? "on" : "";
      control = `<span class="ha-entity-value ${valueClass}">${stateStr}</span>`;
    }

    // Brightness slider + color picker for supported lights
    let extras = "";
    if (entity.type === "light" && entity.config && entity.cmdTopic && !offline) {
      const modes = entity.config.supported_color_modes || [];
      const hasBrightness = modes.includes("brightness") || modes.includes("rgb") || entity.config.brightness;
      const hasColor = modes.includes("rgb");
      const scale = entity.config.brightness_scale || 255;
      const curBrightness = entity.brightness !== undefined ? entity.brightness : (typeof entity.stateRaw === "object" && entity.stateRaw?.brightness !== undefined ? entity.stateRaw.brightness : scale);
      const curColor = entity.color || (typeof entity.stateRaw === "object" ? entity.stateRaw?.color : null);
      const hexColor = curColor ? rgbToHex(curColor.r || 0, curColor.g || 0, curColor.b || 0) : "#ff0000";

      if (hasBrightness || hasColor) {
        extras = `<div class="ha-light-controls">`;
        if (hasBrightness) {
          const effectiveOff = entity.state === "OFF" || entity.state === "off";
          const sliderVal = effectiveOff ? 0 : curBrightness;
          extras += `
            <div class="ha-slider-row">
              <span class="material-symbols-outlined ha-slider-icon">brightness_medium</span>
              <input type="range" class="ha-brightness-slider" min="0" max="${scale}" value="${sliderVal}" data-eid="${entity.entityId}" data-scale="${scale}">
              <span class="ha-slider-value">${sliderVal}</span>
            </div>`;
        }
        if (hasColor) {
          extras += `
            <div class="ha-slider-row">
              <span class="material-symbols-outlined ha-slider-icon">palette</span>
              <input type="color" class="ha-color-picker" value="${hexColor}" data-eid="${entity.entityId}">
              <span class="ha-slider-value">${hexColor}</span>
            </div>`;
        }
        extras += `</div>`;
      }
    }

    return `
      <div class="ha-entity-row-wrap${offline ? " ha-offline" : ""}">
        <div class="ha-entity-row" data-eid="${entity.entityId}">
          <div class="ha-entity-icon ${iconClass}">
            <span class="material-symbols-outlined">${iconName}</span>
          </div>
          <div class="ha-entity-info">
            <div class="ha-entity-name">${escHtml(entity.name)}</div>
            <div class="ha-entity-state-text">${escHtml(stateStr)}</div>
          </div>
          ${control}
        </div>
        ${extras}
      </div>`;
  }

  function rgbToHex(r, g, b) {
    return "#" + [r, g, b].map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, "0")).join("");
  }

  function hexToRgb(hex) {
    const m = hex.replace("#", "").match(/.{2}/g);
    return { r: parseInt(m[0], 16), g: parseInt(m[1], 16), b: parseInt(m[2], 16) };
  }

  const REVERT_TIMEOUT = 2000; // ms to wait for MQTT confirmation before reverting

  function _applyOptimistic(entity, newState, newBrightness, newColor) {
    // Save previous state for revert
    entity._prevState = entity.state;
    entity._prevBrightness = entity.brightness;
    entity._prevColor = entity.color;
    entity._prevStateRaw = entity.stateRaw;
    // Apply optimistic values
    entity._pendingCmd = newState;
    if (newBrightness !== undefined) entity._pendingBrightness = newBrightness;
    if (newColor !== undefined) entity._pendingColor = newColor;
    entity.state = newState;
    if (newBrightness !== undefined) entity.brightness = newBrightness;
    if (newColor !== undefined) entity.color = newColor;
    renderAll();
    // Start revert timer
    if (entity._revertTimer) clearTimeout(entity._revertTimer);
    entity._revertTimer = setTimeout(() => {
      // No MQTT confirmation arrived — revert
      if (entity._pendingCmd) {
        entity.state = entity._prevState;
        entity.brightness = entity._prevBrightness;
        entity.color = entity._prevColor;
        entity.stateRaw = entity._prevStateRaw;
        delete entity._pendingCmd;
        delete entity._pendingBrightness;
        delete entity._pendingColor;
        delete entity._prevState;
        delete entity._prevBrightness;
        delete entity._prevColor;
        delete entity._prevStateRaw;
        delete entity._revertTimer;
        renderAll();
      }
    }, REVERT_TIMEOUT);
  }

  function _clearOptimistic(entity) {
    if (entity._revertTimer) clearTimeout(entity._revertTimer);
    delete entity._pendingCmd;
    delete entity._pendingBrightness;
    delete entity._pendingColor;
    delete entity._prevState;
    delete entity._prevBrightness;
    delete entity._prevColor;
    delete entity._prevStateRaw;
    delete entity._revertTimer;
  }

  function entityIsOn(entity) {
    if (!entity.state) return false;
    const s = String(entity.state).toUpperCase();
    return s === "ON" || s === "1" || s === "TRUE" || s === "OPEN" || s === "YES";
  }

  function entityStateText(entity) {
    if (entity.available === false) return "Offline";
    if (entity.state === null || entity.state === undefined) return "Unknown";
    if (typeof entity.stateRaw === "object" && entity.stateRaw !== null) {
      const s = entity.stateRaw;
      const parts = [];
      if (s.state) parts.push(s.state);
      if (s.brightness !== undefined) parts.push(`Brightness: ${s.brightness}`);
      if (s.color_temp !== undefined) parts.push(`Color Temp: ${s.color_temp}`);
      if (parts.length) return parts.join(" · ");
    }
    return String(entity.state);
  }

  function entityIcon(entity) {
    const t = entity.type;
    const isOn = entityIsOn(entity);
    if (t === "light") return isOn ? "lightbulb" : "lightbulb";
    if (t === "switch") return isOn ? "toggle_on" : "toggle_off";
    if (t === "binary_sensor") return entity.config?.device_class === "motion" ? "motion_sensor_active" : "sensors";
    if (t === "sensor") return "thermostat";
    if (t === "climate") return "thermostat";
    if (t === "fan") return "mode_fan";
    if (t === "cover") return "blinds";
    if (t === "lock") return "lock";
    if (t === "camera") return "videocam";
    return "smart_toy";
  }

  // -------------------------------------------------------
  // Toggle handler
  // -------------------------------------------------------
  function onToggle(e) {
    const eid = e.target.dataset.eid;
    const entity = entities[eid];
    if (!entity || !entity.cmdTopic) return;
    if (entity.available === false) { e.target.checked = !e.target.checked; return; }
    const newState = e.target.checked ? "ON" : "OFF";
    const cmdPayload = JSON.stringify({ state: newState });
    socket.emit("publish", { topic: entity.cmdTopic, payload: cmdPayload });
    _applyOptimistic(entity, newState, undefined, undefined);
  }

  // -------------------------------------------------------
  // Brightness & Color handlers
  // -------------------------------------------------------
  function bindLightControls(container) {
    container.querySelectorAll(".ha-brightness-slider").forEach((slider) => {
      slider.addEventListener("mousedown", () => { _sliderActive = true; });
      slider.addEventListener("touchstart", () => { _sliderActive = true; }, { passive: true });
      slider.addEventListener("input", (e) => {
        const val = e.target.value;
        e.target.closest(".ha-slider-row").querySelector(".ha-slider-value").textContent = val;
      });
      slider.addEventListener("change", (e) => {
        _sliderActive = false;
        const eid = e.target.dataset.eid;
        const entity = entities[eid];
        if (!entity || !entity.cmdTopic || entity.available === false) return;
        const brightness = parseInt(e.target.value, 10);
        // HA behavior: brightness 0 means turn OFF, otherwise turn ON with brightness
        if (brightness === 0) {
          const cmd = { state: "OFF" };
          socket.emit("publish", { topic: entity.cmdTopic, payload: JSON.stringify(cmd) });
          _applyOptimistic(entity, "OFF", 0, undefined);
        } else {
          const cmd = { state: "ON", brightness };
          socket.emit("publish", { topic: entity.cmdTopic, payload: JSON.stringify(cmd) });
          _applyOptimistic(entity, "ON", brightness, undefined);
        }
      });
    });
    container.querySelectorAll(".ha-color-picker").forEach((picker) => {
      picker.addEventListener("mousedown", () => { _sliderActive = true; });
      picker.addEventListener("touchstart", () => { _sliderActive = true; }, { passive: true });
      picker.addEventListener("input", (e) => {
        const hex = e.target.value;
        e.target.closest(".ha-slider-row").querySelector(".ha-slider-value").textContent = hex;
      });
      picker.addEventListener("change", (e) => {
        _sliderActive = false;
        const eid = e.target.dataset.eid;
        const entity = entities[eid];
        if (!entity || !entity.cmdTopic || entity.available === false) return;
        const hex = e.target.value;
        const color = hexToRgb(hex);
        const cmd = { state: "ON", color };
        socket.emit("publish", { topic: entity.cmdTopic, payload: JSON.stringify(cmd) });
        _applyOptimistic(entity, "ON", undefined, color);
      });
    });
  }

  // -------------------------------------------------------
  // Badges (overview)
  // -------------------------------------------------------
  function renderBadges(container, groups) {
    if (!container) return;
    const lightsOn = groups.light.filter(entityIsOn).length;
    const switchesOn = groups.switch.filter(entityIsOn).length;
    const sensorsCount = groups.sensor.length + groups.binary_sensor.length;

    container.innerHTML = `
      <div class="ha-badge ${lightsOn ? "on" : "off"}">
        <span class="material-symbols-outlined">lightbulb</span>
        <span class="ha-badge-value">${lightsOn}</span> lights on
      </div>
      <div class="ha-badge ${switchesOn ? "on" : "off"}">
        <span class="material-symbols-outlined">toggle_on</span>
        <span class="ha-badge-value">${switchesOn}</span> switches on
      </div>
      <div class="ha-badge">
        <span class="material-symbols-outlined">sensors</span>
        <span class="ha-badge-value">${sensorsCount}</span> sensors
      </div>`;
  }

  function renderSensorBadges(container, sensors) {
    if (!container) return;
    if (sensors.length === 0) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = sensors.map((s) => {
      const val = s.state !== null && s.state !== undefined ? s.state : "—";
      return `
        <div class="ha-badge">
          <span class="material-symbols-outlined">${entityIcon(s)}</span>
          <span class="ha-badge-value">${escHtml(String(val))}</span>
          <span>${escHtml(s.name)}</span>
        </div>`;
    }).join("");
  }

  // -------------------------------------------------------
  // All entities in developer view
  // -------------------------------------------------------
  function renderAllEntitiesList(groups) {
    if (!allEntList) return;
    const all = [
      ...groups.light,
      ...groups.switch,
      ...groups.binary_sensor,
      ...groups.sensor,
      ...groups.other,
    ];
    if (all.length === 0) {
      allEntList.innerHTML = '<div class="ha-empty-row">No entities</div>';
      return;
    }
    allEntList.innerHTML = all.map((e) => `
      <div class="ha-entity-row" data-eid="${e.entityId}">
        <div class="ha-entity-icon">
          <span class="material-symbols-outlined">${entityIcon(e)}</span>
        </div>
        <div class="ha-entity-info">
          <div class="ha-entity-name">${escHtml(e.entityId)}</div>
          <div class="ha-entity-state-text">${escHtml(e.name)} · ${escHtml(entityStateText(e))}</div>
        </div>
        <span class="ha-entity-value">${escHtml(e.type)}</span>
      </div>`).join("");
    bindEntityClicks(allEntList);
  }

  // -------------------------------------------------------
  // Charts (history tab)
  // -------------------------------------------------------
  function renderCharts(sensors) {
    if (!sensorCharts) return;
    if (sensors.length === 0) {
      sensorCharts.innerHTML = '<div class="ha-card"><div class="ha-empty-row">No sensor history yet…</div></div>';
      return;
    }
    sensors.forEach((s) => {
      const topic = s.stateTopic || s.configTopic;
      if (!topic) return;
      const chartId = `chart-${cssId(topic)}`;
      let card = sensorCharts.querySelector(`#${CSS.escape(chartId)}`);
      if (!card) {
        card = document.createElement("div");
        card.id = chartId;
        card.className = "ha-chart-card";
        card.innerHTML = `<h4>${escHtml(s.name)}</h4><canvas></canvas>`;
        sensorCharts.appendChild(card);
      }
      // Request history
      socket.emit("get_history", { topic });
    });
  }

  socket.on("sensor_history", (data) => {
    const topic = data.topic;
    const history = data.history || [];
    if (history.length === 0) return;
    const chartId = `chart-${cssId(topic)}`;
    const card = sensorCharts ? sensorCharts.querySelector(`#${CSS.escape(chartId)}`) : null;
    if (!card) return;
    const canvas = card.querySelector("canvas");
    if (!canvas) return;

    const labels = history.map((h) => new Date(h.ts));
    const values = history.map((h) => h.value);

    if (charts[topic]) {
      charts[topic].data.labels = labels;
      charts[topic].data.datasets[0].data = values;
      charts[topic].update("none");
    } else {
      charts[topic] = new Chart(canvas, {
        type: "line",
        data: {
          labels,
          datasets: [{
            data: values,
            borderColor: "#03a9f4",
            backgroundColor: "rgba(3,169,244,.1)",
            borderWidth: 2,
            pointRadius: 0,
            fill: true,
            tension: 0.3,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              type: "time",
              ticks: { color: "#9e9e9e", maxTicksLimit: 6, font: { size: 10 } },
              grid: { color: "rgba(255,255,255,.05)" },
            },
            y: {
              ticks: { color: "#9e9e9e", font: { size: 10 } },
              grid: { color: "rgba(255,255,255,.05)" },
            },
          },
        },
      });
    }
  });

  // -------------------------------------------------------
  // Logbook
  // -------------------------------------------------------
  function addLogEntry(topic, payload, ts) {
    const time = new Date(ts).toLocaleTimeString();
    const payloadStr = typeof payload === "object" ? JSON.stringify(payload) : String(payload);
    logEntries.unshift({ time, topic, payload: payloadStr });
    if (logEntries.length > MAX_LOG) logEntries.length = MAX_LOG;

    // Only render if log tab is visible
    if (logBody && isTabActive("log")) {
      renderLogTable();
    }
  }

  function renderLogTable() {
    const filter = (logFilter ? logFilter.value : "").toLowerCase();
    const filtered = filter
      ? logEntries.filter((e) => e.topic.toLowerCase().includes(filter) || e.payload.toLowerCase().includes(filter))
      : logEntries;
    logBody.innerHTML = filtered.slice(0, 200).map((e) => `
      <tr>
        <td>${escHtml(e.time)}</td>
        <td>${escHtml(e.topic)}</td>
        <td>${escHtml(e.payload)}</td>
      </tr>`).join("");
  }

  if (logFilter) logFilter.addEventListener("input", renderLogTable);
  if (btnClearLog) btnClearLog.addEventListener("click", () => {
    logEntries.length = 0;
    renderLogTable();
  });

  // -------------------------------------------------------
  // Publish + Developer Live Log
  // -------------------------------------------------------
  const devLogBody = $("#dev-log-body");
  const btnClearDevLog = $("#btn-clear-dev-log");
  const devLogEntries = [];
  const MAX_DEV_LOG = 100;

  function addDevLog(direction, topic, payload) {
    const time = new Date().toLocaleTimeString();
    const payloadStr = typeof payload === "object" ? JSON.stringify(payload) : String(payload);
    devLogEntries.unshift({ time, direction, topic, payload: payloadStr });
    if (devLogEntries.length > MAX_DEV_LOG) devLogEntries.length = MAX_DEV_LOG;
    if (isTabActive("developer")) renderDevLog();
  }

  function renderDevLog() {
    if (!devLogBody) return;
    devLogBody.innerHTML = devLogEntries.map((e) => {
      const dirClass = e.direction === "TX" ? "ha-dev-tx" : e.direction === "SUB" ? "ha-dev-sub" : "ha-dev-rx";
      return `<tr class="${dirClass}">
        <td>${escHtml(e.time)}</td>
        <td><span class="ha-dir-badge ${dirClass}">${e.direction}</span></td>
        <td>${escHtml(e.topic)}</td>
        <td>${escHtml(e.payload)}</td>
      </tr>`;
    }).join("");
  }

  if (btnClearDevLog) btnClearDevLog.addEventListener("click", () => {
    devLogEntries.length = 0;
    renderDevLog();
  });

  if (btnPublish) btnPublish.addEventListener("click", () => {
    const topic = pubTopic.value.trim();
    const payload = pubPayload.value.trim();
    if (!topic) return;
    socket.emit("publish", { topic, payload });
    addDevLog("TX", topic, payload);
  });

  if (btnSubscribe) btnSubscribe.addEventListener("click", () => {
    const topic = pubTopic.value.trim();
    if (!topic) return;
    socket.emit("subscribe", { topic });
    addDevLog("SUB", topic, "(listening...)");
    if (pubFeedback) {
      pubFeedback.classList.remove("hidden", "ok", "err");
      pubFeedback.textContent = `Subscribed to ${topic}`;
      pubFeedback.classList.add("ok");
      setTimeout(() => pubFeedback.classList.add("hidden"), 3000);
    }
  });

  socket.on("publish_ack", (data) => {
    if (!pubFeedback) return;
    pubFeedback.classList.remove("hidden", "ok", "err");
    if (data.ok) {
      pubFeedback.textContent = `Published to ${data.topic}`;
      pubFeedback.classList.add("ok");
    } else {
      pubFeedback.textContent = data.error || "Publish failed";
      pubFeedback.classList.add("err");
    }
    setTimeout(() => pubFeedback.classList.add("hidden"), 3000);
  });

  function updateTopicDatalist() {
    if (!topicList) return;
    const current = new Set([...topicList.options].map((o) => o.value));
    for (const t of allTopics) {
      if (!current.has(t)) {
        const opt = document.createElement("option");
        opt.value = t;
        topicList.appendChild(opt);
      }
    }
  }

  // -------------------------------------------------------
  // Entity count
  // -------------------------------------------------------
  function updateEntityCount() {
    const count = Object.keys(entities).length;
    if (deviceCount) deviceCount.textContent = `${count} entit${count === 1 ? "y" : "ies"}`;
  }

  // -------------------------------------------------------
  // Navigation
  // -------------------------------------------------------
  navItems.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      navItems.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      $$(".ha-view").forEach((v) => v.classList.remove("active"));
      const target = $(`#tab-${tab}`);
      if (target) target.classList.add("active");

      // Close mobile sidebar
      if (sidebar) sidebar.classList.remove("open");

      // Lazy renders
      if (tab === "log") renderLogTable();
      if (tab === "developer") renderDevLog();
      if (tab === "history") renderAll();
    });
  });

  function isTabActive(tab) {
    const el = $(`#tab-${tab}`);
    return el && el.classList.contains("active");
  }

  // Mobile sidebar toggle
  const sidebarOverlay = $("#sidebar-overlay");
  function closeSidebar() { sidebar.classList.remove("open"); }

  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", () => {
      sidebar.classList.toggle("open");
    });
  }
  if (sidebarOverlay) {
    sidebarOverlay.addEventListener("click", closeSidebar);
  }
  // Close sidebar when a nav item is tapped on mobile
  navItems.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (window.innerWidth <= 768) closeSidebar();
    });
  });

  // Logout
  const btnLogout = $("#btn-logout");
  if (btnLogout) {
    btnLogout.addEventListener("click", () => {
      socket.emit("logout");
      appEl.classList.add("hidden");
      loginOverlay.classList.remove("hidden");
      loginPass.value = "";
      loginError.classList.add("hidden");
      if (statusText) statusText.textContent = "Disconnected";
      [statusDot, statusDotMob].forEach((d) => { if (d) d.classList.remove("connected"); });
    });
  }

  // -------------------------------------------------------
  // Utility
  // -------------------------------------------------------
  function escHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  function escAttr(str) {
    return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function cssId(str) {
    return str.replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  // -------------------------------------------------------
  // Entity Detail Panel
  // -------------------------------------------------------
  const detailOverlay  = $("#detail-overlay");
  const detailPanel    = $("#detail-panel");
  const detailClose    = $("#detail-close");
  const detailIcon     = $("#detail-icon");
  const detailName     = $("#detail-name");
  const detailType     = $("#detail-type");
  const detailState    = $("#detail-state");
  const detailDevice   = $("#detail-device");
  const detailTopics   = $("#detail-topics");
  const detailCommands = $("#detail-commands");
  const detailCmdsSection = $("#detail-commands-section");
  const detailQuickSection = $("#detail-quick-section");
  const detailConfig   = $("#detail-config");
  const detailPubTopic = $("#detail-pub-topic");
  const detailPubPayload = $("#detail-pub-payload");
  const detailPubBtn   = $("#detail-pub-btn");
  const detailSubBtn   = $("#detail-sub-btn");
  const detailPubFeedback = $("#detail-pub-feedback");

  let _currentDetailEid = null;

  function openDetailPanel(entityId) {
    const entity = entities[entityId];
    if (!entity) return;
    _currentDetailEid = entityId;

    // Header
    detailIcon.textContent = entityIcon(entity);
    detailName.textContent = entity.name;
    detailType.textContent = `${entity.type} · ${entity.entityId}`;

    // Current state
    if (entity.stateRaw !== null && entity.stateRaw !== undefined) {
      detailState.textContent = typeof entity.stateRaw === "object"
        ? JSON.stringify(entity.stateRaw, null, 2)
        : String(entity.stateRaw);
    } else {
      detailState.textContent = "No state received yet";
    }

    // Device info
    const dev = entity.config?.device || {};
    const serial = entity.deviceSerial || "—";
    detailDevice.innerHTML = [
      row("Name", dev.name || "—"),
      row("Serial", serial),
      row("Model", dev.model || "—"),
      row("Manufacturer", dev.manufacturer || "—"),
      row("Firmware", dev.sw_version || "—"),
    ].join("");

    // Topics
    const topics = [];
    if (entity.configTopic) topics.push(["Config", entity.configTopic]);
    if (entity.stateTopic) topics.push(["State (read)", entity.stateTopic]);
    if (entity.cmdTopic) topics.push(["Command (write)", entity.cmdTopic]);
    if (entity.availTopic) topics.push(["Availability", entity.availTopic]);
    // Extra topics from config
    const cfg = entity.config || {};
    if (cfg.brightness_state_topic) topics.push(["Brightness State", cfg.brightness_state_topic]);
    if (cfg.rgb_state_topic) topics.push(["RGB State", cfg.rgb_state_topic]);
    if (cfg.color_temp_state_topic) topics.push(["Color Temp State", cfg.color_temp_state_topic]);
    if (cfg.json_attributes_topic) topics.push(["Attributes", cfg.json_attributes_topic]);

    detailTopics.innerHTML = topics.map(([label, topic]) =>
      `<tr>
        <td>${escHtml(label)}</td>
        <td class="ha-topic-val" data-topic="${escAttr(topic)}" title="Click to copy">${escHtml(topic)}</td>
        <td><button class="ha-topic-sub" data-topic="${escAttr(topic)}">Subscribe</button></td>
      </tr>`
    ).join("");

    // Bind copy-on-click for topics
    detailTopics.querySelectorAll(".ha-topic-val").forEach((el) => {
      el.addEventListener("click", () => {
        navigator.clipboard.writeText(el.dataset.topic);
        showToast("Copied to clipboard");
      });
    });

    // Bind subscribe buttons on topics
    detailTopics.querySelectorAll(".ha-topic-sub").forEach((btn) => {
      btn.addEventListener("click", () => {
        const t = btn.dataset.topic;
        socket.emit("subscribe", { topic: t });
        addDevLog("SUB", t, "(listening...)");
        btn.textContent = "Subscribed!";
        btn.classList.add("subscribed");
        setTimeout(() => { btn.textContent = "Subscribe"; btn.classList.remove("subscribed"); }, 2000);
        showToast(`Subscribed to ${t}`);
      });
    });

    // Available commands
    const commands = buildCommands(entity);
    if (commands.length > 0) {
      detailCmdsSection.classList.remove("hidden");
      detailCommands.innerHTML = commands.map((cmd) => `
        <div class="ha-cmd-row">
          <span class="ha-cmd-label">${escHtml(cmd.label)}</span>
          <code class="ha-cmd-code" title="Click to copy">${escHtml(cmd.payload)}</code>
          <button class="ha-cmd-send" data-topic="${escAttr(cmd.topic)}" data-payload="${escAttr(cmd.payload)}">Send</button>
        </div>`).join("");

      detailCommands.querySelectorAll(".ha-cmd-code").forEach((el) => {
        el.addEventListener("click", () => {
          navigator.clipboard.writeText(el.textContent);
          showToast("Payload copied");
        });
      });
      detailCommands.querySelectorAll(".ha-cmd-send").forEach((btn) => {
        btn.addEventListener("click", () => {
          const ent = entities[_currentDetailEid];
          if (ent && ent.available === false) { showToast("Device is offline"); return; }
          socket.emit("publish", { topic: btn.dataset.topic, payload: btn.dataset.payload });
          addDevLog("TX", btn.dataset.topic, btn.dataset.payload);
          btn.textContent = "Sent!";
          setTimeout(() => (btn.textContent = "Send"), 1500);
        });
      });
    } else {
      detailCmdsSection.classList.add("hidden");
    }

    // Quick access
    const quickTopic = entity.cmdTopic || entity.stateTopic || entity.availTopic || "";
    if (quickTopic) {
      detailQuickSection.classList.remove("hidden");
      detailPubTopic.value = quickTopic;
      detailPubTopic.readOnly = false;
    } else {
      detailQuickSection.classList.add("hidden");
    }

    // Full config
    detailConfig.textContent = entity.config
      ? JSON.stringify(entity.config, null, 2)
      : "No config available";

    detailOverlay.classList.remove("hidden");
  }

  function buildCommands(entity) {
    const cmds = [];
    if (!entity.cmdTopic) return cmds;
    const t = entity.type;
    const topic = entity.cmdTopic;

    if (t === "switch") {
      cmds.push({ label: "Turn ON", topic, payload: '{"state":"ON"}' });
      cmds.push({ label: "Turn OFF", topic, payload: '{"state":"OFF"}' });
    } else if (t === "light") {
      const modes = entity.config?.supported_color_modes || [];
      const scale = entity.config?.brightness_scale || 255;
      cmds.push({ label: "Turn ON", topic, payload: '{"state":"ON"}' });
      cmds.push({ label: "Turn OFF", topic, payload: '{"state":"OFF"}' });
      if (modes.includes("brightness") || modes.includes("rgb") || entity.config?.brightness) {
        cmds.push({ label: "Brightness 25%", topic, payload: `{"state":"ON","brightness":${Math.round(scale * 0.25)}}` });
        cmds.push({ label: "Brightness 50%", topic, payload: `{"state":"ON","brightness":${Math.round(scale * 0.5)}}` });
        cmds.push({ label: "Brightness 100%", topic, payload: `{"state":"ON","brightness":${scale}}` });
      }
      if (modes.includes("rgb")) {
        cmds.push({ label: "Red", topic, payload: '{"state":"ON","color":{"r":255,"g":0,"b":0}}' });
        cmds.push({ label: "Green", topic, payload: '{"state":"ON","color":{"r":0,"g":255,"b":0}}' });
        cmds.push({ label: "Blue", topic, payload: '{"state":"ON","color":{"r":0,"g":0,"b":255}}' });
        cmds.push({ label: "White", topic, payload: '{"state":"ON","color":{"r":255,"g":255,"b":255}}' });
      }
    } else if (t === "button") {
      cmds.push({ label: "Press", topic, payload: entity.config?.payload_press || "PRESS" });
    }
    return cmds;
  }

  function row(label, value) {
    return `<tr><td>${escHtml(label)}</td><td>${escHtml(value)}</td></tr>`;
  }

  function showToast(msg) {
    const toast = document.createElement("div");
    toast.className = "ha-copied-toast";
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 1500);
  }

  // Close panel
  function closeDetailPanel() {
    detailOverlay.classList.add("hidden");
    _currentDetailEid = null;
  }
  if (detailClose) detailClose.addEventListener("click", closeDetailPanel);
  if (detailOverlay) detailOverlay.addEventListener("click", (e) => {
    if (e.target === detailOverlay) closeDetailPanel();
  });

  // Quick publish inside detail panel
  if (detailPubBtn) detailPubBtn.addEventListener("click", () => {
    const ent = _currentDetailEid ? entities[_currentDetailEid] : null;
    const topic = detailPubTopic.value.trim();
    const payload = detailPubPayload.value.trim();
    if (!topic) return;
    socket.emit("publish", { topic, payload });
    addDevLog("TX", topic, payload);
    detailPubFeedback.classList.remove("hidden", "ok", "err");
    detailPubFeedback.textContent = "Published!";
    detailPubFeedback.classList.add("ok");
    setTimeout(() => detailPubFeedback.classList.add("hidden"), 2000);
  });

  // Quick subscribe inside detail panel
  if (detailSubBtn) detailSubBtn.addEventListener("click", () => {
    const topic = detailPubTopic.value.trim();
    if (!topic) return;
    socket.emit("subscribe", { topic });
    addDevLog("SUB", topic, "(listening...)");
    detailPubFeedback.classList.remove("hidden", "ok", "err");
    detailPubFeedback.textContent = `Subscribed to ${topic}`;
    detailPubFeedback.classList.add("ok");
    setTimeout(() => detailPubFeedback.classList.add("hidden"), 3000);
  });

  // Update detail panel state when entity updates
  socket.on("device_update", (msg) => {
    if (_currentDetailEid) {
      const eid = _stateTopicMap[msg.topic];
      if (eid === _currentDetailEid) {
        const payload = msg.payload;
        detailState.textContent = typeof payload === "object"
          ? JSON.stringify(payload, null, 2)
          : String(payload);
      }
    }
  });

  // -------------------------------------------------------
  // Make entity rows clickable → open detail
  // -------------------------------------------------------
  function bindEntityClicks(container) {
    if (!container) return;
    container.querySelectorAll(".ha-entity-row[data-eid]").forEach((row) => {
      row.style.cursor = "pointer";
      row.addEventListener("click", (e) => {
        // Don't open panel if clicking a toggle, slider, or color picker
        if (e.target.closest(".ha-toggle") || e.target.closest(".ha-brightness-slider") || e.target.closest(".ha-color-picker")) return;
        openDetailPanel(row.dataset.eid);
      });
    });
  }

  // -------------------------------------------------------
  // API Details Tab
  // -------------------------------------------------------
  const apiCodeEl = $("#api-code-examples");
  const apiEntTable = $("#api-entities-table");
  const apiBroker = $("#api-broker");
  const apiPort = $("#api-port");
  const apiPrefix = $("#api-prefix");

  // Fill connection info once MQTT connects
  socket.on("mqtt_status", (data) => {
    if (data.connected) {
      if (apiBroker) apiBroker.textContent = "egycad.com";
      if (apiPort) apiPort.textContent = "1883";
      if (apiPrefix) apiPrefix.textContent = "homeassistant";
    }
  });

  function getCodeExamples(lang) {
    const broker = "egycad.com";
    const port = "1883";
    const email = document.getElementById("login-email")?.value || "your@email.com";
    // Pick a real command topic if available
    let exTopic = "homeassistant/switch/ACCOUNT_ID/SERIAL_CHANNEL/set";
    let exState = "homeassistant/switch/ACCOUNT_ID/SERIAL_CHANNEL/state";
    let exAvail = "homeassistant/switch/ACCOUNT_ID/SERIAL_CHANNEL/availability";
    let exConfig = "homeassistant/switch/ACCOUNT_ID/SERIAL_CHANNEL/config";
    for (const eid in entities) {
      const e = entities[eid];
      if (e.cmdTopic) {
        exTopic = e.cmdTopic;
        exState = e.stateTopic || exTopic.replace("/set", "/state");
        exAvail = e.availTopic || exTopic.replace("/set", "/availability");
        exConfig = e.configTopic || exTopic.replace("/set", "/config");
        break;
      }
    }

    const examples = {
      python: `# pip install paho-mqtt requests

import json
import paho.mqtt.client as mqtt
import requests

# Step 1: Login to get MQTT broker details
resp = requests.post("https://egycad.com/apis/cadio/login", json={
    "email": "${email}",
    "password": "YOUR_PASSWORD"
})
config = resp.json()
print(f"Broker: {config['mqtt_host']}:{config['mqtt_port']}")

# Step 2: Connect to MQTT
def on_connect(client, userdata, flags, rc):
    print(f"Connected: {rc}")
    # Subscribe to read topics
    client.subscribe("${exState}")        # Current state (JSON)
    client.subscribe("${exAvail}")        # Availability (YES/NO)
    client.subscribe("${exConfig}")       # Discovery config (JSON)

def on_message(client, userdata, msg):
    payload = msg.payload.decode()
    print(f"{msg.topic} -> {payload}")
    # Availability is a plain string: YES or NO
    if msg.topic.endswith("/availability"):
        is_online = payload == "YES"
        print(f"  Device online: {is_online}")
    # State and config are JSON
    else:
        try:
            data = json.loads(payload)
            print(f"  Parsed: {data}")
        except json.JSONDecodeError:
            pass

client = mqtt.Client("my-app")
client.username_pw_set("${email}", "YOUR_PASSWORD")
client.on_connect = on_connect
client.on_message = on_message
client.connect("${broker}", ${port}, 60)
client.loop_start()

# Step 3: Publish a command (write to /set topic)
# Turn ON
client.publish("${exTopic}", json.dumps({"state": "ON"}))

# Set brightness (lights only)
client.publish("${exTopic}", json.dumps({"state": "ON", "brightness": 75}))

# Set RGB color (RGB lights only)
client.publish("${exTopic}", json.dumps({
    "state": "ON",
    "color": {"r": 255, "g": 0, "b": 128}
}))

# Turn OFF
client.publish("${exTopic}", json.dumps({"state": "OFF"}))`,

      javascript: `// npm install mqtt

const mqtt = require('mqtt');

// Step 1: Login
const resp = await fetch('https://egycad.com/apis/cadio/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    email: '${email}',
    password: 'YOUR_PASSWORD'
  })
});
const config = await resp.json();
console.log(\`Broker: \${config.mqtt_host}:\${config.mqtt_port}\`);

// Step 2: Connect
const client = mqtt.connect('mqtt://${broker}:${port}', {
  username: '${email}',
  password: 'YOUR_PASSWORD'
});

client.on('connect', () => {
  console.log('Connected');
  client.subscribe('${exState}');
});

client.on('message', (topic, message) => {
  console.log(\`\${topic} -> \${message.toString()}\`);
});

// Step 3: Publish commands
client.publish('${exTopic}', JSON.stringify({ state: 'ON' }));
client.publish('${exTopic}', JSON.stringify({ state: 'ON', brightness: 75 }));
client.publish('${exTopic}', JSON.stringify({
  state: 'ON', color: { r: 255, g: 0, b: 128 }
}));
client.publish('${exTopic}', JSON.stringify({ state: 'OFF' }));`,

      java: `// Add dependency: org.eclipse.paho:org.eclipse.paho.client.mqttv3:1.2.5

import org.eclipse.paho.client.mqttv3.*;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;

public class NivixsaClient {
    public static void main(String[] args) throws Exception {
        // Step 1: Login (use HttpURLConnection or OkHttp)
        // POST https://egycad.com/apis/cadio/login
        // Body: {"email":"${email}","password":"YOUR_PASSWORD"}

        // Step 2: Connect to MQTT
        String broker = "tcp://${broker}:${port}";
        MqttClient client = new MqttClient(broker, "java-app", new MemoryPersistence());

        MqttConnectOptions opts = new MqttConnectOptions();
        opts.setUserName("${email}");
        opts.setPassword("YOUR_PASSWORD".toCharArray());
        client.connect(opts);

        // Subscribe to read topics
        client.subscribe("${exState}", (topic, msg) -> {
            System.out.println("State: " + topic + " -> " + new String(msg.getPayload()));
        });
        client.subscribe("${exAvail}", (topic, msg) -> {
            String payload = new String(msg.getPayload());
            System.out.println("Availability: " + payload); // YES or NO
        });
        client.subscribe("${exConfig}", (topic, msg) -> {
            System.out.println("Config: " + new String(msg.getPayload()));
        });

        // Step 3: Publish commands (write to /set topic)
        // Turn ON
        client.publish("${exTopic}",
            new MqttMessage("{\\"state\\":\\"ON\\"}".getBytes()));

        // Set brightness
        client.publish("${exTopic}",
            new MqttMessage("{\\"state\\":\\"ON\\",\\"brightness\\":75}".getBytes()));

        // Set color
        client.publish("${exTopic}",
            new MqttMessage("{\\"state\\":\\"ON\\",\\"color\\":{\\"r\\":255,\\"g\\":0,\\"b\\":128}}".getBytes()));

        // Turn OFF
        client.publish("${exTopic}",
            new MqttMessage("{\\"state\\":\\"OFF\\"}".getBytes()));
    }
}`,

      csharp: `// NuGet: MQTTnet

using MQTTnet;
using MQTTnet.Client;
using System.Text;
using System.Text.Json;

// Step 1: Login
var http = new HttpClient();
var loginResp = await http.PostAsync("https://egycad.com/apis/cadio/login",
    new StringContent(
        JsonSerializer.Serialize(new { email = "${email}", password = "YOUR_PASSWORD" }),
        Encoding.UTF8, "application/json"));
var config = JsonSerializer.Deserialize<JsonElement>(await loginResp.Content.ReadAsStringAsync());

// Step 2: Connect
var factory = new MqttFactory();
var client = factory.CreateMqttClient();
var options = new MqttClientOptionsBuilder()
    .WithTcpServer("${broker}", ${port})
    .WithCredentials("${email}", "YOUR_PASSWORD")
    .Build();

client.ApplicationMessageReceivedAsync += e => {
    var topic = e.ApplicationMessage.Topic;
    var payload = Encoding.UTF8.GetString(e.ApplicationMessage.PayloadSegment);
    Console.WriteLine($"{topic} -> {payload}");
    // Availability is plain text: YES or NO
    if (topic.EndsWith("/availability"))
        Console.WriteLine($"  Online: {payload == "YES"}");
    return Task.CompletedTask;
};

await client.ConnectAsync(options);
// Subscribe to read topics
await client.SubscribeAsync("${exState}");        // Current state (JSON)
await client.SubscribeAsync("${exAvail}");        // Availability (YES/NO)
await client.SubscribeAsync("${exConfig}");       // Discovery config (JSON)

// Step 3: Publish commands (write to /set topic)
await client.PublishStringAsync("${exTopic}", "{\\"state\\":\\"ON\\"}");
await client.PublishStringAsync("${exTopic}", "{\\"state\\":\\"ON\\",\\"brightness\\":75}");
await client.PublishStringAsync("${exTopic}", "{\\"state\\":\\"OFF\\"}");`,

      curl: `# Step 1: Login to get MQTT broker details
curl -X POST https://egycad.com/apis/cadio/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"${email}","password":"YOUR_PASSWORD"}'

# Response:
# {"email":"...","success":true,"mqtt_host":"egycad.com","mqtt_port":1883,"discovery_prefix":"homeassistant"}

# ── READ: Subscribe to topics (mosquitto_sub) ──

# Read current state (JSON: {"state":"ON","brightness":75})
mosquitto_sub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exState}"

# Read availability (plain text: YES or NO)
mosquitto_sub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exAvail}"

# Read discovery config (full entity configuration JSON)
mosquitto_sub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exConfig}"

# ── WRITE: Publish commands (mosquitto_pub) ──

# Turn ON
mosquitto_pub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exTopic}" \\
  -m '{"state":"ON"}'

# Set brightness
mosquitto_pub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exTopic}" \\
  -m '{"state":"ON","brightness":75}'

# Set RGB color
mosquitto_pub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exTopic}" \\
  -m '{"state":"ON","color":{"r":255,"g":0,"b":128}}'

# Turn OFF
mosquitto_pub -h ${broker} -p ${port} \\
  -u "${email}" -P "YOUR_PASSWORD" \\
  -t "${exTopic}" \\
  -m '{"state":"OFF"}'`,

      go: `// go get github.com/eclipse/paho.mqtt.golang

package main

import (
    "encoding/json"
    "fmt"
    "strings"
    mqtt "github.com/eclipse/paho.mqtt.golang"
)

func main() {
    // Step 1: Login (use net/http POST to https://egycad.com/apis/cadio/login)

    // Step 2: Connect
    opts := mqtt.NewClientOptions().
        AddBroker("tcp://${broker}:${port}").
        SetUsername("${email}").
        SetPassword("YOUR_PASSWORD").
        SetClientID("go-app")

    client := mqtt.NewClient(opts)
    if token := client.Connect(); token.Wait() && token.Error() != nil {
        panic(token.Error())
    }

    handler := func(c mqtt.Client, m mqtt.Message) {
        payload := string(m.Payload())
        fmt.Printf("%s -> %s\\n", m.Topic(), payload)
        // Availability is plain text: YES or NO
        if strings.HasSuffix(m.Topic(), "/availability") {
            fmt.Printf("  Online: %v\\n", payload == "YES")
        }
    }

    // Subscribe to read topics
    client.Subscribe("${exState}", 0, handler)        // Current state (JSON)
    client.Subscribe("${exAvail}", 0, handler)        // Availability (YES/NO)
    client.Subscribe("${exConfig}", 0, handler)       // Discovery config (JSON)

    // Step 3: Publish commands (write to /set topic)
    // Turn ON
    cmd, _ := json.Marshal(map[string]interface{}{"state": "ON"})
    client.Publish("${exTopic}", 0, false, cmd)

    // Brightness
    cmd, _ = json.Marshal(map[string]interface{}{"state": "ON", "brightness": 75})
    client.Publish("${exTopic}", 0, false, cmd)

    // RGB Color
    cmd, _ = json.Marshal(map[string]interface{}{
        "state": "ON",
        "color": map[string]int{"r": 255, "g": 0, "b": 128},
    })
    client.Publish("${exTopic}", 0, false, cmd)

    // Turn OFF
    cmd, _ = json.Marshal(map[string]interface{}{"state": "OFF"})
    client.Publish("${exTopic}", 0, false, cmd)
}`,
    };
    return examples[lang] || "";
  }

  // Language tab switching
  let _currentLang = "python";
  function renderApiCode() {
    if (!apiCodeEl) return;
    const code = getCodeExamples(_currentLang);
    apiCodeEl.innerHTML = `<pre class="ha-detail-code ha-api-code">${escHtml(code)}</pre>`;
  }

  document.querySelectorAll(".ha-lang-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".ha-lang-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      _currentLang = tab.dataset.lang;
      renderApiCode();
    });
  });

  // Render entities table for API tab
  function renderApiEntitiesTable() {
    if (!apiEntTable) return;
    const all = Object.values(entities).filter((e) => e.cmdTopic || e.stateTopic);
    if (all.length === 0) {
      apiEntTable.innerHTML = '<div class="ha-empty-row">No entities discovered yet</div>';
      return;
    }
    const monoStyle = "font-family:'Roboto Mono',monospace;font-size:11px";
    apiEntTable.innerHTML = `<table class="ha-log-table">
      <thead><tr><th>Name</th><th>Type</th><th>Device</th><th>Command (/set)</th><th>State (/state)</th><th>Availability</th></tr></thead>
      <tbody>${all.map((e) => `<tr>
        <td style="color:var(--ha-text)">${escHtml(e.name)}</td>
        <td>${escHtml(e.type)}</td>
        <td>${escHtml(e.deviceSerial || "—")}</td>
        <td style="${monoStyle};color:var(--ha-primary)">${escHtml(e.cmdTopic || "—")}</td>
        <td style="${monoStyle};color:var(--ha-green)">${escHtml(e.stateTopic || "—")}</td>
        <td style="${monoStyle};color:var(--ha-yellow)">${escHtml(e.availTopic || "—")}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  }

  // Render API tab on navigation
  const origNavClick = navItems;
  navItems.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "api") {
        renderApiCode();
        renderApiEntitiesTable();
      }
    });
  });

  // Patch render functions to bind clicks after rendering
  const _origRenderOverview = renderOverviewByDevice;
  const _origRenderTypeTab = renderTypeTabByDevice;

})();
