import os, json, asyncio, threading, subprocess, time, hashlib, shutil, faulthandler
faulthandler.enable()
try:
    import psutil
except Exception:
    psutil = None
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
from gi.repository import Gst, GstWebRTC, GstSdp, GLib
from aiohttp import web, WSMsgType

Gst.init(None)

BASE_PORT  = int(os.environ.get('PORT', '8100'))
TARGET_URL = os.environ.get('TARGET_URL', 'https://www.precisionit.co.in/')
BITRATE    = int(os.environ.get('BITRATE', '4000000'))
TEST       = os.environ.get('TEST_PATTERN', '0') == '1'

# ---- browser: prefer non-snap Google Chrome (robust for many private instances) ----
BROWSER = next((b for b in ['google-chrome-stable', 'google-chrome', 'chromium', 'chromium-browser']
                if shutil.which(b)), 'chromium')

# ---- hardware-sized, load-aware capacity ----
CORES = os.cpu_count() or 4
if psutil:
    TOTAL_GB = psutil.virtual_memory().total / (1024**3)
else:
    TOTAL_GB = 8.0
CAP_BY_CORES = max(1, CORES - 1)                 # leave 1 core for OS/Python/Xvfb
CAP_BY_RAM   = max(1, int((TOTAL_GB - 2) / 0.5)) # reserve 2GB, ~0.5GB per private browser
AUTO_CAP     = min(CAP_BY_CORES, CAP_BY_RAM)
MAX_SESSIONS = int(os.environ.get('MAX_SESSIONS', str(min(AUTO_CAP, 3))))   # pilot: 3 on this 4-core box
CPU_CEIL     = float(os.environ.get('CPU_CEIL', '85'))
RAM_CEIL     = float(os.environ.get('RAM_CEIL', '85'))

APP_LOOP = None
RUNNER = None
STARTED = {}                 # port -> TCPSite
SESSIONS = {}                # session_id -> Session (every PRIVATE viewer)
SESS_LOCK = threading.Lock()
LOAD = {'cpu': 0.0, 'ram': 0.0}

# pool of X display numbers, one per active private viewer
DISPLAY_POOL = list(range(101, 101 + 64))
POOL_LOCK = threading.Lock()

def alloc_display():
    with POOL_LOCK:
        return DISPLAY_POOL.pop(0) if DISPLAY_POOL else None

def free_display(num):
    with POOL_LOCK:
        if num not in DISPLAY_POOL: DISPLAY_POOL.append(num); DISPLAY_POOL.sort()

XDG_DIR = os.environ.get('XDG_RUNTIME_DIR', '/tmp/rbi-runtime')
try:
    os.makedirs(XDG_DIR, exist_ok=True); os.chmod(XDG_DIR, 0o700)
    os.makedirs('/tmp/rbi-home', exist_ok=True)
except Exception: pass

def env_for(display):
    return dict(os.environ, DISPLAY=display, DBUS_SESSION_BUS_ADDRESS='disabled:',
                XDG_RUNTIME_DIR=XDG_DIR, HOME='/tmp/rbi-home')

DEVNULL = subprocess.DEVNULL
SHARED   = os.environ.get('SHARED_DIR', '/shared')
CONFIG_F = os.path.join(SHARED, 'config.json')
SESSIONS_F = os.path.join(SHARED, 'sessions.json')
COMMANDS_F = os.path.join(SHARED, 'commands.json')

def read_config():
    base = {"enabled": True, "bitrate": BITRATE, "max_sessions": MAX_SESSIONS,
            "sites": [{"id": "default", "name": "Default", "url": TARGET_URL,
                       "port": BASE_PORT, "policies": {}}]}
    try:
        with open(CONFIG_F) as f: base.update(json.load(f))
    except Exception: pass
    if not base.get("sites"):
        base["sites"] = [{"id": "default", "name": "Default", "url": TARGET_URL,
                          "port": BASE_PORT, "policies": {}}]
    for s in base["sites"]:
        if not s.get("port"): s["port"] = BASE_PORT
    return base

