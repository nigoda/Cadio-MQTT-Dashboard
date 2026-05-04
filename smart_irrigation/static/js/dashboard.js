/**
 * Smart Irrigation Dashboard — Frontend Logic
 */

// SocketIO connection
const socket = io();
let automations = [];
let switches = {};
let sensors = {};
let selectedAutoId = null;
let editingAutoId = null; // null = new, string = editing

// ===== Tab Navigation =====
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    item.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + item.dataset.tab).classList.add('active');
  });
});

// ===== SocketIO Events =====
socket.on('connect', () => { console.log('[WS] Connected'); });
socket.on('disconnect', () => { setMqttStatus(false); });

socket.on('mqtt_status', data => { setMqttStatus(data.connected); });

socket.on('full_state', data => {
  switches = data.switches || {};
  sensors = data.sensors || {};
  automations = data.automations || [];
  renderAll();
});

socket.on('automations_update', data => {
  automations = data;
  renderAll();
});

socket.on('state_update', data => {
  if (data.type && ['switch','light','lock','fan','cover'].includes(data.type)) {
    switches[data.topic] = data.value;
  } else {
    sensors[data.topic] = data.value;
  }
  renderSwitches();
  renderSensors();
});

function setMqttStatus(connected) {
  const dot = document.getElementById('mqtt-dot');
  const label = document.getElementById('mqtt-label');
  dot.classList.toggle('connected', connected);
  label.textContent = connected ? 'MQTT Connected' : 'Disconnected';
}

// ===== Render Functions =====
function renderAll() {
  renderDashboard();
  renderAutoList();
  renderAutoDetail();
  renderSwitches();
  renderSensors();
  renderSystem();
  renderLogs();
}

function renderDashboard() {
  const running = automations.filter(a => ['ACTION_SET','ACTION_VERIFY','ACTION_RUN','ACTION_REVERT','ACTION_VERIFY_REVERT','BUFFER','INIT_SET','INIT_VERIFY'].includes(a.runtime.state));
  const paused = automations.filter(a => a.runtime.state.startsWith('PAUSED'));
  const errors = automations.filter(a => a.runtime.state === 'ERROR' || a.runtime.state.startsWith('ERROR_'));

  document.getElementById('sum-automations').textContent = automations.length;
  document.getElementById('sum-running').textContent = running.length;
  document.getElementById('sum-paused').textContent = paused.length;
  document.getElementById('sum-errors').textContent = errors.length;
  document.getElementById('sum-switches').textContent = Object.keys(switches).length;
  document.getElementById('sum-sensors').textContent = Object.keys(sensors).length;

  // Active automations mini list
  const active = automations.filter(a => a.status && a.runtime.state !== 'IDLE');
  const list = document.getElementById('dash-active-list');
  if (!active.length) {
    list.innerHTML = '<p class="empty-msg">No automations running</p>';
  } else {
    list.innerHTML = active.map(a => `
      <div class="auto-mini-item">
        <span class="auto-status-dot ${getStatusClass(a)}"></span>
        <span class="mini-name">${esc(a.name)}</span>
        <span class="mini-state ${getStateColorClass(a)}">${a.runtime.state}</span>
      </div>
    `).join('');
  }
}

function renderAutoList() {
  const list = document.getElementById('auto-list');
  if (!automations.length) {
    list.innerHTML = '<p class="empty-msg">No automations yet</p>';
    return;
  }
  list.innerHTML = automations.map(a => `
    <div class="auto-list-item ${a.id === selectedAutoId ? 'selected' : ''}" data-id="${a.id}">
      <span class="auto-status-dot ${getStatusClass(a)}"></span>
      <span class="auto-list-name">${esc(a.name)}</span>
      <span class="auto-list-state">${shortState(a)}</span>
    </div>
  `).join('');

  list.querySelectorAll('.auto-list-item').forEach(el => {
    el.addEventListener('click', () => {
      selectedAutoId = el.dataset.id;
      renderAutoList();
      renderAutoDetail();
    });
  });
}

