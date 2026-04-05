"""
╔══════════════════════════════════════════════════════════════════╗
║   TRADING DASHBOARD — Flask Web Server                           ║
║   Live P&L + Trade History + Bot Status                          ║
║   Run alongside bot: python dashboard.py                         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

IST        = ZoneInfo("Asia/Kolkata")
TRADE_FILE = "/tmp/trades_log.json"
PORT       = int(os.environ.get("PORT", 8080))

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>Trading Dashboard</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  
  header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 16px 24px;
           display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 18px; font-weight: 600; color: #fff; letter-spacing: 0.5px; }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: #48bb78;
               display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .timestamp { font-size: 12px; color: #718096; }

  .container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 28px; }
  .card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 12px; padding: 18px; }
  .card .label { font-size: 11px; color: #718096; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; }
  .card .value { font-size: 26px; font-weight: 700; }
  .card .sub   { font-size: 12px; color: #718096; margin-top: 4px; }
  .green { color: #48bb78; } .red { color: #fc8181; } .white { color: #fff; } .yellow { color: #f6c90e; }

  .section-title { font-size: 13px; color: #a0aec0; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.8px; }
  
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 28px; }
  @media(max-width:700px){ .grid2 { grid-template-columns:1fr; } }

  .panel { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 12px; padding: 18px; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 10px; color: #718096; font-weight: 500;
       border-bottom: 1px solid #2d3748; font-size: 11px; text-transform: uppercase; }
  td { padding: 10px 10px; border-bottom: 1px solid #1e2433; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2433; }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; }
  .badge-green { background: #1a3a2a; color: #48bb78; }
  .badge-red   { background: #3a1a1a; color: #fc8181; }
  .badge-yellow{ background: #3a3010; color: #f6c90e; }
  .badge-blue  { background: #1a2a3a; color: #63b3ed; }

  .bar-wrap { background: #2d3748; border-radius: 4px; height: 6px; margin-top: 8px; }
  .bar-fill { height: 6px; border-radius: 4px; }

  .monthly-row { display: flex; align-items: center; gap: 10px; padding: 7px 0;
                 border-bottom: 1px solid #1e2433; font-size: 13px; }
  .monthly-row:last-child { border-bottom: none; }
  .month-label { width: 60px; color: #a0aec0; }
  .month-bar-wrap { flex: 1; background: #2d3748; border-radius: 3px; height: 8px; }
  .month-bar { height: 8px; border-radius: 3px; }
  .month-val { width: 90px; text-align: right; font-weight: 600; font-size: 13px; }

  .no-trades { text-align: center; color: #4a5568; padding: 32px; font-size: 14px; }
  .refresh-note { font-size: 11px; color: #4a5568; text-align: center; margin-top: 20px; }
</style>
</head>
<body>
<header>
  <div>
    <span class="live-dot"></span>
    <span style="font-size:15px;font-weight:600;color:#fff;">Hybrid Trading Bot</span>
    <span style="margin-left:12px;font-size:12px;color:#718096;">BankNifty &amp; Nifty | ORB+EMA+VWAP</span>
  </div>
  <div class="timestamp" id="clock"></div>
</header>

<div class="container">
  <!-- Summary Cards -->
  <div class="cards" id="cards">
    <div class="card"><div class="label">Today P&amp;L</div><div class="value" id="today-pnl">—</div><div class="sub" id="today-sub">—</div></div>
    <div class="card"><div class="label">Total Trades</div><div class="value white" id="total-trades">—</div><div class="sub" id="total-sub">—</div></div>
    <div class="card"><div class="label">Win Rate</div><div class="value yellow" id="win-rate">—</div><div class="sub" id="wr-sub">—</div></div>
    <div class="card"><div class="label">Net P&amp;L (All)</div><div class="value" id="net-pnl">—</div><div class="sub">Overall</div></div>
    <div class="card"><div class="label">Best Day</div><div class="value green" id="best-day">—</div><div class="sub" id="best-day-sub">—</div></div>
    <div class="card"><div class="label">Max Drawdown</div><div class="value red" id="max-dd">—</div><div class="sub">From peak</div></div>
  </div>

  <div class="grid2">
    <!-- Monthly P&L -->
    <div class="panel">
      <div class="section-title">Monthly P&amp;L</div>
      <div id="monthly-chart"><div class="no-trades">No data yet</div></div>
    </div>

    <!-- CALL vs PUT Stats -->
    <div class="panel">
      <div class="section-title">Strategy Breakdown</div>
      <div id="breakdown"><div class="no-trades">No data yet</div></div>
    </div>
  </div>

  <!-- Trade History -->
  <div class="panel">
    <div class="section-title">Trade History</div>
    <div id="trade-table"><div class="no-trades">No trades yet today</div></div>
  </div>

  <div class="refresh-note">Auto-refreshes every 30 seconds</div>
</div>

<script>
function updateClock() {
  const now = new Date();
  const ist = new Date(now.toLocaleString("en-US", {timeZone: "Asia/Kolkata"}));
  document.getElementById('clock').textContent =
    ist.toLocaleString('en-IN', {day:'2-digit',month:'short',year:'numeric',
      hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}) + ' IST';
}
setInterval(updateClock, 1000); updateClock();

function fmt(n) {
  const s = n < 0 ? '-' : '+';
  return s + '₹' + Math.abs(n).toLocaleString('en-IN', {maximumFractionDigits:0});
}

async function load() {
  try {
    const r = await fetch('/api/trades');
    const d = await r.json();
    render(d);
  } catch(e) { console.error(e); }
}

function render(trades) {
  if (!trades || trades.length === 0) return;

  const today = new Date().toLocaleDateString('en-CA', {timeZone:'Asia/Kolkata'});
  const todayT  = trades.filter(t => t.date === today);
  const allWins = trades.filter(t => t.pnl > 0);
  const netPnl  = trades.reduce((s,t) => s+t.pnl, 0);
  const todayPnl= todayT.reduce((s,t) => s+t.pnl, 0);
  const wr      = trades.length ? (allWins.length/trades.length*100) : 0;

  // Running max drawdown
  let peak=0, dd=0, running=0;
  trades.forEach(t => { running+=t.pnl; if(running>peak)peak=running; dd=Math.max(dd,peak-running); });

  // Best day
  const byDay = {};
  trades.forEach(t => { byDay[t.date]=(byDay[t.date]||0)+t.pnl; });
  const bestDate = Object.entries(byDay).sort((a,b)=>b[1]-a[1])[0];

  // Cards
  document.getElementById('today-pnl').textContent = fmt(todayPnl);
  document.getElementById('today-pnl').className   = 'value ' + (todayPnl>=0?'green':'red');
  document.getElementById('today-sub').textContent  = `${todayT.length} trades today`;
  document.getElementById('total-trades').textContent = trades.length;
  document.getElementById('total-sub').textContent    = `${allWins.length}W / ${trades.length-allWins.length}L`;
  document.getElementById('win-rate').textContent     = wr.toFixed(1)+'%';
  document.getElementById('wr-sub').textContent       = `${allWins.length} winners`;
  document.getElementById('net-pnl').textContent      = fmt(netPnl);
  document.getElementById('net-pnl').className        = 'value '+(netPnl>=0?'green':'red');
  document.getElementById('best-day').textContent     = bestDate ? fmt(bestDate[1]) : '—';
  document.getElementById('best-day-sub').textContent = bestDate ? bestDate[0] : '';
  document.getElementById('max-dd').textContent       = '-₹'+dd.toLocaleString('en-IN',{maximumFractionDigits:0});

  // Monthly chart
  const months = {};
  trades.forEach(t => {
    const m = t.date.substring(0,7);
    months[m] = (months[m]||0) + t.pnl;
  });
  const maxAbs = Math.max(...Object.values(months).map(Math.abs), 1);
  let mhtml = '';
  Object.entries(months).sort().forEach(([m, v]) => {
    const label = new Date(m+'-01').toLocaleString('en-IN',{month:'short',year:'2-digit'});
    const w = Math.abs(v)/maxAbs*100;
    const col = v>=0 ? '#48bb78' : '#fc8181';
    mhtml += `<div class="monthly-row">
      <span class="month-label">${label}</span>
      <div class="month-bar-wrap"><div class="month-bar" style="width:${w.toFixed(0)}%;background:${col}"></div></div>
      <span class="month-val" style="color:${col}">${fmt(v)}</span>
    </div>`;
  });
  document.getElementById('monthly-chart').innerHTML = mhtml || '<div class="no-trades">No data</div>';

  // Breakdown
  const call_t = trades.filter(t=>t.direction==='CALL');
  const put_t  = trades.filter(t=>t.direction==='PUT');
  const call_pnl = call_t.reduce((s,t)=>s+t.pnl,0);
  const put_pnl  = put_t.reduce((s,t)=>s+t.pnl,0);
  const bn_t   = trades.filter(t=>t.symbol==='BANKNIFTY');
  const nf_t   = trades.filter(t=>t.symbol==='NIFTY');
  const sl_t   = trades.filter(t=>t.status&&t.status.includes('SL'));
  const tgt_t  = trades.filter(t=>t.status&&t.status.includes('TARGET'));
  const eod_t  = trades.filter(t=>t.status&&t.status.includes('EOD'));
  document.getElementById('breakdown').innerHTML = `
    <table>
      <tr><th>Category</th><th>Trades</th><th>Wins</th><th>P&amp;L</th></tr>
      <tr><td>📈 CALL (CE)</td><td>${call_t.length}</td><td>${call_t.filter(t=>t.pnl>0).length}</td>
          <td style="color:${call_pnl>=0?'#48bb78':'#fc8181'}">${fmt(call_pnl)}</td></tr>
      <tr><td>📉 PUT (PE)</td><td>${put_t.length}</td><td>${put_t.filter(t=>t.pnl>0).length}</td>
          <td style="color:${put_pnl>=0?'#48bb78':'#fc8181'}">${fmt(put_pnl)}</td></tr>
      <tr><td>🏦 BankNifty</td><td>${bn_t.length}</td><td>${bn_t.filter(t=>t.pnl>0).length}</td>
          <td style="color:${bn_t.reduce((s,t)=>s+t.pnl,0)>=0?'#48bb78':'#fc8181'}">${fmt(bn_t.reduce((s,t)=>s+t.pnl,0))}</td></tr>
      <tr><td>📊 Nifty</td><td>${nf_t.length}</td><td>${nf_t.filter(t=>t.pnl>0).length}</td>
          <td style="color:${nf_t.reduce((s,t)=>s+t.pnl,0)>=0?'#48bb78':'#fc8181'}">${fmt(nf_t.reduce((s,t)=>s+t.pnl,0))}</td></tr>
      <tr><td>🎯 Target exits</td><td>${tgt_t.length}</td><td>—</td><td style="color:#48bb78">${fmt(tgt_t.reduce((s,t)=>s+t.pnl,0))}</td></tr>
      <tr><td>🛑 SL exits</td><td>${sl_t.length}</td><td>—</td><td style="color:#fc8181">${fmt(sl_t.reduce((s,t)=>s+t.pnl,0))}</td></tr>
      <tr><td>⏰ EOD exits</td><td>${eod_t.length}</td><td>—</td><td>${fmt(eod_t.reduce((s,t)=>s+t.pnl,0))}</td></tr>
    </table>`;

  // Trade Table (latest 50)
  const recent = [...trades].reverse().slice(0,50);
  if (recent.length === 0) {
    document.getElementById('trade-table').innerHTML = '<div class="no-trades">No trades yet</div>';
    return;
  }
  let thtml = `<table>
    <tr><th>Date</th><th>Symbol</th><th>Option</th><th>Dir</th><th>Lots</th>
        <th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Status</th></tr>`;
  recent.forEach(t => {
    const pc = t.pnl>=0?'green':'red';
    const statusParts = (t.status||'').split(' ');
    const badge = t.status&&t.status.includes('TARGET') ? 'badge-green' :
                  t.status&&t.status.includes('SL')     ? 'badge-red'   :
                  t.status&&t.status.includes('EOD')    ? 'badge-yellow' : 'badge-blue';
    thtml += `<tr>
      <td style="color:#a0aec0;font-size:12px">${t.date}</td>
      <td><b>${t.symbol}</b></td>
      <td style="font-size:12px;color:#a0aec0">${t.opt_sym||'—'}</td>
      <td><span class="badge ${t.direction==='CALL'?'badge-green':'badge-red'}">${t.direction}</span></td>
      <td>${t.lots}</td>
      <td>₹${(t.entry||0).toFixed(1)}</td>
      <td>₹${(t.exit||0).toFixed(1)}</td>
      <td style="font-weight:600;color:${t.pnl>=0?'#48bb78':'#fc8181'}">${fmt(t.pnl)}</td>
      <td><span class="badge ${badge}">${statusParts[0]||'—'}</span></td>
    </tr>`;
  });
  thtml += '</table>';
  document.getElementById('trade-table').innerHTML = thtml;
}

load();
setInterval(load, 30000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/trades")
def api_trades():
    if os.path.exists(TRADE_FILE):
        try:
            with open(TRADE_FILE) as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify([])


@app.route("/api/status")
def api_status():
    trades = []
    if os.path.exists(TRADE_FILE):
        try:
            with open(TRADE_FILE) as f:
                trades = json.load(f)
        except Exception:
            pass
    today = datetime.now(IST).strftime("%Y-%m-%d")
    today_t = [t for t in trades if t.get("date") == today]
    return jsonify({
        "status": "running",
        "time_ist": datetime.now(IST).strftime("%d %b %Y %H:%M:%S IST"),
        "total_trades": len(trades),
        "today_trades": len(today_t),
        "today_pnl": sum(t["pnl"] for t in today_t),
    })


def run_dashboard():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_dashboard()