def site_for_port(cfg, port):
    for s in cfg.get("sites", []):
        if int(s.get("port", 0)) == int(port): return s
    sites = cfg.get("sites", [])
    return sites[0] if sites else None

def effective_cap(cfg):
    # admin can lower it, but never above the hardware ceiling
    try: c = int(cfg.get('max_sessions', MAX_SESSIONS))
    except Exception: c = MAX_SESSIONS
    return max(1, min(c, MAX_SESSIONS))

def load_ok():
    if psutil is None: return True
    return LOAD['cpu'] < CPU_CEIL and LOAD['ram'] < RAM_CEIL

def write_sessions(items):
    try:
        os.makedirs(SHARED, exist_ok=True)
        tmp = SESSIONS_F + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({"sessions": items, "updated": int(time.time()),
                       "cpu": round(LOAD['cpu'], 1), "ram": round(LOAD['ram'], 1),
                       "cap": MAX_SESSIONS, "used": len(items)}, f)
        os.replace(tmp, SESSIONS_F)
    except Exception: pass

def read_commands():
    try:
        with open(COMMANDS_F) as f: return json.load(f)
    except Exception: return {}

def clear_commands():
    try:
        with open(COMMANDS_F, 'w') as f: json.dump({}, f)
    except Exception: pass

def even(n, lo, hi):
    n = int(round(n)); n = max(lo, min(hi, n))
    return n - (n % 2)

def wait_x(display, timeout=6.0):
    env = env_for(display); t = time.time()
    while time.time() - t < timeout:
        if subprocess.run(['xdpyinfo', '-display', display], env=env,
                          stdout=DEVNULL, stderr=DEVNULL).returncode == 0:
            return True
        time.sleep(0.12)
    return False

def wait_window(display, timeout=8.0):
    env = env_for(display); t = time.time()
    while time.time() - t < timeout:
        r = subprocess.run(['xdotool', 'search', '--name', '.'], env=env,
                           stdout=subprocess.PIPE, stderr=DEVNULL)
        if r.stdout.strip(): return True
        time.sleep(0.18)
    return False

def _devtools_policy(devtools):
    try:
        d = '/etc/chromium/policies/managed'; os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'policy.json'), 'w') as f:
            json.dump({} if devtools else {"DeveloperToolsAvailability": 2}, f)
        d2 = '/etc/opt/chrome/policies/managed'; os.makedirs(d2, exist_ok=True)
        with open(os.path.join(d2, 'policy.json'), 'w') as f:
            json.dump({} if devtools else {"DeveloperToolsAvailability": 2}, f)
    except Exception: pass

BASE_FLAGS = [
    '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    '--no-first-run', '--no-default-browser-check', '--kiosk', '--start-fullscreen',
    '--disable-pinch', '--overscroll-history-navigation=0',
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
    '--disable-background-networking', '--disable-sync', '--disable-default-apps',
    '--disable-component-update', '--disable-domain-reliability', '--no-service-autorun',
    '--disable-features=Translate,MediaRouter,OptimizationHints,DialMediaRouteProvider,BackForwardCache',
    '--password-store=basic', '--use-mock-keychain', '--metrics-recording-only',
    '--mute-audio', '--disable-breakpad', '--log-level=3', '--force-device-scale-factor=1',
    '--no-pings', '--disable-hang-monitor', '--disk-cache-size=104857600',
]