function renderAutoDetail() {
  const empty = document.getElementById('auto-detail-empty');
  const detail = document.getElementById('auto-detail');

  if (!selectedAutoId) {
    empty.style.display = 'flex';
    detail.style.display = 'none';
    return;
  }

  const auto = automations.find(a => a.id === selectedAutoId);
  if (!auto) { empty.style.display = 'flex'; detail.style.display = 'none'; return; }

  empty.style.display = 'none';
  detail.style.display = 'flex';

  const rt = auto.runtime;
  const progress = calcProgress(auto);

  detail.innerHTML = `
    <div class="detail-header">
      <h3>${esc(auto.name)}</h3>
      <div class="detail-controls">
        <span class="detail-section-state ${getStateColorClass(auto)}">${rt.state}</span>
        <label class="toggle">
          <input type="checkbox" ${auto.status ? 'checked' : ''} onchange="toggleAuto('${auto.id}', this.checked)">
          <span class="toggle-slider"></span>
        </label>
        <button class="btn btn-icon" onclick="resetAuto('${auto.id}')" title="Reset">&#8635;</button>
        <button class="btn btn-icon" onclick="editAuto('${auto.id}')" title="Edit">&#9998;</button>
        <button class="btn btn-icon" onclick="deleteAuto('${auto.id}')" title="Delete" style="color:var(--red)">&#128465;</button>
      </div>
    </div>

    ${renderProgressSection(auto, progress)}
    ${renderScheduleSection(auto)}
    ${renderConditionSection(auto)}
    ${renderInitSection(auto)}
    ${renderActionsSection(auto)}
    ${renderErrorSection(auto)}
    ${renderDetailLogs(auto)}
  `;
}

function renderProgressSection(auto, progress) {
  const rt = auto.runtime;
  const actionInfo = auto.actions.length ?
    `Action ${Math.min(rt.current_action_index + 1, auto.actions.length)} of ${auto.actions.length}` : 'No actions';

  return `
    <div class="detail-section">
      <h4>Progress</h4>
      <div style="display:flex;justify-content:space-between;font-size:.82rem;margin-bottom:4px">
        <span>${actionInfo}</span>
        <span>${progress}%</span>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div>
    </div>
  `;
}

function renderScheduleSection(auto) {
  const sched = auto.schedule || {};
  const days = sched.days || [];
  const allDays = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

  return `
    <div class="detail-section">
      <h4>Schedule</h4>
      <div class="sched-days">
        ${allDays.map(d => `<span class="sched-day ${days.includes(d) ? 'active' : ''}">${d[0]}</span>`).join('')}
      </div>
      <div class="sched-time">${sched.startTime || '00:00'} → ${sched.endTime || '23:59'}</div>
    </div>
  `;
}

function renderConditionSection(auto) {
  if (!auto.conditions || !auto.conditions.length) return '';
  return `
    <div class="detail-section">
      <h4>Conditions</h4>
      ${auto.conditions.map(c => {
        const actual = sensors[c.sensor] || switches[c.sensor] || '--';
        const met = checkCondition(actual, c.operator, c.value);
        return `<div class="cond-item">
          <span class="cond-dot ${met ? 'met' : 'unmet'}"></span>
          <span>${esc(c.sensor.split('/').pop())} ${c.operator || '='} ${esc(c.value)}</span>
          <span style="margin-left:auto;font-size:.72rem;color:var(--text-dim)">(${esc(actual)})</span>
        </div>`;
      }).join('')}
    </div>
  `;
}

function renderInitSection(auto) {
  if (!auto.initialization || !auto.initialization.length) return '';
  return `
    <div class="detail-section">
      <h4>Initialization</h4>
      ${auto.initialization.map(i => {
        const actual = switches[i.switch] || '--';
        const ok = actual.toUpperCase() === i.state.toUpperCase();
        return `<div class="action-item ${ok ? 'done' : ''}">
          <span class="action-icon">${ok ? '&#10004;' : '&#9679;'}</span>
          <span class="action-text">${esc(i.switch.split('/').pop())} → ${i.state}</span>
          <span class="action-time">(${actual})</span>
        </div>`;
      }).join('')}
    </div>
  `;
}

