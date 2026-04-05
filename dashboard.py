"""
╔══════════════════════════════════════════════════════════════════╗
║   TRADING DASHBOARD — Tick-by-Tick Real Time                     ║
║   KiteTicker WebSocket → Flask-SocketIO → Browser               ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import json
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import eventlet
eventlet.monkey_patch()

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
# eventlet async_mode — works correctly with Railway reverse proxy
socketio  = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

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
            high  = ohlc.get("high", ltp)
            low   = ohlc.get("low",  ltp)
            close = ohlc.get("close", ltp)  # prev close
            opn   = ohlc.get("open",  ltp)
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

    t = threading.Thread(target=_ticker.connect, kwargs={"threaded": True}, daemon=True)
    t.start()
    log.info("📡 KiteTicker started in background thread.")


def update_ema(sym: str, ema50: float, ema200: float):
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

@app.route("/health")
def health():
    """Railway health check endpoint"""
    return {"status": "ok", "port": PORT}, 200

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
    trades = []
    if os.path.exists(TRADE_FILE):
        try:
            with open(TRADE_FILE) as f:
                trades = json.load(f)
        except Exception:
            pass
    socketio.emit("snapshot", {"market": _tick_data, "trades": trades})


# ─────────────────────────────────────────────
#  HTML (kept exactly as original)
# ─────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e2e8f0;font-family:'Segoe UI',sans-serif;font-size:14px}
.wrap{max-width:1200px;margin:0 auto;padding:16px}
.hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.htitle{font-size:20px;font-weight:700;color:#fff}
.hright{text-align:right}
#clock{font-size:22px;font-weight:700;color:#63b3ed;letter-spacing:1px}
#cdate{font-size:12px;color:#718096}
.ws-row{display:flex;align-items:center;gap:6px;margin-top:4px;justify-content:flex-end}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot-g{background:#48bb78}.dot-r{background:#fc8181}.dot-y{background:#f6c90e}
#ws-txt{font-size:12px;color:#a0aec0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px}
.card-title{font-size:11px;color:#718096;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
.mc-badge{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:700}
.bo{background:#1a3a2a;color:#48bb78}.bc{background:#3a1a1a;color:#fc8181}
.ltp{font-size:32px;font-weight:800;color:#fff;letter-spacing:-1px}
.chg{font-size:13px;margin-top:2px}
.cup{color:#48bb78}.cdn{color:#fc8181}.cfl{color:#a0aec0}
.ohlc-row{display:flex;gap:12px;margin-top:10px;flex-wrap:wrap}
.ohlc-item{font-size:12px;color:#718096}
.ohlc-item span{color:#cbd5e0;font-weight:600;margin-left:3px}
.vol-wrap{margin-top:8px}
.vol-label{font-size:11px;color:#718096;margin-bottom:3px}
.vol-bg{background:#21262d;border-radius:3px;height:5px;width:100%}
.vol-bar{height:5px;border-radius:3px;background:#4a90d9;transition:width .5s}
.ema-row{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.ema-box{background:#0d1117;border-radius:6px;padding:6px 10px;flex:1;min-width:100px}
.ema-label{font-size:10px;color:#718096}
.ema-val{font-size:14px;font-weight:700;color:#fff}
.ema-sig{font-size:10px;margin-top:1px}
.sg{color:#48bb78}.sr{color:#fc8181}
.flash-up{animation:fu .4s}
.flash-dn{animation:fd .4s}
@keyframes fu{0%{background:#1a3a2a}100%{background:transparent}}
@keyframes fd{0%{background:#3a1a1a}100%{background:transparent}}
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
.scard{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px;text-align:center}
.sl{font-size:10px;color:#718096;margin-bottom:4px}
.sv{font-size:20px;font-weight:800;color:#fff}
.sv.g{color:#48bb78}.sv.r{color:#fc8181}
.ss{font-size:11px;color:#a0aec0;margin-top:2px}
.section{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px;margin-bottom:14px}
.sec-title{font-size:13px;font-weight:700;color:#a0aec0;margin-bottom:10px}
.mbr{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.mbl{font-size:11px;color:#718096;width:50px}
.mbt{flex:1;background:#21262d;border-radius:3px;height:8px;overflow:hidden}
.mbf{height:8px;border-radius:3px}
.mbv{font-size:12px;font-weight:700;width:70px;text-align:right}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:#718096;text-align:left;padding:6px 8px;border-bottom:1px solid #21262d}
td{padding:6px 8px;border-bottom:1px solid #161b22;color:#cbd5e0}
.b{padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700}
.bg2{background:#1a3a2a;color:#48bb78}.br2{background:#3a1a1a;color:#fc8181}
.by2{background:#3a2e00;color:#f6c90e}.bb2{background:#1a2a3a;color:#63b3ed}
.nodata{color:#4a5568;text-align:center;padding:20px;font-size:13px}
.rnote{text-align:center;color:#4a5568;font-size:11px;margin-top:10px}
@media(max-width:600px){.grid2{grid-template-columns:1fr}.stats-grid{grid-template-columns:repeat(2,1fr)}.ltp{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div class="htitle">⚡ Hybrid Trading Bot</div>
    <div class="hright">
      <div id="clock">--:--:--</div>
      <div id="cdate"></div>
      <div class="ws-row"><span class="dot dot-y" id="ws-dot"></span><span id="ws-txt">Connecting...</span></div>
    </div>
  </div>

  <div class="grid2">
    <div class="card">
      <div class="card-title">🏦 BankNifty <span class="mc-badge bc" id="bn-badge">CLOSED</span></div>
      <div class="ltp" id="bn-ltp">—</div>
      <div class="chg cfl" id="bn-chg">—</div>
      <div class="ohlc-row">
        <div class="ohlc-item">O<span id="bn-open">—</span></div>
        <div class="ohlc-item">H<span id="bn-high">—</span></div>
        <div class="ohlc-item">L<span id="bn-low">—</span></div>
        <div class="ohlc-item">PC<span id="bn-close">—</span></div>
        <div class="ohlc-item" style="margin-left:auto;font-size:10px;color:#4a5568" id="bn-time"></div>
      </div>
      <div class="vol-wrap"><div class="vol-label">Volume</div><div class="vol-bg"><div class="vol-bar" id="bn-vol-bar" style="width:0%"></div></div></div>
      <div class="ema-row">
        <div class="ema-box"><div class="ema-label">EMA 50</div><div class="ema-val" id="bn-e50">—</div><div class="ema-sig" id="bn-e50s">—</div></div>
        <div class="ema-box"><div class="ema-label">EMA 200</div><div class="ema-val" id="bn-e200">—</div><div class="ema-sig" id="bn-e200s">—</div></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">📊 Nifty 50 <span class="mc-badge bc" id="nf-badge">CLOSED</span></div>
      <div class="ltp" id="nf-ltp">—</div>
      <div class="chg cfl" id="nf-chg">—</div>
      <div class="ohlc-row">
        <div class="ohlc-item">O<span id="nf-open">—</span></div>
        <div class="ohlc-item">H<span id="nf-high">—</span></div>
        <div class="ohlc-item">L<span id="nf-low">—</span></div>
        <div class="ohlc-item">PC<span id="nf-close">—</span></div>
        <div class="ohlc-item" style="margin-left:auto;font-size:10px;color:#4a5568" id="nf-time"></div>
      </div>
      <div class="vol-wrap"><div class="vol-label">Volume</div><div class="vol-bg"><div class="vol-bar" id="nf-vol-bar" style="width:0%"></div></div></div>
      <div class="ema-row">
        <div class="ema-box"><div class="ema-label">EMA 50</div><div class="ema-val" id="nf-e50">—</div><div class="ema-sig" id="nf-e50s">—</div></div>
        <div class="ema-box"><div class="ema-label">EMA 200</div><div class="ema-val" id="nf-e200">—</div><div class="ema-sig" id="nf-e200s">—</div></div>
      </div>
    </div>
  </div>

  <div class="stats-grid">
    <div class="scard"><div class="sl">Today P&L</div><div class="sv" id="s1">—</div><div class="ss" id="s1s">—</div></div>
    <div class="scard"><div class="sl">Total Trades</div><div class="sv" id="s2">—</div><div class="ss" id="s2s">—</div></div>
    <div class="scard"><div class="sl">Win Rate</div><div class="sv" id="s3">—</div><div class="ss" id="s3s">—</div></div>
    <div class="scard"><div class="sl">Net P&L</div><div class="sv" id="s4">—</div><div class="ss"></div></div>
    <div class="scard"><div class="sl">Best Day</div><div class="sv" id="s5">—</div><div class="ss" id="s5s">—</div></div>
    <div class="scard"><div class="sl">Max Drawdown</div><div class="sv" id="s6">—</div><div class="ss"></div></div>
  </div>

  <div class="section"><div class="sec-title">📅 Monthly P&L</div><div id="mchart"><div class="nodata">No data</div></div></div>
  <div class="section"><div class="sec-title">📊 Breakdown</div><div id="bdown"><div class="nodata">No trades yet</div></div></div>
  <div class="section"><div class="sec-title">📋 Trade History</div><div id="ttable"><div class="nodata">No trades yet</div></div></div>
  <div class="rnote">Live ticks via KiteTicker WebSocket · EMA updates every 5 min</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
function tick(){
  const ist=new Date(new Date().toLocaleString("en-US",{timeZone:"Asia/Kolkata"}));
  document.getElementById('clock').textContent=ist.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  document.getElementById('cdate').textContent=ist.toLocaleDateString('en-IN',{weekday:'short',day:'2-digit',month:'short',year:'numeric'});
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

function fn(n,d=2){return n==null||isNaN(n)?'—':n.toLocaleString('en-IN',{maximumFractionDigits:d,minimumFractionDigits:d});}
function fp(n){if(n==null||isNaN(n))return'—';const s=n<0?'-':'+';return s+'₹'+Math.abs(n).toLocaleString('en-IN',{maximumFractionDigits:0});}

const prevLtp={bn:0,nf:0};
const maxVol={bn:1,nf:1};

function applyTick(d){
  const s=d.sym;
  const lEl=document.getElementById(s+'-ltp');
  const prev=prevLtp[s];
  const ltp=d.ltp;
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
  document.getElementById(s+'-open').textContent=fn(d.open);
  document.getElementById(s+'-time').textContent=d.time||'';
  const chg=d.change||0,chgp=d.change_pct||0;
  const cEl=document.getElementById(s+'-chg');
  cEl.textContent=(chg>=0?'+':'')+fn(chg)+' ('+(chgp>=0?'+':'')+fn(chgp)+'%)';
  cEl.className='chg '+(chg>0?'cup':chg<0?'cdn':'cfl');
  if(d.volume>maxVol[s])maxVol[s]=d.volume;
  const vw=Math.min((d.volume/maxVol[s])*100,100);
  const vb=document.getElementById(s+'-vol-bar');
  if(vb)vb.style.width=vw.toFixed(1)+'%';
  applyEma(s,d.ema50,d.ema200,ltp);
}

function applyEma(s,e50,e200,ltp){
  const e50El=document.getElementById(s+'-e50');
  const e200El=document.getElementById(s+'-e200');
  const e50Sig=document.getElementById(s+'-e50s');
  const e200Sig=document.getElementById(s+'-e200s');
  if(!e50El||!e200El)return;
  if(e50){e50El.textContent=fn(e50);e50El.style.color=ltp>e50?'#48bb78':'#fc8181';e50Sig.textContent=ltp>e50?'▲ Above EMA50':'▼ Below EMA50';e50Sig.className='ema-sig '+(ltp>e50?'sg':'sr');}
  if(e200){e200El.textContent=fn(e200);e200El.style.color=ltp>e200?'#48bb78':'#fc8181';e200Sig.textContent=ltp>e200?'▲ Above EMA200':'▼ Below EMA200';e200Sig.className='ema-sig '+(ltp>e200?'sg':'sr');}
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
  const mo={};trades.forEach(t=>{const m=t.date.substring(0,7);mo[m]=(mo[m]||0)+t.pnl;});
  const mx=Math.max(...Object.values(mo).map(Math.abs),1);
  document.getElementById('mchart').innerHTML=Object.entries(mo).sort().map(([m,v])=>{
    const lb=new Date(m+'-01').toLocaleString('en-IN',{month:'short',year:'2-digit'});
    const w=(Math.abs(v)/mx*100).toFixed(0);
    const c=v>=0?'#48bb78':'#fc8181';
    return`<div class="mbr"><span class="mbl">${lb}</span><div class="mbt"><div class="mbf" style="width:${w}%;background:${c}"></div></div><span class="mbv" style="color:${c}">${fp(v)}</span></div>`;
  }).join('')||'<div class="nodata">No data</div>';
  const ct=trades.filter(t=>t.direction==='CALL'),pt=trades.filter(t=>t.direction==='PUT');
  const bt=trades.filter(t=>t.symbol==='BANKNIFTY'),nt=trades.filter(t=>t.symbol==='NIFTY');
  const st=trades.filter(t=>t.status&&t.status.includes('SL'));
  const tt=trades.filter(t=>t.status&&t.status.includes('TARGET'));
  const et=trades.filter(t=>t.status&&t.status.includes('EOD'));
  const pc=(a)=>a.reduce((s,t)=>s+t.pnl,0);
  document.getElementById('bdown').innerHTML=`<table>
    <tr><th>Category</th><th>Trades</th><th>Wins</th><th>P&L</th></tr>
    ${[['📈 CALL',ct],['📉 PUT',pt],['🏦 BankNifty',bt],['📊 Nifty',nt]].map(([n,a])=>`<tr><td>${n}</td><td>${a.length}</td><td>${a.filter(t=>t.pnl>0).length}</td><td style="color:${pc(a)>=0?'#48bb78':'#fc8181'}">${fp(pc(a))}</td></tr>`).join('')}
    <tr><td>🎯 Target</td><td>${tt.length}</td><td>—</td><td style="color:#48bb78">${fp(pc(tt))}</td></tr>
    <tr><td>🛑 SL</td><td>${st.length}</td><td>—</td><td style="color:#fc8181">${fp(pc(st))}</td></tr>
    <tr><td>⏰ EOD</td><td>${et.length}</td><td>—</td><td>${fp(pc(et))}</td></tr>
    </table>`;
  document.getElementById('ttable').innerHTML=trades.length?`<table>
    <tr><th>Date</th><th>Symbol</th><th>Option</th><th>Strike</th><th>Dir</th><th>Lots</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Exit Type</th></tr>
    ${[...trades].reverse().slice(0,60).map(t=>{
      const dc=t.direction==='CALL'?'bg2':'br2';
      const sc=t.status&&t.status.includes('TARGET')?'bg2':t.status&&t.status.includes('SL')?'br2':t.status&&t.status.includes('EOD')?'by2':'bb2';
      return`<tr><td style="color:#718096">${t.date}</td><td><b>${t.symbol}</b></td><td style="color:#a0aec0;font-size:11px">${t.opt_sym||'—'}</td><td>${t.strike||'—'}</td><td><span class="b ${dc}">${t.direction}</span></td><td>${t.lots}</td><td>${(t.entry||0).toFixed(1)}</td><td>${(t.exit||0).toFixed(1)}</td><td style="font-weight:700;color:${t.pnl>=0?'#48bb78':'#fc8181'}">${fp(t.pnl)}</td><td><span class="b ${sc}">${(t.status||'').split(' ')[0]}</span></td></tr>`;
    }).join('')}
    </table>`:'<div class="nodata">No trades yet</div>';
}

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
socket.on('tick',applyTick);
socket.on('ema_update',d=>applyEma(d.sym,d.ema50,d.ema200,prevLtp[d.sym]||0));
socket.on('snapshot',d=>{
  if(d.market){['bn','nf'].forEach(s=>{const m=d.market[s];if(m&&m.ltp)applyTick({sym:s,...m});});}
  if(d.trades)renderTrades(d.trades);
});
setInterval(async()=>{try{const r=await fetch('/api/trades');const t=await r.json();renderTrades(t);}catch(e){}},30000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  START
# ─────────────────────────────────────────────
def run_dashboard():
    log.info(f"🌐 Dashboard starting on port {PORT}...")
    socketio.run(app, host="0.0.0.0", port=PORT,
                 debug=False, use_reloader=False)


if __name__ == "__main__":
    run_dashboard()