def spawn_private(num, w, h, target, devtools):
    """Start a dedicated Xvfb + browser for ONE viewer on display :num."""
    display = f':{num}'; env = env_for(display); udd = f'/tmp/rbi-prof-{num}'
    _devtools_policy(devtools)
    subprocess.run(['pkill', '-9', '-f', f'Xvfb {display} '], stderr=DEVNULL)
    subprocess.run(['pkill', '-9', '-f', f'rbi-prof-{num}'], stderr=DEVNULL)
    subprocess.run(['rm', '-rf', udd], stderr=DEVNULL)
    subprocess.run(['rm', '-f', f'/tmp/.X{num}-lock', f'/tmp/.X11-unix/X{num}'], stderr=DEVNULL)
    time.sleep(0.2)
    subprocess.Popen(['Xvfb', display, '-screen', '0', f'{w}x{h}x24', '-nolisten', 'tcp', '-noreset'])
    wait_x(display)
    cmd = [BROWSER] + BASE_FLAGS + [f'--window-size={w},{h}', '--window-position=0,0',
           f'--user-data-dir={udd}', f'--class=rbi-prof-{num}', f'--display={display}', target]
    subprocess.Popen(cmd, env=env)
    if not wait_window(display):
        subprocess.run(['pkill', '-9', '-f', f'rbi-prof-{num}'], stderr=DEVNULL); time.sleep(0.4)
        subprocess.Popen(cmd, env=env); wait_window(display)
    time.sleep(0.3)
    print(f'[{display}] private env up ({BROWSER}) -> {target} {w}x{h}', flush=True)
    return display, env

def teardown_private(num):
    display = f':{num}'
    subprocess.run(['pkill', '-9', '-f', f'rbi-prof-{num}'], stderr=DEVNULL)
    subprocess.run(['pkill', '-9', '-f', f'Xvfb {display} '], stderr=DEVNULL)
    subprocess.run(['rm', '-rf', f'/tmp/rbi-prof-{num}'], stderr=DEVNULL)
    print(f'[{display}] private env torn down', flush=True)

SPECIAL = {'Enter':'Return','Backspace':'BackSpace','Tab':'Tab','Escape':'Escape',
           'ArrowUp':'Up','ArrowDown':'Down','ArrowLeft':'Left','ArrowRight':'Right',
           ' ':'space','Delete':'Delete','Home':'Home','End':'End','PageUp':'Prior',
           'PageDown':'Next','Insert':'Insert','F1':'F1','F2':'F2','F3':'F3','F4':'F4',
           'F5':'F5','F6':'F6','F7':'F7','F8':'F8','F9':'F9','F10':'F10','F11':'F11','F12':'F12'}
KEYSYM = {'.':'period',',':'comma','/':'slash','\\':'backslash',';':'semicolon',
          "'":'apostrophe','`':'grave','-':'minus','=':'equal','[':'bracketleft',']':'bracketright'}

def keysym(k):
    if k in SPECIAL: return SPECIAL[k]
    if k in KEYSYM: return KEYSYM[k]
    if len(k) == 1: return k
    return None

def mods_of(m):
    out = []
    if m.get('ctrl'):  out.append('ctrl')
    if m.get('alt'):   out.append('alt')
    if m.get('shift'): out.append('shift')
    if m.get('meta'):  out.append('super')
    return out

def xdo(env, args):
    try: subprocess.run(['xdotool'] + args, env=env, timeout=2, stdout=DEVNULL, stderr=DEVNULL)
    except Exception as e: print('xdotool err', e, flush=True)

def handle_input(m, env, devtools=False):
    t = m.get('type'); x, y = str(int(m.get('x', 0))), str(int(m.get('y', 0)))
    if t == 'mousemove':   xdo(env, ['mousemove', x, y])
    elif t == 'mousedown': xdo(env, ['mousemove', x, y, 'mousedown', str(m.get('button', '1'))])
    elif t == 'mouseup':   xdo(env, ['mousemove', x, y, 'mouseup', str(m.get('button', '1'))])
    elif t == 'wheel':     xdo(env, ['click', '4' if m.get('deltaY', 0) < 0 else '5'])
    elif t == 'key':
        k = m.get('key', '')
        if k in ('Control','Alt','Shift','Meta','CapsLock','AltGraph'): return
        mods = mods_of(m)
        if not devtools and (k == 'F12' or (('ctrl' in mods) and ('shift' in mods) and k.lower() in ('i','j','c'))):
            return
        sym = keysym(k)
        if sym is None: return
        if mods:
            for mod in mods: xdo(env, ['keydown', mod])
            xdo(env, ['key', sym])
            for mod in reversed(mods): xdo(env, ['keyup', mod])
        elif k in SPECIAL: xdo(env, ['key', sym])
        elif len(k) == 1:  xdo(env, ['type', k])