function renderActionsSection(auto) {
  if (!auto.actions || !auto.actions.length) return '';
  const rt = auto.runtime;
  return `
    <div class="detail-section">
      <h4>Actions</h4>
      ${auto.actions.map((a, idx) => {
        let cls = '';
        if (idx < rt.current_action_index) cls = 'done';
        else if (idx === rt.current_action_index && ['ACTION_SET','ACTION_VERIFY','ACTION_RUN','ACTION_REVERT','ACTION_VERIFY_REVERT'].includes(rt.state)) cls = 'active';
        const icon = cls === 'done' ? '&#10004;' : cls === 'active' ? '&#9654;' : '&#9679;';
        return `<div class="action-item ${cls}">
          <span class="action-icon">${icon}</span>
          <span class="action-text">${esc(a.switch.split('/').pop())} → ${a.state}</span>
          <span class="action-time">${formatDuration(a.duration)}</span>
        </div>`;
      }).join('')}
    </div>
  `;
}

function renderErrorSection(auto) {
  if (!auto.error_state || !auto.error_state.length) return '';
  const isError = auto.runtime.state === 'ERROR' || auto.runtime.state.startsWith('ERROR_');
  return `
    <div class="detail-section" style="${isError ? 'border-color:var(--red)' : ''}">
      <h4>Error State (Fail-Safe) ${isError ? '<span style="color:var(--red)">ACTIVE</span>' : ''}</h4>
      ${auto.error_state.map(e => `<div class="action-item">
        <span class="action-icon">&#9888;</span>
        <span class="action-text">${esc(e.switch.split('/').pop())} → ${e.state}</span>
      </div>`).join('')}
    </div>
  `;
}

function renderDetailLogs(auto) {
  const logs = (auto.logs || []).slice(-20).reverse();
  if (!logs.length) return '';
  return `
    <div class="detail-section">
      <h4>Activity Log</h4>
      ${logs.map(l => `<div class="log-entry"><span class="log-ts">${l.ts}</span>${esc(l.msg)}</div>`).join('')}
    </div>
  `;
}

function renderSwitches() {
  const grid = document.getElementById('switch-grid');
  const entries = Object.entries(switches);
  if (!entries.length) { grid.innerHTML = '<p class="empty-msg">No switches discovered</p>'; return; }
  grid.innerHTML = entries.map(([topic, val]) => {
    const info = findDevice(topic);
    const name = info ? info.name : topic.split('/').pop();
    const isOn = val === 'ON';
    return `<div class="device-card">
      <div class="device-name">${esc(name)}</div>
      <div class="device-topic">${esc(topic)}</div>
      <div class="device-value" style="color:${isOn ? 'var(--green)' : 'var(--text-dim)'}">${val}</div>
      <div class="device-toggle">
        <label class="toggle">
          <input type="checkbox" ${isOn ? 'checked' : ''} onchange="setSwitch('${esc(topic)}', this.checked ? 'ON' : 'OFF')">
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>`;
  }).join('');
}

function renderSensors() {
  const grid = document.getElementById('sensor-grid');
  const entries = Object.entries(sensors);
  if (!entries.length) { grid.innerHTML = '<p class="empty-msg">No sensors discovered</p>'; return; }
  grid.innerHTML = entries.map(([topic, val]) => {
    const info = findDevice(topic);
    const name = info ? info.name : topic.split('/').pop();
    return `<div class="device-card">
      <div class="device-name">${esc(name)}</div>
      <div class="device-topic">${esc(topic)}</div>
      <div class="device-value">${esc(val)}</div>
    </div>`;
  }).join('');
}

