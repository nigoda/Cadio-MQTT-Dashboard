// =============================================================================
//  pages.h — All HTML stored in PROGMEM (flash). Zero heap allocation.
//  Setup page: WiFi + MQTT + Serial Number configuration with captive portal
//  Dashboard:  Devices filtered by serial, on/off, brightness, color picker
// =============================================================================
#pragma once

// ======================= SETUP PAGE =========================================
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
.hint{font-size:.72rem;color:#64748b;margin-top:2px}
</style>
</head>
<body>
<div class="card">
  <h1>&#9881; Nivixsa IoT Setup</h1>
  <p class="sub">Configure Wi-Fi, MQTT credentials and unit serial number</p>

  <div id="pin-gate">
    <label for="pin">Setup PIN</label>
    <input type="password" id="pin" placeholder="Enter PIN" maxlength="16">
    <button onclick="checkPin()" style="margin-top:14px">Unlock</button>
    <div id="pinMsg" class="msg"></div>
  </div>

  <div id="config-form" style="display:none">
    <div class="section-title">Wi-Fi</div>
    <label>Network</label>
    <select id="ssid_select" onchange="syncSSID()">
      <option value="">-- click Scan --</option>
    </select>
    <button class="scan-btn" onclick="scanWifi()" style="width:auto;margin-top:6px;padding:6px 14px">Scan Networks</button>
    <label for="ssid">SSID</label>
    <input type="text" id="ssid" placeholder="Wi-Fi name">
    <label for="wpass">Password</label>
    <input type="password" id="wpass" placeholder="Wi-Fi password">

    <div class="section-title">MQTT / Account</div>
    <label for="email">Email</label>
    <input type="email" id="email" placeholder="your@email.com">
    <label for="mpass">MQTT Password</label>
    <input type="password" id="mpass" placeholder="MQTT password">

    <div class="section-title">Unit Serial Number</div>
    <label for="serial">Serial (MAC prefix)</label>
    <input type="text" id="serial" placeholder="e.g. A4CF12F03246">
    <p class="hint">The base MAC address of the physical unit. Only entities from this unit will be shown on the dashboard.</p>

    <div class="section-title">Security</div>
    <label for="newpin">New Setup PIN (optional)</label>
    <input type="text" id="newpin" placeholder="Leave blank to keep current">

    <button onclick="saveConfig()">Save &amp; Restart</button>
    <div id="saveMsg" class="msg"></div>
  </div>
</div>

<script>
function checkPin(){
  var pin=document.getElementById('pin').value;
  fetch('/check_pin?pin='+encodeURIComponent(pin))
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.ok){
        document.getElementById('pin-gate').style.display='none';
        document.getElementById('config-form').style.display='block';
      } else {
        var m=document.getElementById('pinMsg');
        m.className='msg error'; m.textContent='Wrong PIN.';
      }
    });
}
function scanWifi(){
  var sel=document.getElementById('ssid_select');
  sel.innerHTML='<option value="">-- scanning... --</option>';
  fetch('/scan').then(function(r){return r.json()}).then(function(nets){
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
    ssid:   document.getElementById('ssid').value.trim(),
    wpass:  document.getElementById('wpass').value,
    email:  document.getElementById('email').value.trim(),
    mpass:  document.getElementById('mpass').value,
    serial: document.getElementById('serial').value.trim().toUpperCase(),
    newpin: document.getElementById('newpin').value
  };
  var m=document.getElementById('saveMsg');
  if(!data.ssid||!data.email||!data.mpass||!data.serial){
    m.className='msg error'; m.textContent='All fields except PIN are required.'; return;
  }
  m.className='msg ok'; m.textContent='Saving... Device will restart.';
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(function(r){return r.json()}).then(function(d){
      if(d.ok) m.textContent='Saved! Restarting now...';
      else { m.className='msg error'; m.textContent='Error: '+(d.msg||'unknown'); }
    }).catch(function(){ m.textContent='Saved! Restarting now...'; });
}
</script>
</body>
</html>
)rawhtml";