def handle_nav(a, env):
    key = {'back':'alt+Left','forward':'alt+Right','reload':'F5'}.get(a)
    if not key: return
    try:
        r = subprocess.run(['xdotool', 'search', '--name', '.'], env=env,
                           stdout=subprocess.PIPE, stderr=DEVNULL, timeout=2)
        wins = r.stdout.decode().split()
        if wins:
            subprocess.run(['xdotool', 'windowactivate', '--sync', wins[-1]], env=env,
                           stdout=DEVNULL, stderr=DEVNULL, timeout=2)
    except Exception: pass
    xdo(env, ['key', '--clearmodifiers', key])

class Session:
    def __init__(self, ws, w, h, ip, target, policies, num, display, env):
        self.ws, self.w, self.h = ws, w, h
        self.id = hashlib.md5(f'{time.time()}{ip}{num}'.encode()).hexdigest()
        self.ip, self.since, self.target = ip, int(time.time()), target
        self.num, self.display, self.env = num, display, env
        self.policies = policies or {}
        self.read_only = bool(self.policies.get('read_only'))
        self.scroll_lock = bool(self.policies.get('scroll_lock'))
        self.devtools = bool(self.policies.get('devtools'))
        self.pipe = self.webrtc = None
        self.remote_set = False; self.pending_ice = []

    def build(self):
        src = ("videotestsrc is-live=true pattern=ball" if TEST else
               f"ximagesrc display-name={self.display} use-damage=false show-pointer=false")
        desc = (
            f"{src} ! video/x-raw,framerate=30/1 ! videoconvert ! "
            f"queue max-size-buffers=4 max-size-time=0 max-size-bytes=0 leaky=downstream ! "
            f"vp8enc deadline=1 cpu-used=5 threads=2 end-usage=cbr lag-in-frames=0 "
            f"error-resilient=1 target-bitrate={BITRATE} keyframe-max-dist=30 ! "
            f"rtpvp8pay pt=96 ! application/x-rtp,media=video,encoding-name=VP8,payload=96,clock-rate=90000 ! "
            f"webrtcbin name=sendrecv bundle-policy=max-bundle latency=0"
        )
        print(f'[{self.display}] pipeline up', flush=True)
        self.pipe = Gst.parse_launch(desc)
        self.webrtc = self.pipe.get_by_name('sendrecv')
        self.webrtc.connect('on-negotiation-needed', self.on_neg)
        self.webrtc.connect('on-ice-candidate', self.on_ice)
        bus = self.pipe.get_bus(); bus.add_signal_watch()
        bus.connect('message::error', lambda b, m: print('GST ERROR:', m.parse_error(), flush=True))
        self.pipe.set_state(Gst.State.PLAYING)
        return False

    def on_neg(self, el):
        el.emit('create-offer', None, Gst.Promise.new_with_change_func(self.on_offer, None))

    def on_offer(self, promise, _):
        try:
            if promise.wait() != Gst.PromiseResult.REPLIED: return
            reply = promise.get_reply()
            if reply is None: return
            offer = reply.get_value('offer')
            if offer is None: return
            text = offer.sdp.as_text()
            p2 = Gst.Promise.new()
            self.webrtc.emit('set-local-description', offer, p2); p2.interrupt()
            self.send({'type':'sdp','sdp':{'type':'offer','sdp':text}})
        except Exception as e:
            print('on_offer error:', e, flush=True)

    def on_ice(self, el, mline, cand):
        self.send({'type':'ice','candidate':{'candidate':cand,'sdpMLineIndex':mline}})

    def send(self, msg):
        if APP_LOOP and not self.ws.closed:
            asyncio.run_coroutine_threadsafe(self.ws.send_str(json.dumps(msg)), APP_LOOP)

    def on_answer(self, sdp):
        def do():
            _, m = GstSdp.SDPMessage.new()
            GstSdp.sdp_message_parse_buffer(sdp.encode(), m)
            ans = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, m)
            self.webrtc.emit('set-remote-description', ans, Gst.Promise.new())
            self.remote_set = True
            for mline, cand in self.pending_ice: self.webrtc.emit('add-ice-candidate', mline, cand)
            self.pending_ice = []
            return False
        GLib.idle_add(do)

    def add_ice(self, mline, cand):
        def do():
            if not self.remote_set: self.pending_ice.append((mline, cand))
            else: self.webrtc.emit('add-ice-candidate', mline, cand)
            return False
        GLib.idle_add(do)

    def close(self):
        if self.pipe:
            self.pipe.set_state(Gst.State.NULL); self.pipe = None