function renderSystem() {
  fetch('/api/status').then(r => r.json()).then(d => {
    document.getElementById('sys-mqtt').textContent = d.mqtt ? 'Connected' : 'Disconnected';
    document.getElementById('sys-broker').textContent = `${d.broker.broker}:${d.broker.port}`;
    document.getElementById('sys-switches').textContent = d.switches;
    document.getElementById('sys-sensors').textContent = d.sensors;
    document.getElementById('sys-autos').textContent = d.automations;
  }).catch(() => {});
}

function renderLogs() {
  const container = document.getElementById('log-container');
  const allLogs = [];
  automations.forEach(a => {
    (a.logs || []).forEach(l => allLogs.push({ ...l, name: a.name }));
  });
  allLogs.sort((a, b) => b.ts.localeCompare(a.ts));
  const recent = allLogs.slice(0, 100);
  if (!recent.length) { container.innerHTML = '<p class="empty-msg">No logs yet</p>'; return; }
  container.innerHTML = recent.map(l =>
    `<div class="log-entry"><span class="log-ts">${l.ts}</span><span class="log-name">[${esc(l.name)}]</span>${esc(l.msg)}</div>`
  ).join('');
}

// ===== API Actions =====
function toggleAuto(id, on) {
  fetch(`/api/automations/${id}/toggle`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ status: on })
  });
}

function resetAuto(id) {
  if (!confirm('Reset this automation?')) return;
  fetch(`/api/automations/${id}/reset`, { method: 'POST' });
}

function deleteAuto(id) {
  if (!confirm('Delete this automation?')) return;
  fetch(`/api/automations/${id}`, { method: 'DELETE' }).then(() => {
    selectedAutoId = null;
    fetchAutomations();
  });
}

function setSwitch(topic, state) {
  fetch(`/api/switches/${encodeURIComponent(topic)}/set`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ state })
  });
}

function fetchAutomations() {
  fetch('/api/automations').then(r => r.json()).then(data => {
    automations = data;
    renderAll();
  });
}

// ===== Modal / Create-Edit =====
document.getElementById('btn-new-auto').addEventListener('click', () => openModal(null));
document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-cancel').addEventListener('click', closeModal);
document.getElementById('modal-save').addEventListener('click', saveModal);
document.getElementById('btn-add-cond').addEventListener('click', () => addCondRow());
document.getElementById('btn-add-init').addEventListener('click', () => addInitRow());
document.getElementById('btn-add-action').addEventListener('click', () => addActionRow());
document.getElementById('btn-add-error').addEventListener('click', () => addErrorRow());
document.getElementById('btn-clear-logs').addEventListener('click', () => {
  document.getElementById('log-container').innerHTML = '<p class="empty-msg">Cleared</p>';
});

// Day picker
document.querySelectorAll('.day-btn').forEach(btn => {
  btn.addEventListener('click', () => btn.classList.toggle('active'));
});

