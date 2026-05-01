// =============================================================================
//  webpages.h — HTML pages stored in flash (PROGMEM)
// =============================================================================
#pragma once

const char SETUP_HTML[] PROGMEM = R"rawhtml(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nivixsa IoT Setup</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#1e293b;border-radius:12px;padding:32px;width:100%;max-width:420px;box-shadow:0 8px 32px #0005}
  h1{font-size:1.4rem;color:#38bdf8;margin-bottom:6px}
  p.sub{font-size:.85rem;color:#94a3b8;margin-bottom:24px}
  label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:4px;margin-top:14px}
  input{width:100%;padding:10px 12px;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#e2e8f0;font-size:.95rem}
  input:focus{outline:none;border-color:#38bdf8}
  .section-title{font-size:.75rem;font-weight:700;letter-spacing:.08em;color:#38bdf8;text-transform:uppercase;margin-top:24px;margin-bottom:4px;border-bottom:1px solid #334155;padding-bottom:4px}
  button{margin-top:24px;width:100%;padding:12px;background:#38bdf8;border:none;border-radius:8px;color:#0f172a;font-weight:700;font-size:1rem;cursor:pointer}
  button:hover{background:#7dd3fc}
  .msg{margin-top:14px;padding:10px 14px;border-radius:8px;font-size:.85rem;display:none}
  .msg.error{background:#450a0a;color:#fca5a5;display:block}
  .msg.ok{background:#052e16;color:#86efac;display:block}
  select{width:100%;padding:10px 12px;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#e2e8f0;font-size:.95rem}
  .scan-btn{margin-top:6px;padding:6px 14px;background:#1e40af;border:none;border-radius:6px;color:#bfdbfe;font-size:.8rem;cursor:pointer}
</style>
</head>
<body>
<div class="card">
  <h1>&#128279; Nivixsa IoT Setup</h1>
  <p class="sub">Configure your device to connect to Wi-Fi and Nivixsa cloud.</p>

  <div id="pinForm">
    <p class="sub" style="margin-top:0">Enter the setup PIN to continue.</p>
    <label>Setup PIN</label>
    <input type="password" id="pin" placeholder="****" maxlength="20">
    <button onclick="checkPin()">Unlock</button>
    <div id="pinMsg" class="msg"></div>
  </div>

  <div id="mainForm" style="display:none">
    <div class="section-title">Wi-Fi</div>
    <label>SSID (Network Name)</label>
    <select id="ssid_select" onchange="syncSSID()"><option value="">-- scanning... --</option></select>
    <button class="scan-btn" onclick="scanWifi()">Rescan</button>
    <label>Or enter manually</label>
    <input type="text" id="ssid" placeholder="Your Wi-Fi name">
    <label>Password</label>
    <input type="password" id="wpass" placeholder="Wi-Fi password">

    <div class="section-title">Nivixsa Cloud</div>
    <label>Account Email</label>
    <input type="email" id="email" placeholder="you@example.com">
    <label>Account Password</label>
    <input type="password" id="mpass" placeholder="Nivixsa password">

    <div class="section-title">Change Setup PIN (optional)</div>
    <label>New PIN (leave blank to keep current)</label>
    <input type="password" id="newpin" placeholder="****" maxlength="20">

    <button onclick="saveConfig()">Save and Connect</button>
    <div id="saveMsg" class="msg"></div>
  </div>
</div>
<script>
function checkPin(){
  var pin = document.getElementById('pin').value;
  fetch('/check_pin?pin=' + encodeURIComponent(pin))
    .then(r=>r.json()).then(d=>{
      if(d.ok){
        document.getElementById('pinForm').style.display='none';
        document.getElementById('mainForm').style.display='block';
        scanWifi();
      } else {
        var m=document.getElementById('pinMsg');
        m.className='msg error'; m.textContent='Wrong PIN. Try again.';
      }
    });
}
function scanWifi(){
  var sel=document.getElementById('ssid_select');
  sel.innerHTML='<option value="">-- scanning... --</option>';
  fetch('/scan').then(r=>r.json()).then(nets=>{
    sel.innerHTML='<option value="">-- select network --</option>';
    nets.forEach(function(n){
      var o=document.createElement('option');
      o.value=n.ssid; o.textContent=n.ssid+' ('+n.rssi+' dBm'+(n.enc?' lock':'')+')';
      sel.appendChild(o);
    });
  });
}
function syncSSID(){
  var v=document.getElementById('ssid_select').value;
  if(v) document.getElementById('ssid').value=v;
}
function saveConfig(){
  var data={
    ssid: document.getElementById('ssid').value.trim(),
    wpass: document.getElementById('wpass').value,
    email: document.getElementById('email').value.trim(),
    mpass: document.getElementById('mpass').value,
    newpin: document.getElementById('newpin').value
  };
  var m=document.getElementById('saveMsg');
  if(!data.ssid||!data.email||!data.mpass){
    m.className='msg error'; m.textContent='SSID, email and password are required.'; return;
  }
  m.className='msg ok'; m.textContent='Saving... Device will restart and connect.';
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>{
      if(d.ok){
        m.textContent='Saved! Device is restarting. Connect your phone back to your home Wi-Fi.';
      } else {
        m.className='msg error'; m.textContent='Error: '+d.msg;
      }
    }).catch(()=>{
      m.textContent='Saved! Device is restarting.';
    });
}
</script>
</body>
</html>
)rawhtml";

const char DASHBOARD_HTML[] PROGMEM = R"rawhtml(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nivixsa ESP8266 Status</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}
  h1{color:#38bdf8;margin-bottom:4px;font-size:1.4rem}
  .sub{color:#94a3b8;font-size:.82rem;margin-bottom:20px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
  .card{background:#1e293b;border-radius:10px;padding:16px}
  .card h2{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:#94a3b8;margin-bottom:10px}
  .stat{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px}
  .stat .lbl{font-size:.82rem;color:#94a3b8}
  .stat .val{font-weight:700;color:#e2e8f0;font-size:.88rem}
  .badge{padding:2px 9px;border-radius:20px;font-size:.72rem;font-weight:700}
  .badge.on{background:#052e16;color:#86efac}
  .badge.off{background:#450a0a;color:#fca5a5}
  .badge.warn{background:#431407;color:#fdba74}
  .msg-list{max-height:160px;overflow-y:auto}
  .msg-item{font-size:.73rem;padding:3px 0;border-bottom:1px solid #334155;word-break:break-all;color:#cbd5e1}
  .msg-item .tp{color:#38bdf8;margin-right:4px}
  .btn{display:inline-block;margin-top:14px;margin-right:8px;padding:7px 16px;background:#1e40af;color:#bfdbfe;border-radius:8px;text-decoration:none;font-size:.82rem}
  .btn:hover{background:#1d4ed8}
  .btn.danger{background:#7f1d1d;color:#fca5a5}
  .dev-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;margin-top:6px}
  .dev-card{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:6px;transition:border-color .2s;overflow:hidden}
  .dev-card.on{border-color:#16a34a}
  .dev-card.off{border-color:#dc2626}
  .dev-type{font-size:.62rem;text-transform:uppercase;color:#38bdf8;letter-spacing:.07em}
  .dev-name{font-size:.88rem;font-weight:700;color:#e2e8f0}
  .dev-id{font-size:.68rem;color:#64748b;word-break:break-all;margin-top:-2px}
  .dev-val{font-size:1rem;font-weight:700;color:#38bdf8}
  .toggle-row{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
  .toggle-lbl{font-size:.8rem;color:#94a3b8}
  .light-wrap{border-top:1px solid #1e293b;background:#0f172a;padding-top:8px;margin-top:6px}
  .light-meta{display:flex;justify-content:space-between;align-items:center;font-size:.74rem;color:#94a3b8;margin-bottom:4px}
  .light-range{display:block;width:100%;accent-color:#38bdf8}
  .toggle{position:relative;display:inline-block;width:42px;height:24px;flex-shrink:0}
  .toggle input{opacity:0;width:0;height:0}
  .slider{position:absolute;inset:0;background:#334155;border-radius:24px;cursor:pointer;transition:.25s}
  .slider:before{content:'';position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#94a3b8;border-radius:50%;transition:.25s}
  input:checked+.slider{background:#16a34a}
  input:checked+.slider:before{transform:translateX(18px);background:#fff}
  .toggle input:disabled+.slider{opacity:.45;cursor:not-allowed}
  .pulse{animation:pulse 1s ease-in-out}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  #status-dot{display:inline-block;width:8px;height:8px;background:#86efac;border-radius:50%;margin-right:6px}
</style>
</head>
<body>
<h1>&#127968; Nivixsa ESP8266 Status</h1>
<p class="sub"><span id="status-dot"></span>Live &mdash; updates every 10s</p>
<div class="grid">
  <div class="card">
    <h2>Network</h2>
    <div class="stat"><span class="lbl">Wi-Fi</span><span class="val" id="d-ssid">...</span></div>
    <div class="stat"><span class="lbl">IP Address</span><span class="val" id="d-ip">...</span></div>
    <div class="stat"><span class="lbl">Signal</span><span class="val" id="d-rssi">...</span></div>
  </div>
  <div class="card">
    <h2>MQTT</h2>
    <div class="stat"><span class="lbl">Broker</span><span class="val" id="d-broker">...</span></div>
    <div class="stat"><span class="lbl">Account</span><span class="val" id="d-email">...</span></div>
    <div class="stat"><span class="lbl">Status</span><span id="d-mqtts" class="badge warn">...</span></div>
    <div class="stat"><span class="lbl">Messages</span><span class="val" id="d-msgs">0</span></div>
  </div>
  <div class="card">
    <h2>Device</h2>
    <div class="stat"><span class="lbl">Uptime</span><span class="val" id="d-uptime">...</span></div>
    <div class="stat"><span class="lbl">Free heap</span><span class="val" id="d-heap">...</span></div>
    <div class="stat"><span class="lbl">Chip</span><span class="val">ESP8266</span></div>
  </div>
</div>
<div class="card" style="margin-top:14px">
  <h2>Devices &amp; Entities</h2>
  <div class="dev-grid" id="dev-grid"><p style="color:#64748b;font-size:.8rem">Waiting for MQTT discovery...</p></div>
</div>
<div class="card" style="margin-top:14px">
  <h2>Recent MQTT Messages</h2>
  <div class="msg-list" id="msg-list"><p style="color:#64748b;font-size:.8rem">No messages yet.</p></div>
</div>
<a class="btn" href="/setup">Setup / Reconfigure</a>
<a class="btn danger" href="/reset" onclick="return confirm('Wipe credentials and reboot?')">Factory Reset</a>

<script>
var WIFI_SSID  = '__WIFI_SSID__';
var BROKER     = '__BROKER__';
var BROKER_PORT= '__PORT__';
var EMAIL      = '__EMAIL__';
var IP         = '__IP__';

function $(id){ return document.getElementById(id); }

function badgeClass(state){
  var s = (state||'').toUpperCase();
  if(s==='ON'||s==='LOCKED'||s==='OPEN') return 'on';
  if(s==='OFF'||s==='UNLOCKED'||s==='CLOSED') return 'off';
  return 'warn';
}

// Stable device state cache — survives re-renders
var devCache = {};

function isOnState(s){
  s=(s||'').toUpperCase();
  return s==='ON'||s==='1'||s==='TRUE'||s==='OPEN'||s==='LOCKED';
}

function stateLabel(s){
  return s && s.length ? s : '--';
}

function devKey(d){
  return d.id || d.cmd || d.name || '';
}

function renderDevices(devs){
  var g=$('dev-grid');
  if(!devs||devs.length===0){
    g.innerHTML="<p style='color:#64748b;font-size:.8rem'>No devices discovered yet. Waiting for MQTT config messages...</p>";
    return;
  }
  // Merge incoming states into cache (skip if optimistic pending)
  devs.forEach(function(d){
    var key=devKey(d);
    if(!key) return;
    if(!devCache[key]||!devCache[key].pending) devCache[key]={state:d.state,pending:false};
  });

  var h='';
  devs.forEach(function(d){
    var key=devKey(d);
    var cached=devCache[key]||{state:d.state,pending:false};
    var curState=cached.state||d.state||'';
    var hasState=!!(curState&&curState.length);
    var isOn=isOnState(curState);
    var canCtrl=d.cmd&&(d.type==='switch'||d.type==='light'||d.type==='lock'||d.type==='fan'||d.type==='cover');
    var canBrightness=(d.type==='light'&&d.cmd&&d.supports_brightness);
    var cardCls=canCtrl?(hasState?(isOn?'on':'off'):''):'';
    var safeCmd=d.cmd.replace(/"/g,'&quot;');
    h+="<div class='dev-card "+cardCls+"'>";
    h+="<div class='dev-type'>"+d.type+"</div>";
    h+="<div class='dev-name'>"+d.name+"</div>";
    if(d.device_id){
      h+="<div class='dev-id'>ID: "+d.device_id+"</div>";
    }
    if(d.serial){
      h+="<div class='dev-id'>Serial: "+d.serial+"</div>";
    }
    if(d.type==='sensor'||d.type==='binary_sensor'){
      h+="<div class='dev-val'>"+stateLabel(curState)+"</div>";
    } else {
      var bc=badgeClass(curState);
      h+="<span class='badge "+(hasState?bc:'warn')+"' id='badge-"+btoa(d.cmd).replace(/=/g,'')+'>'+stateLabel(curState)+"</span>";
      if(canCtrl){
        var chk=isOn?'checked':'';
        var dis=cached.pending?'disabled':'';
        h+="<div class='toggle-row'>";
        h+="<span class='toggle-lbl'>"+(hasState?(isOn?'ON':'OFF'):'--')+"</span>";
        h+="<label class='toggle'><input type='checkbox' "+chk+' '+dis+" onchange=\"toggleCmd(this,'"+key.replace(/"/g,'&quot;')+"','"+safeCmd+"')\">"
          +"<span class='slider'></span></label>";
        h+="</div>";
      }
      if(canBrightness){
        var bri=(typeof d.brightness==='number'&&d.brightness>=0)?d.brightness:0;
        h+="<div class='light-wrap'>";
        h+="<div class='light-meta'><span>Brightness</span><span id='bri-val-"+btoa(d.cmd).replace(/=/g,'')+"'>"+bri+"%</span></div>";
        h+="<input class='light-range' type='range' min='0' max='100' value='"+bri+"' oninput=\"this.previousElementSibling.querySelector('span:last-child').textContent=this.value+'%'\" onchange=\"sendBrightness('"+key.replace(/"/g,'&quot;')+"','"+safeCmd+"',this.value)\">";
        h+="</div>";
      }
    }
    h+="</div>";
  });
  g.innerHTML=h;
}

function renderMessages(msgs){
  var el=$('msg-list');
  if(!msgs||msgs.length===0){el.innerHTML="<p style='color:#64748b;font-size:.8rem'>No messages yet.</p>";return;}
  var h='';
  msgs.forEach(function(m){
    h+="<div class='msg-item'><span class='tp'>"+m.t+"</span>"+m.p+"</div>";
  });
  el.innerHTML=h;
}

function refresh(){
  var dot=$('status-dot');
  dot.classList.add('pulse');
  fetch('/api/data')
    .then(function(r){return r.json();})
    .then(function(d){
      dot.classList.remove('pulse');
      $('d-ssid').textContent   = WIFI_SSID;
      $('d-ip').textContent     = IP;
      $('d-rssi').textContent   = d.rssi+' dBm';
      $('d-broker').textContent = BROKER+':'+BROKER_PORT;
      $('d-email').textContent  = EMAIL;
      $('d-uptime').textContent = d.uptime;
      $('d-heap').textContent   = d.heap+' KB';
      $('d-msgs').textContent   = d.msg_count;
      var ms=$('d-mqtts');
      ms.textContent=d.mqtt?'Connected':'Disconnected';
      ms.className='badge '+(d.mqtt?'on':'off');
      renderDevices(d.devices);
      renderMessages(d.messages);
    })
    .catch(function(){dot.classList.remove('pulse');});
}

function toggleCmd(checkbox, key, topic){
  var newState = checkbox.checked ? 'ON' : 'OFF';
  // Optimistic update
  if(!devCache[key]) devCache[key]={state:newState,pending:true};
  devCache[key].state   = newState;
  devCache[key].pending = true;
  checkbox.disabled = true;

  fetch('/cmd?topic='+encodeURIComponent(topic)+'&payload='+encodeURIComponent(newState))
    .then(function(r){return r.json();})
    .then(function(d){
      if(!d.ok){ alert('Command failed: '+(d.msg||'error')); devCache[key].pending=false; }
      // Refresh quickly to get real broker state
      setTimeout(function(){ if(devCache[key]) devCache[key].pending=false; refresh(); }, 1200);
    })
    .catch(function(){ if(devCache[key]) devCache[key].pending=false; setTimeout(refresh,1200); });
}

function sendBrightness(key, topic, value){
  var bri=Math.max(0, Math.min(100, parseInt(value||0,10)));
  if(!devCache[key]) devCache[key]={state:'ON',pending:true};
  devCache[key].pending=true;
  var payload=JSON.stringify({state:'ON', brightness:bri});

  fetch('/cmd?topic='+encodeURIComponent(topic)+'&payload='+encodeURIComponent(payload)+'&raw=1')
    .then(function(r){return r.json();})
    .then(function(d){
      if(!d.ok){ alert('Brightness failed: '+(d.msg||'error')); }
      setTimeout(function(){ if(devCache[key]) devCache[key].pending=false; refresh(); }, 900);
    })
    .catch(function(){ if(devCache[key]) devCache[key].pending=false; setTimeout(refresh,900); });
}

// Populate static fields from placeholders baked in at serve time
$('d-ssid').textContent   = WIFI_SSID;
$('d-ip').textContent     = IP;
$('d-broker').textContent = BROKER+':'+BROKER_PORT;
$('d-email').textContent  = EMAIL;

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
)rawhtml";
