import os, json, time, hmac, hashlib, uuid
from aiohttp import web

PORT       = int(os.environ.get('ADMIN_PORT', '8200'))
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'precision')
SHARED     = os.environ.get('SHARED_DIR', '/shared')
SECRET     = os.environ.get('ADMIN_SECRET', 'change-me-secret').encode()
SERVER_HOST = os.environ.get('SERVER_HOST', '10.0.49.145')
BASE_PORT   = int(os.environ.get('BASE_PORT', '8100'))

CONFIG   = os.path.join(SHARED, 'config.json')
SESSIONS = os.path.join(SHARED, 'sessions.json')
COMMANDS = os.path.join(SHARED, 'commands.json')

POLICY_KEYS = ["read_only", "scroll_lock", "copy", "paste", "clipboard", "print", "download", "devtools", "file_management"]
DEFAULT_POLICIES = {k: False for k in POLICY_KEYS}

DEFAULT_CONFIG = {
    "enabled": True,
    "bitrate": 4000000,
    "max_sessions": 1,
    "sites": [
        {"id": "default", "name": "Precision IT",
         "url": "https://www.precisionit.co.in/", "port": BASE_PORT,
         "policies": dict(DEFAULT_POLICIES)},
    ],
}

def read_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception:
        return default

def write_json(path, data):
    os.makedirs(SHARED, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(data, f, indent=2)
    os.replace(tmp, path)

def ensure_config():
    if not os.path.exists(CONFIG):
        write_json(CONFIG, DEFAULT_CONFIG)

def token():
    return hmac.new(SECRET, (ADMIN_USER + ADMIN_PASS).encode(), hashlib.sha256).hexdigest()

def authed(request):
    return request.cookies.get('rbi_admin') == token()

# ---------------- API ----------------
async def api_state(request):
    if not authed(request): return web.json_response({'error': 'auth'}, status=401)
    return web.json_response({
        "config": read_json(CONFIG, DEFAULT_CONFIG),
        "sessions": read_json(SESSIONS, {"sessions": [], "updated": 0}),
        "policy_keys": POLICY_KEYS,
    })

async def api_settings(request):
    if not authed(request): return web.json_response({'error': 'auth'}, status=401)
    body = await request.json()
    cfg = read_json(CONFIG, DEFAULT_CONFIG)
    for k in ("enabled", "bitrate", "max_sessions"):
        if k in body: cfg[k] = body[k]
    write_json(CONFIG, cfg)
    return web.json_response({"ok": True})

async def api_site_add(request):
    if not authed(request): return web.json_response({'error': 'auth'}, status=401)
    body = await request.json()
    url = (body.get('url') or '').strip()
    name = (body.get('name') or url).strip()
    if not url:
        return web.json_response({"error": "url required"}, status=400)
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    cfg = read_json(CONFIG, DEFAULT_CONFIG)
    cfg.setdefault('sites', [])
    if any(s['url'] == url for s in cfg['sites']):
        return web.json_response({"error": "already exists"}, status=400)
    used = [int(x.get('port', 0)) for x in cfg['sites'] if x.get('port')]
    nextp = max(used + [BASE_PORT]) + 1
    cfg['sites'].append({"id": uuid.uuid4().hex[:8], "name": name, "url": url,
                         "port": nextp, "policies": dict(DEFAULT_POLICIES)})
    write_json(CONFIG, cfg)
    return web.json_response({"ok": True})

async def api_site_delete(request):
    if not authed(request): return web.json_response({'error': 'auth'}, status=401)
    sid = (await request.json()).get('id')
    cfg = read_json(CONFIG, DEFAULT_CONFIG)
    cfg['sites'] = [s for s in cfg.get('sites', []) if s['id'] != sid]
    write_json(CONFIG, cfg)
    return web.json_response({"ok": True})

async def api_site_policy(request):
    if not authed(request): return web.json_response({'error': 'auth'}, status=401)
    body = await request.json()
    sid, pol = body.get('id'), body.get('policies', {})
    cfg = read_json(CONFIG, DEFAULT_CONFIG)
    for s in cfg.get('sites', []):
        if s['id'] == sid:
            s.setdefault('policies', dict(DEFAULT_POLICIES))
            for k in POLICY_KEYS:
                if k in pol: s['policies'][k] = bool(pol[k])
    write_json(CONFIG, cfg)
    return web.json_response({"ok": True})

async def api_command(request):
    if not authed(request): return web.json_response({'error': 'auth'}, status=401)
    write_json(COMMANDS, await request.json())
    return web.json_response({"ok": True})

async def login(request):
    data = await request.post()
    if data.get('user') == ADMIN_USER and data.get('pass') == ADMIN_PASS:
        resp = web.HTTPFound('/'); resp.set_cookie('rbi_admin', token(), httponly=True, max_age=86400); return resp
    return web.HTTPFound('/?e=1')

async def logout(request):
    resp = web.HTTPFound('/?out=1'); resp.del_cookie('rbi_admin'); return resp

async def index(request):
    return web.Response(text=(PANEL_HTML if authed(request) else LOGIN_HTML), content_type='text/html')

SHIELD = ('<svg width="26" height="26" viewBox="-28 -30 56 74">'
  '<path d="M0,-26 L22,-16 L22,8 C22,26 0,40 0,40 C0,40 -22,26 -22,8 L-22,-16 Z" fill="#06140a" stroke="#7ac943" stroke-width="2.4"/>'
  '<path d="M-9,2 C-9,-6 9,-6 9,2" fill="none" stroke="#7ac943" stroke-width="2"/>'
  '<path d="M-6,6 C-6,0 6,0 6,6" fill="none" stroke="#7ac943" stroke-width="2"/>'
  '<path d="M-3,9 C-3,5 3,5 3,9" fill="none" stroke="#7ac943" stroke-width="2"/></svg>')

BRAND_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&display=swap');
*{box-sizing:border-box} body{margin:0;font-family:'Montserrat',system-ui,sans-serif;
  background:#f4f7f1;color:#1f2a1a;-webkit-font-smoothing:antialiased}
body:before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.05;
  background:repeating-linear-gradient(90deg,#7ac943 0 1px,transparent 1px 64px);
  -webkit-mask:linear-gradient(180deg,#000,transparent);mask:linear-gradient(180deg,#000,transparent)}
.glow{box-shadow:0 0 24px rgba(122,201,67,.18)}
a{color:#3f7d22;text-decoration:none}
.grn{color:#3f7d22}
"""

LOGIN_HTML = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>InnaIT · RBI Console</title>
<style>{BRAND_CSS}
 .wrap{{height:100vh;display:flex;align-items:center;justify-content:center;position:relative;z-index:1}}
 .card{{width:360px;padding:36px 32px;border-radius:18px;background:#ffffff;border:1px solid #e3e8dd}}
 h1{{font-size:18px;margin:0 0 2px;font-weight:600;display:flex;align-items:center;gap:10px}}
 .sub{{color:#6f8060;font-size:12px;margin:6px 0 22px}}
 label{{font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:#6f8060;display:block;margin:16px 0 7px}}
 input{{width:100%;padding:12px 14px;border-radius:10px;border:1px solid #cdd6c4;background:#ffffff;color:#1f2a1a;font-size:14px;font-family:inherit}}
 input:focus{{outline:none;border-color:#7ac943;box-shadow:0 0 0 3px rgba(122,201,67,.15)}}
 button{{width:100%;margin-top:24px;padding:13px;border:0;border-radius:10px;background:#7ac943;color:#04210a;font-weight:700;cursor:pointer;font-size:14px;font-family:inherit}}
 .err{{color:#ff7b6b;font-size:12px;margin-top:14px;text-align:center;display:none}}
</style></head><body><div class=wrap>
 <form class="card glow" method=post action=/login>
   <h1>{SHIELD} InnaIT RBI Console</h1>
   <div class=sub>Identity Secured · Zero Trust, Verify Everything</div>
   <label>Username</label><input name=user autocomplete=username>
   <label>Password</label><input name=pass type=password autocomplete=current-password>
   <button type=submit>Sign in</button>
   <div class=err id=err>Invalid credentials</div>
 </form></div>
 <script>if(location.search.includes('e=1'))err.style.display='block'</script>
</body></html>"""

PANEL_HTML = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>InnaIT · RBI Console</title>
<style>{BRAND_CSS}
 header{{display:flex;align-items:center;gap:12px;padding:16px 26px;background:#ffffff;border-bottom:1px solid #e3e8dd;position:relative;z-index:1}}
 header h1{{font-size:16px;margin:0;font-weight:600;flex:1;display:flex;align-items:center;gap:10px}}
 .pill{{padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px}}
 .on-p{{background:#e7f6da;color:#7ac943;box-shadow:0 0 14px rgba(122,201,67,.25)}} .off-p{{background:#fbe3e3;color:#ff8a8a}}
 .tabs{{display:flex;gap:2px;padding:0 26px;background:#ffffff;border-bottom:1px solid #e3e8dd;position:relative;z-index:1}}
 .tabs button{{background:none;border:0;color:#6f8060;padding:14px 18px;cursor:pointer;font-size:13px;font-family:inherit;border-bottom:2px solid transparent}}
 .tabs button.on{{color:#16321a;border-bottom-color:#7ac943}}
 main{{padding:26px;max-width:960px;position:relative;z-index:1}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:22px}}
 .stat{{background:#ffffff;border:1px solid #e3e8dd;border-radius:14px;padding:18px}}
 .stat .k{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#6f8060}}
 .stat .v{{font-size:26px;font-weight:700;margin-top:8px}}
 .card{{background:#ffffff;border:1px solid #e3e8dd;border-radius:14px;padding:20px;margin-bottom:18px}}
 .card h2{{font-size:14px;margin:0 0 14px;font-weight:600}}
 label{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#6f8060;display:block;margin:12px 0 6px}}
 input[type=text],input[type=number],select{{width:100%;padding:10px 12px;border-radius:9px;border:1px solid #cdd6c4;background:#ffffff;color:#1f2a1a;font-size:14px;font-family:inherit}}
 select{{cursor:pointer}}
 .row{{display:flex;align-items:center;justify-content:space-between;padding:9px 0;border-bottom:1px solid #eef1ea}}
 .row:last-child{{border-bottom:0}}
 .toggle{{position:relative;width:44px;height:24px}} .toggle input{{display:none}}
 .toggle span{{position:absolute;inset:0;background:#cdd6c4;border-radius:20px;cursor:pointer;transition:.2s}}
 .toggle span:before{{content:'';position:absolute;width:18px;height:18px;left:3px;top:3px;background:#ffffff;border-radius:50%;transition:.2s}}
 .toggle input:checked+span{{background:#7ac943;box-shadow:0 0 12px rgba(122,201,67,.4)}}
 .toggle input:checked+span:before{{transform:translateX(20px);background:#04210a}}
 button.act{{padding:10px 16px;border:0;border-radius:9px;background:#7ac943;color:#04210a;font-weight:700;cursor:pointer;font-size:13px;font-family:inherit}}
 button.danger{{background:#ff5d5d;color:#2a0606}} button.ghost{{background:#eef6e6;color:#7ac943;border:1px solid #cdd6c4}}
 table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{text-align:left;padding:9px 8px;border-bottom:1px solid #eef1ea}}
 th{{color:#6f8060;font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
 .site{{border:1px solid #e3e8dd;border-radius:12px;padding:16px;margin-bottom:14px;background:#fbfcfa}}
 .site .top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}}
 .site .url{{color:#6f8060;font-size:12px;word-break:break-all}}
 .site .acc{{font-size:12px;margin-top:5px}} .site .acc a{{color:#3f7d22;font-weight:600}}
 .polgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:14px}}
 .pol{{display:flex;align-items:center;justify-content:space-between;gap:8px;background:#ffffff;border:1px solid #e3e8dd;border-radius:9px;padding:8px 11px}}
 .pol .nm{{font-size:12px}} .pol select{{width:auto;padding:6px 8px;font-size:12px}}
 .hide{{display:none}} .saved{{color:#7ac943;font-size:12px;margin-left:10px;opacity:0;transition:.3s}}
 .add{{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}} .add>div{{flex:1;min-width:160px}}
</style></head><body>
 <header><h1>{SHIELD} InnaIT RBI Operations Console</h1>
   <span id=svc class="pill on-p">SERVICE: ON</span>&nbsp;&nbsp;<a href=/logout>Sign out</a></header>
 <div class=tabs>
   <button class=on onclick="tab('dash',this)">Dashboard</button>
   <button onclick="tab('sites',this)">Sites &amp; Policies</button>
   <button onclick="tab('set',this)">Settings</button>
 </div>
 <main>
  <section id=dash>
   <div class=grid>
     <div class=stat><div class=k>Active sessions</div><div class=v id=s_active>0</div></div>
     <div class=stat><div class=k>Whitelisted sites</div><div class=v id=s_sites>0</div></div>
     <div class=stat><div class=k>Service</div><div class=v id=s_svc>ON</div></div>
   </div>
   <div class=card><h2>Active sessions <button class="act danger" style="float:right" onclick="killAll()">Stop all</button></h2>
     <table><thead><tr><th>ID</th><th>Client IP</th><th>Site</th><th>Size</th><th>Started</th><th></th></tr></thead>
     <tbody id=sessrows><tr><td colspan=6 style="color:#6f8060">No active sessions</td></tr></tbody></table>
   </div>
  </section>

  <section id=sites class=hide>
   <div class=card><h2>Add a website to run in RBI</h2>
     <div class=add>
       <div><label>Display name</label><input type=text id=ns_name placeholder="InnaIT Demo"></div>
       <div><label>URL</label><input type=text id=ns_url placeholder="innaitdemo.innait.com"></div>
       <button class=act onclick="addSite()">Add site</button>
     </div>
     <div style="color:#6f8060;font-size:11.5px;margin-top:10px">Each site gets its own port automatically. Open it at http://SERVER:PORT — no Docker edits, no restart.</div>
   </div>
   <div id=sitelist></div>
  </section>

  <section id=set class=hide>
   <div class=card><h2>Service settings <span class=saved id=savedSet>Saved</span></h2>
     <div class=row><div><b>Service enabled</b><div style="color:#6f8060;font-size:12px">Master on/off for the RBI stream</div></div>
       <label class=toggle><input type=checkbox id=f_enabled><span></span></label></div>
     <label>Video bitrate (bps)</label><input type=number id=f_bitrate step=500000>
     <label>Max concurrent sessions</label><input type=number id=f_max min=1 max=50>
     <button class=act style="margin-top:16px" onclick="saveSettings()">Save settings</button>
   </div>
  </section>
 </main>
<script>
 let CFG={{}}, PK=[]; const HOST='{SERVER_HOST}'; const BASE={BASE_PORT};
 const POL_LABEL={{read_only:'Read-only mode',scroll_lock:'Scroll lock',copy:'Allow copy text',paste:'Allow paste',clipboard:'Allow clipboard',print:'Allow printing',download:'Allow download',devtools:'Allow DevTools',file_management:'Allow file management'}};
 function tab(id,btn){{document.querySelectorAll('main section').forEach(s=>s.classList.add('hide'));
   document.getElementById(id).classList.remove('hide');
   document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));btn.classList.add('on');}}
 async function post(u,b){{return fetch(u,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b)}})}}
 function flash(id){{const e=document.getElementById(id);e.style.opacity=1;setTimeout(()=>e.style.opacity=0,1500)}}
 async function load(){{
   const r=await fetch('/api/state'); if(r.status==401){{location.href='/';return}} const d=await r.json();
   CFG=d.config; PK=d.policy_keys; const s=d.sessions.sessions||[]; const sites=CFG.sites||[];
   s_active.textContent=s.length; s_sites.textContent=sites.length; s_svc.textContent=CFG.enabled?'ON':'OFF';
   svc.textContent='SERVICE: '+(CFG.enabled?'ON':'OFF'); svc.className='pill '+(CFG.enabled?'on-p':'off-p');
   sessrows.innerHTML = s.length? s.map(x=>`<tr><td>${{x.id.slice(0,8)}}</td><td>${{x.ip||'-'}}</td><td>${{x.target||'-'}}</td><td>${{x.w}}×${{x.h}}</td><td>${{new Date(x.since*1000).toLocaleTimeString()}}</td><td><button class="act danger" onclick="killId('${{x.id}}')">Stop</button></td></tr>`).join('')
     : '<tr><td colspan=6 style="color:#6f8060">No active sessions</td></tr>';
   f_enabled.checked=CFG.enabled; f_bitrate.value=CFG.bitrate; f_max.value=CFG.max_sessions;
   sitelist.innerHTML = sites.map(site=>{{
     const pols = PK.map(k=>`<div class=pol><span class=nm>${{POL_LABEL[k]||k}}</span>
       <select onchange="setPolicy('${{site.id}}','${{k}}',this.value)">
         <option value="off" ${{site.policies[k]?'':'selected'}}>Disable</option>
         <option value="on" ${{site.policies[k]?'selected':''}}>Enable</option></select></div>`).join('');
     const acc = `http://${{HOST}}:${{site.port||BASE}}`;
     return `<div class=site><div class=top><div><b>${{site.name}}</b><div class=url>${{site.url}}</div>
       <div class=acc>Open in RBI: <a href="${{acc}}" target=_blank>${{acc}}</a></div></div>
       <button class="act danger" onclick="delSite('${{site.id}}')">Remove</button></div>
       <div class=polgrid>${{pols}}</div></div>`;
   }}).join('') || '<div class=card style="color:#6f8060">No sites yet — add one above.</div>';
 }}
 async function addSite(){{ if(!ns_url.value.trim())return;
   await post('/api/site/add',{{name:ns_name.value,url:ns_url.value}}); ns_name.value='';ns_url.value=''; load(); }}
 async function delSite(id){{ if(confirm('Remove this site from RBI?')){{await post('/api/site/delete',{{id}});load()}} }}
 async function setPolicy(id,key,val){{ await post('/api/site/policy',{{id,policies:{{[key]:val==='on'}}}}); }}
 async function saveSettings(){{ await post('/api/settings',{{enabled:f_enabled.checked,bitrate:+f_bitrate.value,max_sessions:+f_max.value}}); flash('savedSet'); load(); }}
 async function killAll(){{ if(confirm('Stop all active sessions?')){{await post('/api/command',{{kill:'all'}});load()}} }}
 async function killId(id){{ await post('/api/command',{{kill_id:id}}); load(); }}
 load(); setInterval(load,3000);
</script>
</body></html>"""

def make_app():
    ensure_config()
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_post('/login', login)
    app.router.add_get('/logout', logout)
    app.router.add_get('/api/state', api_state)
    app.router.add_post('/api/settings', api_settings)
    app.router.add_post('/api/site/add', api_site_add)
    app.router.add_post('/api/site/delete', api_site_delete)
    app.router.add_post('/api/site/policy', api_site_policy)
    app.router.add_post('/api/command', api_command)
    return app

if __name__ == '__main__':
    app = make_app()
    print(f'InnaIT RBI Console on :{PORT}  (user={ADMIN_USER})', flush=True)
    web.run_app(app, host='0.0.0.0', port=PORT)