function openModal(autoId) {
  editingAutoId = autoId;
  document.getElementById('modal-title').textContent = autoId ? 'Edit Automation' : 'New Automation';

  // Clear form
  document.getElementById('auto-name').value = '';
  document.querySelectorAll('.day-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('sched-start').value = '06:00';
  document.getElementById('sched-end').value = '09:00';
  document.getElementById('cond-list').innerHTML = '';
  document.getElementById('init-list').innerHTML = '';
  document.getElementById('action-list').innerHTML = '';
  document.getElementById('error-list').innerHTML = '';
  document.getElementById('buffer-time').value = '5';

  // Fill if editing
  if (autoId) {
    const auto = automations.find(a => a.id === autoId);
    if (auto) {
      document.getElementById('auto-name').value = auto.name;
      (auto.schedule.days || []).forEach(d => {
        const btn = document.querySelector(`.day-btn[data-day="${d}"]`);
        if (btn) btn.classList.add('active');
      });
      document.getElementById('sched-start').value = auto.schedule.startTime || '06:00';
      document.getElementById('sched-end').value = auto.schedule.endTime || '09:00';
      document.getElementById('buffer-time').value = auto.buffer_time || 5;
      (auto.conditions || []).forEach(c => addCondRow(c));
      (auto.initialization || []).forEach(i => addInitRow(i));
      (auto.actions || []).forEach(a => addActionRow(a));
      (auto.error_state || []).forEach(e => addErrorRow(e));
    }
  }

  document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
  editingAutoId = null;
}

function saveModal() {
  const name = document.getElementById('auto-name').value.trim();
  if (!name) { alert('Name is required'); return; }

  const days = [];
  document.querySelectorAll('.day-btn.active').forEach(b => days.push(b.dataset.day));

  const data = {
    name,
    schedule: {
      days,
      startTime: document.getElementById('sched-start').value,
      endTime: document.getElementById('sched-end').value,
    },
    conditions: getConditions(),
    initialization: getInitList(),
    actions: getActionList(),
    error_state: getErrorList(),
    buffer_time: parseInt(document.getElementById('buffer-time').value) || 5,
  };

  const url = editingAutoId ? `/api/automations/${editingAutoId}` : '/api/automations';
  const method = editingAutoId ? 'PUT' : 'POST';

  fetch(url, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  }).then(r => r.json()).then(d => {
    if (d.ok !== false) {
      closeModal();
      fetchAutomations();
    } else {
      alert(d.msg || 'Error saving');
    }
  });
}

function editAuto(id) { openModal(id); }

// ===== Dynamic Rows =====
function addCondRow(data) {
  const list = document.getElementById('cond-list');
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.innerHTML = `
    <select class="cond-logic"><option value="AND">AND</option><option value="OR">OR</option></select>
    <input type="text" class="cond-sensor" placeholder="sensor topic" value="${esc(data?.sensor || '')}">
    <select class="cond-op">
      <option value="=">=</option><option value="!=">!=</option>
      <option value=">">&gt;</option><option value="<">&lt;</option>
      <option value=">=">&gt;=</option><option value="<=">&lt;=</option>
    </select>
    <input type="text" class="cond-value" placeholder="value" value="${esc(data?.value || '')}">
    <button class="row-remove" onclick="this.parentElement.remove()">&times;</button>
  `;
  if (data?.logic) row.querySelector('.cond-logic').value = data.logic;
  if (data?.operator) row.querySelector('.cond-op').value = data.operator;
  list.appendChild(row);
}

function addInitRow(data) {
  const list = document.getElementById('init-list');
  const row = document.createElement('div');
  row.className = 'action-row';
  row.innerHTML = `
    <input type="text" class="sw-topic" placeholder="switch topic" value="${esc(data?.switch || '')}">
    <select class="sw-state"><option value="ON">ON</option><option value="OFF">OFF</option></select>
    <button class="row-remove" onclick="this.parentElement.remove()">&times;</button>
  `;
  if (data?.state) row.querySelector('.sw-state').value = data.state.toUpperCase();
  list.appendChild(row);
}

function addActionRow(data) {
  const list = document.getElementById('action-list');
  const row = document.createElement('div');
  row.className = 'action-row';
  row.innerHTML = `
    <input type="text" class="sw-topic" placeholder="switch topic" value="${esc(data?.switch || '')}">
    <select class="sw-state"><option value="ON">ON</option><option value="OFF">OFF</option></select>
    <input type="number" class="sw-duration" placeholder="sec" min="1" value="${data?.duration || 60}">
    <button class="row-remove" onclick="this.parentElement.remove()">&times;</button>
  `;
  if (data?.state) row.querySelector('.sw-state').value = data.state.toUpperCase();
  list.appendChild(row);
}

function addErrorRow(data) {
  const list = document.getElementById('error-list');
  const row = document.createElement('div');
  row.className = 'action-row';
  row.innerHTML = `
    <input type="text" class="sw-topic" placeholder="switch topic" value="${esc(data?.switch || '')}">
    <select class="sw-state"><option value="ON">ON</option><option value="OFF">OFF</option></select>
    <button class="row-remove" onclick="this.parentElement.remove()">&times;</button>
  `;
  if (data?.state) row.querySelector('.sw-state').value = data.state.toUpperCase();
  list.appendChild(row);
}