INPUT_TYPES = {'mousemove','mousedown','mouseup','wheel','key'}

def conn_port(request):
    try:
        sock = request.transport.get_extra_info('sockname')
        if sock and len(sock) >= 2: return int(sock[1])
    except Exception: pass
    return BASE_PORT

async def index(request):
    return web.FileResponse('public/index.html')

async def whoami(request):
    cfg = read_config(); s = site_for_port(cfg, conn_port(request))
    if not cfg.get('enabled', True) or s is None:
        return web.json_response({"enabled": False})
    return web.json_response({"enabled": True, "name": s.get("name"), "url": s.get("url"),
                              "port": s.get("port")})

async def loadinfo(request):
    with SESS_LOCK: used = len(SESSIONS)
    return web.json_response({"cpu": round(LOAD['cpu'],1), "ram": round(LOAD['ram'],1),
                              "cap": MAX_SESSIONS, "used": used, "browser": BROWSER})

async def ws_handler(request):
    ws = web.WebSocketResponse(); await ws.prepare(request)
    cfg = read_config(); port = conn_port(request); site = site_for_port(cfg, port)
    if not cfg.get('enabled', True) or site is None:
        try: await ws.send_str(json.dumps({'type':'disabled'}))
        except Exception: pass
        await ws.close(); return ws

    # ---- ADMISSION CONTROL: numeric cap + live load gate ----
    cap = effective_cap(cfg)
    with SESS_LOCK: used = len(SESSIONS)
    if used >= cap or not load_ok():
        try: await ws.send_str(json.dumps({'type':'capacity',
              'used': used, 'cap': cap, 'cpu': round(LOAD['cpu'],1), 'ram': round(LOAD['ram'],1)}))
        except Exception: pass
        await ws.close(); return ws

    num = alloc_display()
    if num is None:
        try: await ws.send_str(json.dumps({'type':'capacity','used':used,'cap':cap}))
        except Exception: pass
        await ws.close(); return ws

    ip = request.remote or ''; sess = None
    try:
        first = await ws.receive()
        w, h = 1280, 720
        if first.type == WSMsgType.TEXT:
            d = json.loads(first.data)
            if d.get('type') == 'resize':
                w = even(d.get('width', 1280), 320, 2560)
                h = even(d.get('height', 720), 240, 1440)
        target = site['url']; policies = site.get('policies', {})
        if TEST:
            display, env = f':{num}', env_for(f':{num}')
        else:
            display, env = await asyncio.get_event_loop().run_in_executor(
                None, spawn_private, num, w, h, target, bool(policies.get('devtools')))
        if ws.closed:
            await asyncio.get_event_loop().run_in_executor(None, teardown_private, num)
            free_display(num); return ws
        try:
            await ws.send_str(json.dumps({'type':'config','width':w,'height':h,
                  'name':site.get('name'),'url':target,'policies':policies}))
        except Exception:
            await asyncio.get_event_loop().run_in_executor(None, teardown_private, num)
            free_display(num); return ws
        sess = Session(ws, w, h, ip, target, policies, num, display, env)
        with SESS_LOCK: SESSIONS[sess.id] = sess
        GLib.idle_add(sess.build)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT: continue
            d = json.loads(msg.data); mt = d.get('type')
            if mt == 'sdp' and d['sdp']['type'] == 'answer':
                sess.on_answer(d['sdp']['sdp'])
            elif mt == 'ice':
                c = d['candidate']; sess.add_ice(c['sdpMLineIndex'], c['candidate'])
            elif sess.read_only:
                continue
            elif mt == 'nav':
                handle_nav(d.get('action'), sess.env)
            elif mt in INPUT_TYPES:
                if mt == 'wheel' and sess.scroll_lock: continue
                handle_input(d, sess.env, sess.devtools)
    finally:
        if sess:
            sess.close()
            with SESS_LOCK: SESSIONS.pop(sess.id, None)
        if not TEST:
            await asyncio.get_event_loop().run_in_executor(None, teardown_private, num)
        free_display(num)
    return ws