// ======================= DASHBOARD PAGE =====================================
const char DASHBOARD_HTML[] PROGMEM = R"rawhtml(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nivixsa Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:16px}
h1{color:#38bdf8;margin-bottom:4px;font-size:1.3rem}
.sub{color:#94a3b8;font-size:.8rem;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.card{background:#1e293b;border-radius:10px;padding:14px}
.card h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#94a3b8;margin-bottom:8px}
.st{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.st .l{font-size:.8rem;color:#94a3b8}
.st .v{font-weight:700;color:#e2e8f0;font-size:.85rem}
.badge{padding:2px 9px;border-radius:20px;font-size:.72rem;font-weight:700}
.badge.on{background:#052e16;color:#86efac}
.badge.off{background:#450a0a;color:#fca5a5}
.badge.warn{background:#431407;color:#fdba74}
.msg-list{max-height:140px;overflow-y:auto}
.msg-item{font-size:.7rem;padding:2px 0;border-bottom:1px solid #334155;word-break:break-all;color:#cbd5e1}
.msg-item .tp{color:#38bdf8;margin-right:4px}
.btn{display:inline-block;margin-top:12px;margin-right:8px;padding:6px 14px;background:#1e40af;color:#bfdbfe;border-radius:8px;text-decoration:none;font-size:.8rem}
.btn:hover{background:#1d4ed8}
.btn.danger{background:#7f1d1d;color:#fca5a5}
.dg{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-top:8px}
.dc{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:5px;transition:border-color .2s}
.dc.on{border-color:#16a34a}
.dc.off{border-color:#dc2626}
.dt{font-size:.6rem;text-transform:uppercase;color:#38bdf8;letter-spacing:.07em}
.dn{font-size:.85rem;font-weight:700;color:#e2e8f0}
.dv{font-size:1rem;font-weight:700;color:#38bdf8}
.tr{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
.tl{font-size:.78rem;color:#94a3b8}
.toggle{position:relative;display:inline-block;width:42px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.sl{position:absolute;inset:0;background:#334155;border-radius:24px;cursor:pointer;transition:.25s}
.sl:before{content:'';position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#94a3b8;border-radius:50%;transition:.25s}
input:checked+.sl{background:#16a34a}
input:checked+.sl:before{transform:translateX(18px);background:#fff}
.toggle input:disabled+.sl{opacity:.45;cursor:not-allowed}
.lr{display:block;width:100%;accent-color:#38bdf8}
.cp input[type=color]{width:44px;height:32px;padding:2px;border:1px solid #334155;border-radius:6px;background:#0f172a;cursor:pointer;position:relative;z-index:100}
.cp{display:flex;align-items:center;gap:8px;margin-top:6px}
.hx{font-size:.7rem;color:#94a3b8}
.pulse{animation:pulse 1s ease-in-out}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
#sd{display:inline-block;width:8px;height:8px;background:#86efac;border-radius:50%;margin-right:6px}
</style>
</head>
<body>
<h1>&#127968; Nivixsa Dashboard</h1>
<p class="sub"><span id="sd"></span>Live &mdash; auto-refresh</p>
<div class="grid">
  <div class="card">
    <h2>Network</h2>
    <div class="st"><span class="l">Wi-Fi</span><span class="v" id="d-ssid">...</span></div>
    <div class="st"><span class="l">IP</span><span class="v" id="d-ip">...</span></div>
    <div class="st"><span class="l">Signal</span><span class="v" id="d-rssi">...</span></div>
  </div>
  <div class="card">
    <h2>MQTT</h2>
    <div class="st"><span class="l">Broker</span><span class="v" id="d-broker">...</span></div>
    <div class="st"><span class="l">Status</span><span id="d-mqtt" class="badge warn">...</span></div>
    <div class="st"><span class="l">Messages</span><span class="v" id="d-msgs">0</span></div>
  </div>
  <div class="card">
    <h2>Unit</h2>
    <div class="st"><span class="l">Serial</span><span class="v" id="d-serial">...</span></div>
    <div class="st"><span class="l">Uptime</span><span class="v" id="d-up">...</span></div>
    <div class="st"><span class="l">Heap</span><span class="v" id="d-heap">...</span></div>
  </div>
</div>

<div class="card" style="margin-top:12px">
  <h2>Devices</h2>
  <div class="dg" id="devs"><p style="color:#64748b;font-size:.8rem">Waiting for MQTT discovery...</p></div>
</div>

<div class="card" style="margin-top:12px">
  <h2>Messages</h2>
  <div class="msg-list" id="msgs"><p style="color:#64748b;font-size:.8rem">No messages yet.</p></div>
</div>
<a class="btn" href="/setup">Setup</a>
<a class="btn danger" href="/reset" onclick="return confirm('Wipe all and reboot?')">Factory Reset</a>

<script>
function $(id){return document.getElementById(id)}
var cache={};

function isOn(s){s=(s||'').toUpperCase();return s==='ON'||s==='1'||s==='TRUE'||s==='OPEN'||s==='LOCKED'}
function bc(s){var u=(s||'').toUpperCase();if(u==='ON'||u==='LOCKED'||u==='OPEN')return 'on';if(u==='OFF'||u==='UNLOCKED'||u==='CLOSED')return 'off';return 'warn'}
function sl(s){return s&&s.length?s:'--'}

function rgbHex(r,g,b){
  function p(v){return Math.max(0,Math.min(255,parseInt(v||0,10))).toString(16).padStart(2,'0')}
  return '#'+p(r)+p(g)+p(b);
}
function hexRgb(h){
  if(!h||typeof h!=='string')return null;
  h=h.trim();if(h[0]==='#')h=h.substring(1);if(h.length!==6)return null;
  var n=parseInt(h,16);if(isNaN(n))return null;
  return{r:(n>>16)&255,g:(n>>8)&255,b:n&255};
}

function dk(d){return d.id||d.cmd||d.name||''}

function render(devs){
  var g=$('devs');
  if(!devs||!devs.length){g.innerHTML="<p style='color:#64748b;font-size:.8rem'>No devices found for this unit.</p>";return}
  devs.forEach(function(d){
    var k=dk(d);if(!k)return;
    if(!cache[k]||!cache[k].p)cache[k]={s:d.state,p:false};
  });
  var h='';
  devs.forEach(function(d){
    var k=dk(d);
    var c=cache[k]||{s:d.state,p:false};
    var cs=c.s||d.state||'';
    var hs=!!(cs&&cs.length);
    var on=isOn(cs);
    var ctrl=d.cmd&&(d.type==='switch'||d.type==='light'||d.type==='lock'||d.type==='fan'||d.type==='cover');
    var bri=(d.type==='light'&&d.cmd&&d.supports_brightness);
    var clr=(d.type==='light'&&d.cmd&&d.supports_rgb);
    var cc=ctrl?(hs?(on?'on':'off'):''):'';
    var sc=d.cmd.replace(/"/g,'&quot;');
    h+="<div class='dc "+cc+"'>";
    h+="<div class='dt'>"+d.type+"</div>";
    h+="<div class='dn'>"+d.name+"</div>";
    if(d.type==='sensor'||d.type==='binary_sensor'){
      h+="<div class='dv'>"+sl(cs)+"</div>";
    } else {
      h+="<span class='badge "+(hs?bc(cs):'warn')+"'>"+sl(cs)+"</span>";
      if(ctrl){
        var chk=on?'checked':'';
        var dis=c.p?'disabled':'';
        h+="<div class='tr'>";
        h+="<span class='tl'>"+(hs?(on?'ON':'OFF'):'--')+"</span>";
        h+="<label class='toggle'>";
        h+="<input type='checkbox' "+chk+" "+dis+" onchange=\"tog(this,'"+k.replace(/"/g,'&quot;')+"','"+sc+"')\">";
        h+="<span class='sl'></span></label></div>";
      }
      if(bri){
        var bv=(typeof d.brightness==='number'&&d.brightness>=0)?d.brightness:0;
        var bi='b-'+btoa(d.cmd).replace(/=/g,'');
        h+="<div style='margin-top:8px'>";
        h+="<div style='display:flex;justify-content:space-between;font-size:.7rem;color:#94a3b8;margin-bottom:3px'>";
        h+="<span>Brightness</span><span id='"+bi+"'>"+bv+"%</span></div>";
        h+="<input class='lr' type='range' min='0' max='100' value='"+bv+"' ";
        h+="oninput=\"document.getElementById('"+bi+"').textContent=this.value+'%'\" ";
        h+="onchange=\"setBri('"+k.replace(/"/g,'&quot;')+"','"+sc+"',this.value)\">";
        h+="</div>";
      }
      if(clr){
        var cr=(typeof d.color_r==='number'&&d.color_r>=0)?d.color_r:255;
        var cg=(typeof d.color_g==='number'&&d.color_g>=0)?d.color_g:0;
        var cb=(typeof d.color_b==='number'&&d.color_b>=0)?d.color_b:0;
        var hx=rgbHex(cr,cg,cb);
        var ci='c-'+btoa(d.cmd).replace(/=/g,'');
        h+="<div class='cp'>";
        h+="<input type='color' value='"+hx+"' ";
        h+="oninput=\"document.getElementById('"+ci+"').textContent=this.value\" ";
        h+="onchange=\"setClr('"+k.replace(/"/g,'&quot;')+"','"+sc+"',this.value)\">";
        h+="<span class='hx' id='"+ci+"'>"+hx+"</span></div>";
      }
    }
    h+="</div>";
  });
  g.innerHTML=h;
}

function renderMsgs(msgs){
  var el=$('msgs');
  if(!msgs||!msgs.length){el.innerHTML="<p style='color:#64748b;font-size:.8rem'>No messages yet.</p>";return}
  var h='';
  msgs.forEach(function(m){h+="<div class='msg-item'><span class='tp'>"+m.t+"</span>"+m.p+"</div>"});
  el.innerHTML=h;
}

function refresh(){
  if(document.querySelector('input[type="color"]:focus'))return;
  var dot=$('sd');dot.classList.add('pulse');
  fetch('/api/data')
    .then(function(r){return r.json()})
    .then(function(d){
      dot.classList.remove('pulse');
      $('d-ssid').textContent=d.ssid||'';
      $('d-ip').textContent=d.ip||'';
      $('d-rssi').textContent=(d.rssi||'')+' dBm';
      $('d-broker').textContent=(d.broker||'')+':'+(d.port||'');
      var ms=$('d-mqtt');ms.textContent=d.mqtt?'Connected':'Disconnected';ms.className='badge '+(d.mqtt?'on':'off');
      $('d-msgs').textContent=d.msg_count||0;
      $('d-serial').textContent=d.serial||'--';
      $('d-up').textContent=d.uptime||'';
      $('d-heap').textContent=(d.heap||'?')+' KB';
      render(d.devices);
      renderMsgs(d.messages);
    })
    .catch(function(){dot.classList.remove('pulse')});
}

function tog(cb,key,topic){
  var ns=cb.checked?'ON':'OFF';
  if(!cache[key])cache[key]={s:ns,p:true};
  cache[key].s=ns;cache[key].p=true;cb.disabled=true;
  fetch('/cmd?topic='+encodeURIComponent(topic)+'&payload='+encodeURIComponent(ns))
    .then(function(r){return r.json()})
    .then(function(d){
      if(!d.ok)alert('Failed');
      setTimeout(function(){if(cache[key])cache[key].p=false;refresh()},1200);
    })
    .catch(function(){if(cache[key])cache[key].p=false;setTimeout(refresh,1200)});
}

function setBri(key,topic,val){
  var b=Math.max(0,Math.min(100,parseInt(val||0,10)));
  if(!cache[key])cache[key]={s:'ON',p:true};cache[key].p=true;
  var p=JSON.stringify({state:'ON',brightness:b});
  fetch('/cmd?topic='+encodeURIComponent(topic)+'&payload='+encodeURIComponent(p)+'&raw=1')
    .then(function(r){return r.json()})
    .then(function(){setTimeout(function(){if(cache[key])cache[key].p=false;refresh()},900)})
    .catch(function(){if(cache[key])cache[key].p=false;setTimeout(refresh,900)});
}

function setClr(key,topic,hex){
  var rgb=hexRgb(hex);if(!rgb)return;
  if(!cache[key])cache[key]={s:'ON',p:true};cache[key].p=true;
  var p=JSON.stringify({state:'ON',color:{r:rgb.r,g:rgb.g,b:rgb.b}});
  fetch('/cmd?topic='+encodeURIComponent(topic)+'&payload='+encodeURIComponent(p)+'&raw=1')
    .then(function(r){return r.json()})
    .then(function(){setTimeout(function(){if(cache[key])cache[key].p=false;refresh()},900)})
    .catch(function(){if(cache[key])cache[key].p=false;setTimeout(refresh,900)});
}

refresh();
setInterval(refresh,2000);
</script>
</body>
</html>
)rawhtml";