function getConditions() {
  const rows = document.querySelectorAll('#cond-list .cond-row');
  return Array.from(rows).map(r => ({
    logic: r.querySelector('.cond-logic').value,
    sensor: r.querySelector('.cond-sensor').value.trim(),
    operator: r.querySelector('.cond-op').value,
    value: r.querySelector('.cond-value').value.trim(),
  })).filter(c => c.sensor);
}

function getInitList() {
  const rows = document.querySelectorAll('#init-list .action-row');
  return Array.from(rows).map(r => ({
    switch: r.querySelector('.sw-topic').value.trim(),
    state: r.querySelector('.sw-state').value,
  })).filter(i => i.switch);
}

function getActionList() {
  const rows = document.querySelectorAll('#action-list .action-row');
  return Array.from(rows).map(r => ({
    switch: r.querySelector('.sw-topic').value.trim(),
    state: r.querySelector('.sw-state').value,
    duration: parseInt(r.querySelector('.sw-duration').value) || 60,
  })).filter(a => a.switch);
}

function getErrorList() {
  const rows = document.querySelectorAll('#error-list .action-row');
  return Array.from(rows).map(r => ({
    switch: r.querySelector('.sw-topic').value.trim(),
    state: r.querySelector('.sw-state').value,
  })).filter(e => e.switch);
}

// ===== Helpers =====
function esc(s) { if (!s) return ''; return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function getStatusClass(auto) {
  const s = auto.runtime.state;
  if (s === 'ERROR' || s.startsWith('ERROR_')) return 'error';
  if (s.startsWith('PAUSED')) return 'paused';
  if (s === 'COMPLETED') return 'completed';
  if (!auto.status || s === 'IDLE') return 'off';
  return 'running';
}

function getStateColorClass(auto) {
  const cls = getStatusClass(auto);
  return 'state-' + cls;
}

function shortState(auto) {
  const s = auto.runtime.state;
  if (!auto.status) return 'OFF';
  if (s === 'WAIT_CONDITION') return 'WAIT';
  if (s.startsWith('ACTION_')) return 'RUN';
  if (s.startsWith('INIT_')) return 'INIT';
  if (s === 'PAUSED_CONDITION') return 'PAUSE';
  if (s === 'PAUSED_SCHEDULE') return 'SCHED';
  if (s === 'COMPLETED') return 'DONE';
  if (s === 'ERROR') return 'ERR';
  if (s === 'BUFFER') return 'BUF';
  return s.substring(0, 4);
}

function calcProgress(auto) {
  if (!auto.actions.length) return 0;
  const idx = auto.runtime.current_action_index;
  return Math.round((idx / auto.actions.length) * 100);
}

function formatDuration(sec) {
  if (!sec) return '--';
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.round(sec / 60) + 'min';
  return (sec / 3600).toFixed(1) + 'h';
}

function checkCondition(actual, op, expected) {
  const a = (actual || '').trim().toUpperCase();
  const e = (expected || '').trim().toUpperCase();
  if (op === '=' || op === '==') return a === e;
  if (op === '!=') return a !== e;
  try {
    const av = parseFloat(actual), ev = parseFloat(expected);
    if (op === '>') return av > ev;
    if (op === '<') return av < ev;
    if (op === '>=') return av >= ev;
    if (op === '<=') return av <= ev;
  } catch(e) {}
  return a === e;
}

function findDevice(topic) {
  // Simple lookup — will be populated when devices API is available
  return null;
}

// ===== Initial load =====
fetchAutomations();
fetch('/api/devices').then(r => r.json()).then(d => {
  (d.switches || []).forEach(s => { switches[s.state_topic] = s.value; });
  (d.sensors || []).forEach(s => { sensors[s.state_topic] = s.value; });
  renderSwitches();
  renderSensors();
}).catch(() => {});

// Periodic refresh as fallback
setInterval(fetchAutomations, 5000);