def glib_thread():
    GLib.MainLoop().run()

def make_app():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/ws', ws_handler)
    app.router.add_get('/whoami', whoami)
    app.router.add_get('/load', loadinfo)
    app.router.add_static('/assets', 'public')
    return app

async def ensure_port(port):
    if port in STARTED: return
    try:
        ts = web.TCPSite(RUNNER, '0.0.0.0', port)
        await ts.start(); STARTED[port] = ts
        print(f'listening on :{port}', flush=True)
    except Exception as e:
        print(f'could not bind :{port}: {e}', flush=True)

async def monitor():
    while True:
        await asyncio.sleep(2)
        if psutil:
            try:
                LOAD['cpu'] = psutil.cpu_percent(interval=None)
                LOAD['ram'] = psutil.virtual_memory().percent
            except Exception: pass
        cfg = read_config()
        want = {int(s['port']) for s in cfg.get('sites', []) if s.get('port')}
        for p in want - set(STARTED): await ensure_port(p)
        for p in set(STARTED) - want:
            try: await STARTED[p].stop()
            except Exception: pass
            STARTED.pop(p, None)
        with SESS_LOCK:
            items = [{"id": s.id, "ip": s.ip, "since": s.since, "w": s.w, "h": s.h,
                      "target": s.target} for s in SESSIONS.values()]
        write_sessions(items)
        cmd = read_commands()
        if cmd:
            with SESS_LOCK: targets = list(SESSIONS.values())
            for s in targets:
                if cmd.get('kill') == 'all' or cmd.get('kill_id') == s.id:
                    try: await s.ws.close()
                    except Exception: pass
            clear_commands()
        if not cfg.get('enabled', True):
            with SESS_LOCK: targets = list(SESSIONS.values())
            for s in targets:
                try: await s.ws.close()
                except Exception: pass

async def main():
    global APP_LOOP, RUNNER
    APP_LOOP = asyncio.get_running_loop()
    threading.Thread(target=glib_thread, daemon=True).start()
    app = make_app()
    RUNNER = web.AppRunner(app); await RUNNER.setup()
    cfg = read_config()
    for s in cfg.get('sites', []):
        if s.get('port'): await ensure_port(int(s['port']))
    if not STARTED: await ensure_port(BASE_PORT)
    asyncio.ensure_future(monitor())
    print(f'WebRTC RBI (PRIVATE) up | browser={BROWSER} | cores={CORES} ram={TOTAL_GB:.1f}GB '
          f'| cap={MAX_SESSIONS} (cores={CAP_BY_CORES}, ram={CAP_BY_RAM}) | psutil={"yes" if psutil else "no"}',
          flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
