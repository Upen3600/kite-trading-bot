"""
╔══════════════════════════════════════════════════════════════════╗
║   TRADING DASHBOARD — Tick-by-Tick Real Time                     ║
║   KiteTicker WebSocket → Flask-SocketIO → Browser               ║
║   Exactly like Kite app — no polling, pure push                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import json
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, render_template_string
from flask_socketio import SocketIO
from kiteconnect import KiteTicker

IST        = ZoneInfo("Asia/Kolkata")
TRADE_FILE = "/tmp/trades_log.json"
MARKET_FILE= "/tmp/market_data.json"
PORT       = int(os.environ.get("PORT", 8080))

API_KEY          = os.environ.get("API_KEY",          "yj3cey9o0ho0gi1b")
BANKNIFTY_TOKEN  = int(os.environ.get("BANKNIFTY_TOKEN", "260105"))
NIFTY_TOKEN      = int(os.environ.get("NIFTY_TOKEN",     "256265"))

log = logging.getLogger(__name__)

app       = Flask(__name__)
app.config["SECRET_KEY"] = "trading_secret_2025"
socketio  = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Global state — latest tick data
_tick_data = {
    "bn": {"ltp": 0, "high": 0, "low": 0, "close": 0, "open": 0,
           "change": 0, "change_pct": 0, "ema50": 0, "ema200": 0, "volume": 0},
    "nf": {"ltp": 0, "high": 0, "low": 0, "close": 0, "open": 0,
           "change": 0, "change_pct": 0, "ema50": 0, "ema200": 0, "volume": 0},
}
_ticker    = None
_access_token = None

TOKEN_MAP = {
    BANKNIFTY_TOKEN: "bn",
    NIFTY_TOKEN:     "nf",
}


# ─────────────────────────────────────────────
#  KITE TICKER — WebSocket
# ─────────────────────────────────────────────
def start_ticker(access_token: str):
    global _ticker, _access_token
    _access_token = access_token

    if _ticker:
        try:
            _ticker.stop()
        except Exception:
            pass

    _ticker = KiteTicker(API_KEY, access_token)

    def on_ticks(ws, ticks):
        for tick in ticks:
            token = tick.get("instrument_token")
            sym   = TOKEN_MAP.get(token)
            if not sym:
                continue

            ltp   = tick.get("last_price", 0)
            ohlc  = tick.get("ohlc", {})
            high  = tick.get("depth", {}).get("buy", [{}])[0].get("price", 0) or ohlc.get("high", ltp)
            # Use day high/low from tick
            high  = tick.get("ohlc", {}).get("high", ltp)
            low   = tick.get("ohlc", {}).get("low",  ltp)
            close = tick.get("ohlc", {}).get("close", ltp)  # prev close
            opn   = tick.get("ohlc", {}).get("open",  ltp)
            vol   = tick.get("volume_traded", 0)
            chg   = round(ltp - close, 2) if close else 0
            chgp  = round((chg / close) * 100, 2) if close else 0

            _tick_data[sym].update({
                "ltp":        round(ltp,   2),
                "high":       round(high,  2),
                "low":        round(low,   2),
                "close":      round(close, 2),
                "open":       round(opn,   2),
                "volume":     vol,
                "change":     chg,
                "change_pct": chgp,
                "time":       datetime.now(IST).strftime("%H:%M:%S"),
            })

            # Push to all connected browsers instantly
            socketio.emit("tick", {
                "sym":        sym,
                "ltp":        round(ltp,   2),
                "high":       round(high,  2),
                "low":        round(low,   2),
                "close":      round(close, 2),
                "open":       round(opn,   2),
                "volume":     vol,
                "change":     chg,
                "change_pct": chgp,
                "ema50":      _tick_data[sym].get("ema50",  0),
                "ema200":     _tick_data[sym].get("ema200", 0),
                "time":       datetime.now(IST).strftime("%H:%M:%S"),
            })

        # Also update market file for bot
        _save_market_file()

    def on_connect(ws, response):
        log.info("✅ KiteTicker WebSocket connected.")
        ws.subscribe([BANKNIFTY_TOKEN, NIFTY_TOKEN])
        ws.set_mode(ws.MODE_FULL, [BANKNIFTY_TOKEN, NIFTY_TOKEN])
        socketio.emit("ws_status", {"connected": True})

    def on_disconnect(ws, code, reason):
        log.warning(f"⚠️ KiteTicker disconnected: {code} {reason}")
        socketio.emit("ws_status", {"connected": False})

    def on_error(ws, code, reason):
        log.error(f"❌ KiteTicker error: {code} {reason}")

    def on_reconnect(ws, attempt):
        log.info(f"🔄 KiteTicker reconnecting... attempt {attempt}")

    def on_noreconnect(ws):
        log.error("❌ KiteTicker: max reconnects reached.")

    _ticker.on_ticks       = on_ticks
    _ticker.on_connect     = on_connect
    _ticker.on_disconnect  = on_disconnect
    _ticker.on_error       = on_error
    _ticker.on_reconnect   = on_reconnect
    _ticker.on_noreconnect = on_noreconnect

    # Run in background thread (non-blocking)
    t = threading.Thread(target=_ticker.connect, kwargs={"threaded": True}, daemon=True)
    t.start()
    log.info("📡 KiteTicker started in background thread.")


def update_ema(sym: str, ema50: float, ema200: float):
    """Called by bot after calculating EMAs — push to dashboard."""
    _tick_data[sym]["ema50"]  = round(ema50,  2)
    _tick_data[sym]["ema200"] = round(ema200, 2)
    socketio.emit("ema_update", {"sym": sym, "ema50": round(ema50, 2), "ema200": round(ema200, 2)})


def _save_market_file():
    try:
        with open(MARKET_FILE, "w") as f:
            json.dump(_tick_data, f)
    except Exception:
        pass


# ─────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/trades")
def api_trades():
    if os.path.exists(TRADE_FILE):
        try:
            with open(TRADE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

@app.route("/api/snapshot")
def api_snapshot():
    """Initial snapshot for newly connected browsers."""
    trades = []
    if os.path.exists(TRADE_FILE):
        try:
            with open(TRADE_FILE) as f:
                trades = json.load(f)
        except Exception:
            pass
    return {"market": _tick_data, "trades": trades}


# ─────────────────────────────────────────────
#  SOCKETIO EVENTS
# ─────────────────────────────────────────────
@socketio.on("connect")
def on_client_connect():
    log.info("Browser connected to dashboard.")
    # Send full snapshot to new client
    trades = []
    if os.path.exists(TRADE_FILE):
        try:
            with open(TRADE_FILE) as f:
                trades = json.load(f)
        except Exception:
            pass
    socketio.emit("snapshot", {"market": _tick_data, "trades": trades})


# ─────────────────────────────────────────────
#  HTML
# ─────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hybrid Bot — Live Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0b0d14;color:#e2e8f0;min-height:100vh}

header{background:#12151f;border-bottom:1px solid #1e2535;padding:12px 20px;
  display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.hdr-left{display:flex;align-items:center;gap:12px}
.logo{font-size:15px;font-weight:700;color:#fff}
.pill{display:flex;align-items:center;gap:5px;background:#1a2035;
  border:1px solid #2d3748;border-radius:20px;padding:3px 10px;font-size:11px;color:#a0aec0}
.dot{width:7px;height:7px;border-radius:50%}
.dot-g{background:#48bb78;box-shadow:0 0 5px #48bb7880;animation:p 1.5s infinite}
.dot-r{background:#fc8181}
.dot-y{background:#f6c90e;animation:p 1.5s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}

.clock-box{text-align:right}
.clock-t{font-size:20px;font-weight:800;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:1.5px}
.clock-d{font-size:11px;color:#4a5568;margin-top:2px}

.container{max-width:1200px;margin:0 auto;padding:18px 14px}
.slabel{font-size:10px;color:#4a5568;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:8px;padding-left:2px}

/* ── Market Cards ── */
.mstrip{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px}
@media(max-width:620px){.mstrip{grid-template-columns:1fr}}

.mcard{background:#12151f;border:1px solid #1e2535;border-radius:16px;padding:16px 18px;position:relative;overflow:hidden}
.mcard.bn{border-top:3px solid #667eea}
.mcard.nf{border-top:3px solid #f5576c}

.mc-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.mc-name{font-size:11px;font-weight:700;color:#a0aec0;letter-spacing:1px}
.mc-badge{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700}
.bo{background:#1a3a2a;color:#48bb78}
.bc{background:#2a1a1a;color:#fc8181}
.bp{background:#2a2a1a;color:#f6c90e}

.ltp-row{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}
.ltp{font-size:32px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;
  letter-spacing:-.5px;transition:color .15s}
.ltp.flash-up{color:#48bb78 !important;text-shadow:0 0 12px #48bb7860}
.ltp.flash-dn{color:#fc8181 !important;text-shadow:0 0 12px #fc818160}

.chg-box{display:flex;flex-direction:column;align-items:flex-start;gap:2px}
.chg{font-size:13px;font-weight:700;padding:2px 8px;border-radius:7px}
.cup{background:#1a3a2a;color:#48bb78} .cdn{background:#3a1a1a;color:#fc8181} .cfl{background:#1e2535;color:#718096}
.tick-time{font-size:10px;color:#4a5568}

.hl-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px}
.hl-box{background:#0f1117;border-radius:8px;padding:7px 10px}
.hl-lbl{font-size:9px;color:#4a5568;text-transform:uppercase;letter-spacing:.6px;margin-bottom:3px}
.hl-v{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.hv-h{color:#48bb78} .hv-l{color:#fc8181} .hv-c{color:#a0aec0}

.ema-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding-top:10px;border-top:1px solid #1e2535}
.ema-box{background:#0f1117;border-radius:8px;padding:8px 10px}
.ema-lbl{font-size:9px;color:#4a5568;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px}
.ema-v{font-size:14px;font-weight:800;font-variant-numeric:tabular-nums}
.ema-sig{font-size:10px;margin-top:3px}
.sg{color:#48bb78} .sr{color:#fc8181}

/* volume bar */
.vol-row{margin-top:8px}
.vol-lbl{font-size:9px;color:#4a5568;margin-bottom:3px}
.vol-bar{background:#1e2535;border-radius:3px;height:4px}
.vol-fill{height:4px;border-radius:3px;background:#667eea;transition:width .5s}

/* ── Summary ── */
.sgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-bottom:20px}
.sc{background:#12151f;border:1px solid #1e2535;border-radius:12px;padding:13px 15px}
.sc .sl{font-size:9px;color:#4a5568;text-transform:uppercase;letter-spacing:.8px;margin-bottom:5px}
.sc .sv{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.sc .ss{font-size:11px;color:#4a5568;margin-top:2px}
.g{color:#48bb78}.r{color:#fc8181}.w{color:#fff}.y{color:#f6c90e}

/* ── Grid 2 ── */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
@media(max-width:700px){.g2{grid-template-columns:1fr}}
.panel{background:#12151f;border:1px solid #1e2535;border-radius:12px;padding:14px}

/* monthly bars */
.mbr{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #0f1117;font-size:12px}
.mbr:last-child{border-bottom:none}
.mbl{width:48px;color:#718096;font-size:11px}
.mbt{flex:1;background:#1e2535;border-radius:2px;height:6px}
.mbf{height:6px;border-radius:2px}
.mbv{width:78px;text-align:right;font-weight:700;font-size:12px}

/* trade table */
.twrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:580px}
th{text-align:left;padding:7px 9px;color:#4a5568;font-weight:500;
  border-bottom:1px solid #1e2535;font-size:9px;text-transform:uppercase;letter-spacing:.6px}
td{padding:8px 9px;border-bottom:1px solid #0f1117;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none} tr:hover td{background:#1a1d2e}
.b{display:inline-block;padding:1px 6px;border-radius:5px;font-size:10px;font-weight:700}
.bg2{background:#1a3a2a;color:#48bb78}.br2{background:#3a1a1a;color:#fc8181}
.by2{background:#3a3010;color:#f6c90e}.bb2{background:#1a2a3a;color:#63b3ed}

.nodata{text-align:center;color:#2d3748;padding:24px;font-size:13px}
.rnote{font-size:10px;color:#2d3748;text-align:center;margin-top:14px;padding-bottom:8px}
</style>
</head>
<body>

<header>
  <div class="hdr-left">
    <span class="logo">⚡ Hybrid Bot</span>
    <div class="pill"><span class="dot dot-y" id="ws-dot"></span><span id="ws-txt">Connecting...</span></div>
    <div class="pill"><span class="dot dot-g" id="bot-dot"></span><span id="bot-txt">Bot Active</span></div>
  </div>
  <div class="clock-box">
    <div class="clock-t" id="clock">--:--:--</div>
    <div class="clock-d" id="cdate">Asia/Kolkata</div>
  </div>
</header>

<div class="container">

  <div class="slabel">Live Market — Tick by Tick</div>
  <div class="mstrip">

    <!-- BankNifty -->
    <div class="mcard bn">
      <div class="mc-top">
        <span class="mc-name">BANK NIFTY</span>
        <span class="mc-badge bp" id="bn-badge">—</span>
      </div>
      <div class="ltp-row">
        <span class="ltp" id="bn-ltp">—</span>
        <div class="chg-box">
          <span class="chg cfl" id="bn-chg">—</span>
          <span class="tick-time" id="bn-time">—</span>
        </div>
      </div>
      <div class="hl-row">
        <div class="hl-box"><div class="hl-lbl">Day High</div><div class="hl-v hv-h" id="bn-high">—</div></div>
        <div class="hl-box"><div class="hl-lbl">Day Low</div><div class="hl-v hv-l" id="bn-low">—</div></div>
        <div class="hl-box"><div class="hl-lbl">Prev Close</div><div class="hl-v hv-c" id="bn-close">—</div></div>
      </div>
      <div class="vol-row">
        <div class="vol-lbl">Volume</div>
        <div class="vol-bar"><div class="vol-fill" id="bn-vol-bar" style="width:0%"></div></div>
      </div>
      <div class="ema-row">
        <div class="ema-box">
          <div class="ema-lbl">EMA 50</div>
          <div class="ema-v" id="bn-e50">—</div>
          <div class="ema-sig" id="bn-e50s">—</div>
        </div>
        <div class="ema-box">
          <div class="ema-lbl">EMA 200</div>
          <div class="ema-v" id="bn-e200">—</div>
          <div class="ema-sig" id="bn-e200s">—</div>
        </div>
      </div>
    </div>

    <!-- Nifty -->
    <div class="mcard nf">
      <div class="mc-top">
        <span class="mc-name">NIFTY 50</span>
        <span class="mc-badge bp" id="nf-badge">—</span>
      </div>
      <div class="ltp-row">
        <span class="ltp" id="nf-ltp">—</span>
        <div class="chg-box">
          <span class="chg cfl" id="nf-chg">—</span>
          <span class="tick-time" id="nf-time">—</span>
        </div>
      </div>
      <div class="hl-row">
        <div class="hl-box"><div class="hl-lbl">Day High</div><div class="hl-v hv-h" id="nf-high">—</div></div>
        <div class="hl-box"><div class="hl-lbl">Day Low</div><div class="hl-v hv-l" id="nf-low">—</div></div>
        <div class="hl-box"><div class="hl-lbl">Prev Close</div><div class="hl-v hv-c" id="nf-close">—</div></div>
      </div>
      <div class="vol-row">
        <div class="vol-lbl">Volume</div>
        <div class="vol-bar"><div class="vol-fill" id="nf-vol-bar" style="width:0%;background:#f5576c"></div></div>
      </div>
      <div class="ema-row">
        <div class="ema-box">
          <div class="ema-lbl">EMA 50</div>
          <div class="ema-v" id="nf-e50">—</div>
          <div class="ema-sig" id="nf-e50s">—</div>
        </div>
        <div class="ema-box">
          <div class="ema-lbl">EMA 200</div>
          <div class="ema-v" id="nf-e200">—</div>
          <div class="ema-sig" id="nf-e200s">—</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Summary -->
  <div class="slabel">Performance</div>
  <div class="sgrid">
    <div class="sc"><div class="sl">Today P&amp;L</div><div class="sv" id="s1">—</div><div class="ss" id="s1s">—</div></div>
    <div class="sc"><div class="sl">Total Trades</div><div class="sv w" id="s2">—</div><div class="ss" id="s2s">—</div></div>
    <div class="sc"><div class="sl">Win Rate</div><div class="sv y" id="s3">—</div><div class="ss" id="s3s">—</div></div>
    <div class="sc"><div class="sl">Net P&amp;L</div><div class="sv" id="s4">—</div><div class="ss">Overall</div></div>
    <div class="sc"><div class="sl">Best Day</div><div class="sv g" id="s5">—</div><div class="ss" id="s5s">—</div></div>
    <div class="sc"><div class="sl">Max Drawdown</div><div class="sv r" id="s6">—</div><div class="ss">From peak</div></div>
  </div>

  <div class="g2">
    <div class="panel"><div class="slabel">Monthly P&amp;L</div><div id="mchart"><div class="nodata">No data</div></div></div>
    <div class="panel"><div class="slabel">Breakdown</div><div id="bdown"><div class="nodata">No data</div></div></div>
  </div>

  <div class="panel" style="margin-bottom:18px">
    <div class="slabel">Trade History</div>
    <div class="twrap" id="ttable"><div class="nodata">No trades yet</div></div>
  </div>
  <div class="rnote">Live ticks via KiteTicker WebSocket · EMA updates every 5 min</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
// ── Clock ──────────────────────────────────
function tick(){
  const ist=new Date(new Date().toLocaleString("en-US",{timeZone:"Asia/Kolkata"}));
  document.getElementById('clock').textContent=
    ist.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  document.getElementById('cdate').textContent=
    ist.toLocaleDateString('en-IN',{weekday:'short',day:'2-digit',month:'short',year:'numeric'});
  const h=ist.getHours(),m=ist.getMinutes(),d=ist.getDay();
  const mins=h*60+m,open=d>0&&d<6&&mins>=555&&mins<930;
  ['bn','nf'].forEach(s=>{
    const b=document.getElementById(s+'-badge');
    if(!b)return;
    if(open){b.textContent='LIVE';b.className='mc-badge bo';}
    else{b.textContent='CLOSED';b.className='mc-badge bc';}
  });
}
setInterval(tick,1000);tick();

// ── Helpers ───────────────────────────────
function fn(n,d=2){return n==null||isNaN(n)?'—':n.toLocaleString('en-IN',{maximumFractionDigits:d,minimumFractionDigits:d});}
function fp(n){if(n==null||isNaN(n))return'—';const s=n<0?'-':'+';return s+'₹'+Math.abs(n).toLocaleString('en-IN',{maximumFractionDigits:0});}

// prev LTP for flash detection
const prevLtp={bn:0,nf:0};
const maxVol={bn:1,nf:1};

function applyTick(d){
  const s=d.sym;
  const lEl=document.getElementById(s+'-ltp');
  const prev=prevLtp[s];
  const ltp=d.ltp;

  // Flash
  if(prev&&ltp!==prev){
    lEl.classList.remove('flash-up','flash-dn');
    void lEl.offsetWidth;
    lEl.classList.add(ltp>prev?'flash-up':'flash-dn');
    setTimeout(()=>lEl.classList.remove('flash-up','flash-dn'),400);
  }
  prevLtp[s]=ltp;

  lEl.textContent=fn(ltp);
  document.getElementById(s+'-high').textContent=fn(d.high);
  document.getElementById(s+'-low').textContent=fn(d.low);
  document.getElementById(s+'-close').textContent=fn(d.close);
  document.getElementById(s+'-time').textContent=d.time||'';

  const chg=d.change||0,chgp=d.change_pct||0;
  const cEl=document.getElementById(s+'-chg');
  cEl.textContent=(chg>=0?'+':'')+fn(chg)+' ('+(chgp>=0?'+':'')+fn(chgp)+'%)';
  cEl.className='chg '+(chg>0?'cup':chg<0?'cdn':'cfl');

  // Volume bar
  if(d.volume>maxVol[s])maxVol[s]=d.volume;
  const vw=Math.min((d.volume/maxVol[s])*100,100);
  const vb=document.getElementById(s+'-vol-bar');
  if(vb)vb.style.width=vw.toFixed(1)+'%';

  // EMA
  applyEma(s,d.ema50,d.ema200,ltp);
}

function applyEma(s,e50,e200,ltp){
  const e50El=document.getElementById(s+'-e50');
  const e200El=document.getElementById(s+'-e200');
  const e50Sig=document.getElementById(s+'-e50s');
  const e200Sig=document.getElementById(s+'-e200s');
  if(!e50El||!e200El)return;
  if(e50){
    e50El.textContent=fn(e50);
    e50El.style.color=ltp>e50?'#48bb78':'#fc8181';
    e50Sig.textContent=ltp>e50?'▲ Above EMA50':'▼ Below EMA50';
    e50Sig.className='ema-sig '+(ltp>e50?'sg':'sr');
  }
  if(e200){
    e200El.textContent=fn(e200);
    e200El.style.color=ltp>e200?'#48bb78':'#fc8181';
    e200Sig.textContent=ltp>e200?'▲ Above EMA200':'▼ Below EMA200';
    e200Sig.className='ema-sig '+(ltp>e200?'sg':'sr');
  }
}

function renderTrades(trades){
  if(!trades||!trades.length)return;
  const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Kolkata'});
  const td=trades.filter(t=>t.date===today);
  const wins=trades.filter(t=>t.pnl>0);
  const net=trades.reduce((s,t)=>s+t.pnl,0);
  const tpnl=td.reduce((s,t)=>s+t.pnl,0);
  const wr=trades.length?(wins.length/trades.length*100):0;
  let pk=0,dd=0,rn=0;
  trades.forEach(t=>{rn+=t.pnl;if(rn>pk)pk=rn;dd=Math.max(dd,pk-rn);});
  const byDay={};
  trades.forEach(t=>{byDay[t.date]=(byDay[t.date]||0)+t.pnl;});
  const best=Object.entries(byDay).sort((a,b)=>b[1]-a[1])[0];

  document.getElementById('s1').textContent=fp(tpnl);
  document.getElementById('s1').className='sv '+(tpnl>=0?'g':'r');
  document.getElementById('s1s').textContent=td.length+' trades today';
  document.getElementById('s2').textContent=trades.length;
  document.getElementById('s2s').textContent=wins.length+'W/'+(trades.length-wins.length)+'L';
  document.getElementById('s3').textContent=wr.toFixed(1)+'%';
  document.getElementById('s3s').textContent=wins.length+' winners';
  document.getElementById('s4').textContent=fp(net);
  document.getElementById('s4').className='sv '+(net>=0?'g':'r');
  document.getElementById('s5').textContent=best?fp(best[1]):'—';
  document.getElementById('s5s').textContent=best?best[0]:'';
  document.getElementById('s6').textContent='-₹'+dd.toLocaleString('en-IN',{maximumFractionDigits:0});

  // Monthly
  const mo={};trades.forEach(t=>{const m=t.date.substring(0,7);mo[m]=(mo[m]||0)+t.pnl;});
  const mx=Math.max(...Object.values(mo).map(Math.abs),1);
  document.getElementById('mchart').innerHTML=Object.entries(mo).sort().map(([m,v])=>{
    const lb=new Date(m+'-01').toLocaleString('en-IN',{month:'short',year:'2-digit'});
    const w=(Math.abs(v)/mx*100).toFixed(0);
    const c=v>=0?'#48bb78':'#fc8181';
    return`<div class="mbr"><span class="mbl">${lb}</span><div class="mbt"><div class="mbf" style="width:${w}%;background:${c}"></div></div><span class="mbv" style="color:${c}">${fp(v)}</span></div>`;
  }).join('')||'<div class="nodata">No data</div>';

  // Breakdown
  const ct=trades.filter(t=>t.direction==='CALL'),pt=trades.filter(t=>t.direction==='PUT');
  const bt=trades.filter(t=>t.symbol==='BANKNIFTY'),nt=trades.filter(t=>t.symbol==='NIFTY');
  const st=trades.filter(t=>t.status&&t.status.includes('SL'));
  const tt=trades.filter(t=>t.status&&t.status.includes('TARGET'));
  const et=trades.filter(t=>t.status&&t.status.includes('EOD'));
  const pc=(a,k='pnl')=>a.reduce((s,t)=>s+t[k],0);
  document.getElementById('bdown').innerHTML=`<table>
    <tr><th>Category</th><th>Trades</th><th>Wins</th><th>P&L</th></tr>
    ${[['📈 CALL',ct],[' 📉 PUT',pt],['🏦 BankNifty',bt],['📊 Nifty',nt]].map(([n,a])=>`<tr><td>${n}</td><td>${a.length}</td><td>${a.filter(t=>t.pnl>0).length}</td><td style="color:${pc(a)>=0?'#48bb78':'#fc8181'}">${fp(pc(a))}</td></tr>`).join('')}
    <tr><td>🎯 Target</td><td>${tt.length}</td><td>—</td><td style="color:#48bb78">${fp(pc(tt))}</td></tr>
    <tr><td>🛑 SL</td><td>${st.length}</td><td>—</td><td style="color:#fc8181">${fp(pc(st))}</td></tr>
    <tr><td>⏰ EOD</td><td>${et.length}</td><td>—</td><td>${fp(pc(et))}</td></tr>
    </table>`;

  // Trade table
  document.getElementById('ttable').innerHTML=trades.length?`<table>
    <tr><th>Date</th><th>Symbol</th><th>Option</th><th>Strike</th><th>Dir</th><th>Lots</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Exit Type</th></tr>
    ${[...trades].reverse().slice(0,60).map(t=>{
      const dc=t.direction==='CALL'?'bg2':'br2';
      const sc=t.status&&t.status.includes('TARGET')?'bg2':t.status&&t.status.includes('SL')?'br2':t.status&&t.status.includes('EOD')?'by2':'bb2';
      return`<tr><td style="color:#718096">${t.date}</td><td><b>${t.symbol}</b></td><td style="color:#a0aec0;font-size:11px">${t.opt_sym||'—'}</td><td>${t.strike||'—'}</td><td><span class="b ${dc}">${t.direction}</span></td><td>${t.lots}</td><td>${(t.entry||0).toFixed(1)}</td><td>${(t.exit||0).toFixed(1)}</td><td style="font-weight:700;color:${t.pnl>=0?'#48bb78':'#fc8181'}">${fp(t.pnl)}</td><td><span class="b ${sc}">${(t.status||'').split(' ')[0]}</span></td></tr>`;
    }).join('')}
    </table>`:'<div class="nodata">No trades yet</div>';
}

// ── Socket.IO ─────────────────────────────
const socket=io({transports:['websocket','polling']});

socket.on('connect',()=>{
  document.getElementById('ws-dot').className='dot dot-g';
  document.getElementById('ws-txt').textContent='WebSocket Live';
});
socket.on('disconnect',()=>{
  document.getElementById('ws-dot').className='dot dot-r';
  document.getElementById('ws-txt').textContent='Disconnected';
});
socket.on('ws_status',d=>{
  const ok=d.connected;
  document.getElementById('ws-dot').className='dot '+(ok?'dot-g':'dot-r');
  document.getElementById('ws-txt').textContent=ok?'Kite WS Live':'Kite WS Down';
});

// Tick-by-tick update
socket.on('tick', applyTick);

// EMA pushed every 5 min from bot
socket.on('ema_update',d=>applyEma(d.sym,d.ema50,d.ema200,prevLtp[d.sym]||0));

// Full snapshot on connect
socket.on('snapshot',d=>{
  if(d.market){
    ['bn','nf'].forEach(s=>{
      const m=d.market[s];
      if(m&&m.ltp) applyTick({sym:s,...m,change:m.change||0,change_pct:m.change_pct||0});
    });
  }
  if(d.trades) renderTrades(d.trades);
});

// Poll trades every 30s (new trades may come in)
setInterval(async()=>{
  try{const r=await fetch('/api/trades');const t=await r.json();renderTrades(t);}catch(e){}
},30000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  START
# ─────────────────────────────────────────────
def run_dashboard():
    socketio.run(app, host="0.0.0.0", port=PORT,
                 debug=False, use_reloader=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_dashboard()
