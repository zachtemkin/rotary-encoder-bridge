#!/usr/bin/env python3
import asyncio, json, os, glob
from aiohttp import web
import serial
import serial_asyncio

# Configuration - Change this to switch between MAC and PI modes
MODE = "MAC"  # Options: "MAC" or "PI"

HTTP_PORT = 8765
SERIAL_BAUD = 115200

def find_serial_port():
    if MODE == "MAC":
        # macOS serial port detection
        for pattern in ["/dev/tty.usbmodem*", "/dev/cu.usbmodem*"]:
            ports = glob.glob(pattern)
            if ports:
                return ports[0]  # Return the first found port
        raise RuntimeError("No macOS serial port found for QT Py")
    
    elif MODE == "PI":
        # Raspberry Pi / Linux serial port detection (unchanged from working code)
        by_id = glob.glob("/dev/serial/by-id/*")
        for p in by_id:
            if ("Adafruit" in p) or ("QTPy" in p) or ("ESP32S2" in p) or ("ESP32-S2" in p):
                return os.path.realpath(p)
        for guess in ["/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0"]:
            if os.path.exists(guess):
                return guess
        raise RuntimeError("No serial port found for QT Py")
    
    else:
        raise ValueError(f"Invalid MODE: {MODE}. Must be 'MAC' or 'PI'")

serial_port_path = find_serial_port()
print(f"[bridge] Using serial port: {serial_port_path}")

clients = set()

async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    clients.add(ws)
    print(f"[ws] client connected ({len(clients)} total)")
    try:
        async for _ in ws:
            pass
    finally:
        clients.discard(ws)
        print(f"[ws] client disconnected ({len(clients)} total)")
    return ws

async def health(_):
    return web.json_response({"ok": True})

# NOTE: this is now a **Unicode** string (no leading b)
INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Encoder Live</title>
<style>
  html,body{margin:0;font:16px/1.4 system-ui,Arial;}
  .wrap{max-width:640px;margin:32px auto;padding:16px}
  .card{border:1px solid #ddd;border-radius:16px;padding:16px;box-shadow:0 2px 12px rgba(0,0,0,0.06)}
  .row{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  .pill{padding:6px 10px;border-radius:999px;background:#f1f5f9;border:1px solid #e2e8f0}
  .big{font-size:48px;font-weight:700;letter-spacing:.5px}
  code{background:#f6f8fa;border:1px solid #e2e8f0;border-radius:8px;padding:2px 6px}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h2>Seesaw Rotary (USB → WebSocket)</h2>
    <div class="row"><div>Status:</div><div id="status" class="pill">connecting…</div></div>
    <p>Open in Chromium kiosk or any browser on the Pi. Turning the encoder streams events below.</p>
    <div class="row">
      <div>Detents</div>
      <div id="detents" class="big">0</div>
    </div>
    <div class="row">
      <div>Last Δ</div>
      <div id="delta" class="pill">0</div>
      <div>Raw</div>
      <div id="raw" class="pill">0</div>
    </div>
    <h3>Event log</h3>
    <pre id="log" style="height:240px;overflow:auto;background:#0b1020;color:#e6edf3;padding:12px;border-radius:8px"></pre>
    <p>WS URL: <code id="wsurl"></code></p>
  </div>
</div>
<script>
(function(){
  const wsURL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  document.getElementById("wsurl").textContent = wsURL;

  let detentsEl = document.getElementById("detents");
  let deltaEl = document.getElementById("delta");
  let rawEl = document.getElementById("raw");
  let logEl = document.getElementById("log");
  let statusEl = document.getElementById("status");

  function append(line){
    logEl.textContent += line + "\\n";
    logEl.scrollTop = logEl.scrollHeight;
  }

  function connect(){
    const ws = new WebSocket(wsURL);
    ws.onopen = () => { statusEl.textContent = "connected"; };
    ws.onclose = () => { statusEl.textContent = "disconnected (reconnecting…)"; setTimeout(connect, 1000); };
    ws.onerror = () => { statusEl.textContent = "error (reconnecting…)"; ws.close(); };
    ws.onmessage = (ev) => {
      try{
        const msg = JSON.parse(ev.data);
        if(msg.ev === "turn"){
          detentsEl.textContent = msg.detents ?? 0;
          deltaEl.textContent = (msg.delta>0?"+":"") + (msg.delta ?? 0);
          rawEl.textContent = msg.raw ?? 0;
        }
        if(msg.ev === "press") append("↓ press @ " + msg.t);
        else if(msg.ev === "release") append("↑ release @ " + msg.t);
        else if(msg.ev === "turn") append("↻ turn  raw:"+msg.raw+" detents:"+msg.detents+" Δ:"+msg.delta);
        else if(msg.ev === "boot") append("boot raw:"+msg.raw+" detents:"+msg.detents);
      } catch(e){
        append("parse error: " + e);
      }
    };
  }
  connect();
})();
</script>
</body>
</html>
"""

async def index(_):
    return web.Response(text=INDEX_HTML, content_type="text/html", charset="utf-8")


async def serial_reader_task():
    # Open serial exclusively, then reopen via asyncio
    ser = serial.Serial(serial_port_path, SERIAL_BAUD, timeout=0)
    ser.close()
    reader, _ = await serial_asyncio.open_serial_connection(url=serial_port_path, baudrate=SERIAL_BAUD)
    buff = b""
    print("[serial] reader started")
    while True:
        chunk = await reader.read(256)
        if not chunk:
            await asyncio.sleep(0.01)
            continue
        buff += chunk
        while b"\n" in buff:
            line, buff = buff.split(b"\n", 1)
            txt = line.strip().decode("utf-8", "ignore")
            if not txt:
                continue
            try:
                msg = json.loads(txt)
            except Exception:
                msg = {"t": None, "ev": "raw", "line": txt}
            if clients:
                data = json.dumps(msg)
                dead = []
                # send_str wants a Python str; aiohttp handles UTF-8
                for c in list(clients):
                    try:
                        await c.send_str(data)
                    except Exception:
                        dead.append(c)
                for d in dead:
                    clients.discard(d)

async def main():
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/health", health),
        web.get("/ws", ws_handler),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    print(f"[http] serving http/ws on :{HTTP_PORT}")
    await serial_reader_task()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
