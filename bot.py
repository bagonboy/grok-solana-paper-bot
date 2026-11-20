#!/usr/bin/env python3
"""Live Solana market data + simulated paper trades. Never sends real orders."""
import os, json, time, base64, secrets, sqlite3, threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

DB = os.getenv("DB_PATH", "/data/paperbot.sqlite3")
START_CASH = float(os.getenv("START_CASH", "70"))
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "5"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "4"))
MIN_LIQ = float(os.getenv("MIN_LIQUIDITY", "10000"))
MIN_VOL = float(os.getenv("MIN_VOLUME_1H", "5000"))
MAX_AGE = float(os.getenv("MAX_TOKEN_AGE_HOURS", "24"))
STOP = float(os.getenv("STOP_LOSS_PCT", "20"))
TARGET = float(os.getenv("TAKE_PROFIT_PCT", "35"))
INTERVAL = max(20, int(os.getenv("SCAN_SECONDS", "60")))
USER = os.getenv("DASH_USER", "admin")
PASSWORD = os.getenv("DASH_PASSWORD", "")
lock = threading.RLock()
last_scan, last_error, candidates = "Starting", "", []

def stamp():
    return datetime.now(timezone.utc).isoformat()

def db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def init():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1),cash REAL,realized REAL)")
        c.execute("INSERT OR IGNORE INTO account VALUES(1,?,0)", (START_CASH,))
        c.execute("""CREATE TABLE IF NOT EXISTS positions(
          mint TEXT PRIMARY KEY,symbol TEXT,name TEXT,entry REAL,amount REAL,usd REAL,
          opened TEXT,last_price REAL,last_update TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
          id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT,action TEXT,mint TEXT,
          symbol TEXT,price REAL,usd REAL,pnl REAL,note TEXT)""")

def get_json(url):
    req = Request(url, headers={"User-Agent":"SolanaPaperBot/1.0","Accept":"application/json"})
    with urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode())

def scan_market():
    profiles = get_json("https://api.dexscreener.com/token-profiles/latest/v1")
    out = {}
    for profile in [p for p in profiles if p.get("chainId")=="solana" and p.get("tokenAddress")][:30]:
        mint = profile["tokenAddress"]
        try:
            pairs = get_json("https://api.dexscreener.com/token-pairs/v1/solana/"+mint)
            if not pairs: continue
            pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
            vol = float((pair.get("volume") or {}).get("h1") or 0)
            price = float(pair.get("priceUsd") or 0)
            created = int(pair.get("pairCreatedAt") or 0)
            age = (time.time()*1000-created)/3600000 if created else 99999
            change = float((pair.get("priceChange") or {}).get("h1") or 0)
            base = pair.get("baseToken") or {}
            if price > 0 and liq >= MIN_LIQ and vol >= MIN_VOL and age <= MAX_AGE:
                out[mint] = {"mint":mint,"symbol":str(base.get("symbol") or "TOKEN")[:16],
                  "name":str(base.get("name") or "Unknown")[:40],"price":price,
                  "liquidity":liq,"volume1h":vol,"change1h":change,"ageHours":round(age,1)}
        except Exception:
            continue
    return sorted(out.values(), key=lambda x:x["volume1h"], reverse=True)

def positions():
    with lock, db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM positions ORDER BY opened DESC")]

def record(action, item, price, usd, pnl, note):
    with lock, db() as c:
        c.execute("INSERT INTO trades(time,action,mint,symbol,price,usd,pnl,note) VALUES(?,?,?,?,?,?,?,?)",
          (stamp(),action,item["mint"],item["symbol"],price,usd,pnl,note))

def buy(item):
    with lock, db() as c:
        cash = c.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
        count = c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        exists = c.execute("SELECT 1 FROM positions WHERE mint=?", (item["mint"],)).fetchone()
        if count >= MAX_POSITIONS or cash < TRADE_SIZE or exists: return
        usd = min(cash, TRADE_SIZE)
        c.execute("UPDATE account SET cash=cash-? WHERE id=1", (usd,))
        c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?)",
          (item["mint"],item["symbol"],item["name"],item["price"],usd/item["price"],usd,stamp(),item["price"],stamp()))
    record("PAPER BUY",item,item["price"],usd,0,"Simulated fill only; no order sent")

def sell(pos, price, reason):
    proceeds = float(pos["amount"])*price
    pnl = proceeds-float(pos["usd"])
    with lock, db() as c:
        c.execute("UPDATE account SET cash=cash+?,realized=realized+? WHERE id=1",(proceeds,pnl))
        c.execute("DELETE FROM positions WHERE mint=?",(pos["mint"],))
    record("PAPER SELL",pos,price,proceeds,pnl,reason)

def scan_once():
    global last_scan,last_error,candidates
    try:
        fresh = scan_market()
        candidates = fresh
        by_mint = {x["mint"]:x for x in fresh}
        for pos in positions():
            item = by_mint.get(pos["mint"])
            if not item: continue
            pct = (item["price"]/float(pos["entry"])-1)*100
            if pct <= -STOP: sell(pos,item["price"],"Paper stop-loss")
            elif pct >= TARGET: sell(pos,item["price"],"Paper take-profit")
            else:
                with lock, db() as c:
                    c.execute("UPDATE positions SET last_price=?,last_update=? WHERE mint=?",
                              (item["price"],stamp(),pos["mint"]))
        held = {p["mint"] for p in positions()}
        for item in fresh:
            if item["mint"] not in held and item["change1h"] > 0:
                buy(item)
                held = {p["mint"] for p in positions()}
                if len(held) >= MAX_POSITIONS: break
        last_error = ""
    except Exception as exc:
        last_error = str(exc)[:250]
    last_scan = stamp()

def worker():
    while True:
        scan_once()
        time.sleep(INTERVAL)

def state():
    with lock, db() as c:
        acct = dict(c.execute("SELECT cash,realized FROM account WHERE id=1").fetchone())
        pos = [dict(r) for r in c.execute("SELECT * FROM positions").fetchall()]
        trades = [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 30").fetchall()]
    value = sum(p["amount"]*p["last_price"] for p in pos)
    return {"mode":"PAPER ONLY","cash":round(acct["cash"],2),"realizedPnl":round(acct["realized"],2),
      "openValue":round(value,2),"equity":round(acct["cash"]+value,2),"lastScan":last_scan,
      "lastError":last_error,"positions":pos,"trades":trades,"candidates":candidates[:20]}

HTML = """<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Solana Paper Bot</title><style>
body{font:15px system-ui;background:#101722;color:#edf3fa;margin:0;padding:16px}main{max-width:900px;margin:auto}
.card{background:#1d2938;padding:14px;border-radius:12px;margin:10px 0;overflow:auto}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.big{font-size:24px;font-weight:bold}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:8px;border-bottom:1px solid #405064}
small{color:#b9c6d5}@media(max-width:560px){.grid{grid-template-columns:1fr}body{padding:9px}}
</style></head><body><main><h2>Solana Meme Paper Bot</h2><p>🟢 PAPER TRADING ONLY — no wallet, no real orders</p>
<div class=grid><div class=card>Cash<div class=big id=cash>—</div></div><div class=card>Equity<div class=big id=equity>—</div></div>
<div class=card>Open positions<div class=big id=open>—</div></div><div class=card>Realized P/L<div class=big id=pnl>—</div></div></div>
<div class=card><b>Status</b><p id=status>Loading…</p></div><div class=card><h3>Open positions</h3><div id=positions></div></div>
<div class=card><h3>Recent paper trades</h3><div id=trades></div></div><div class=card><h3>Market candidates</h3><div id=candidates></div></div>
<small>Data from DexScreener. Simulated prices omit fees, slippage, failed fills and liquidity impact. Basic filters do not establish token safety. Experimental demo, not financial advice.</small></main>
<script>
const cash=n=>'$'+Number(n||0).toFixed(2);
function tbl(rows,cols){if(!rows.length)return '<small>None yet</small>';return '<table><tr>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+c[1](r)+'</td>').join('')+'</tr>').join('')+'</table>'}
async function refresh(){try{let r=await fetch('/state');if(!r.ok)throw Error('Not authorized');let d=await r.json();
for(let [id,v] of Object.entries({cash:cash(d.cash),equity:cash(d.equity),open:cash(d.openValue),pnl:cash(d.realizedPnl)}))document.getElementById(id).textContent=v;
document.getElementById('status').textContent='Mode: '+d.mode+' | Last scan: '+d.lastScan+' | '+(d.lastError||'No current error');
document.getElementById('positions').innerHTML=tbl(d.positions,[['Token',r=>r.symbol],['Entry',r=>Number(r.entry).toPrecision(5)],['Last',r=>Number(r.last_price).toPrecision(5)],['Value',r=>cash(r.amount*r.last_price)]]);
document.getElementById('trades').innerHTML=tbl(d.trades,[['Time',r=>r.time.slice(0,19)],['Action',r=>r.action],['Token',r=>r.symbol],['USD',r=>cash(r.usd)],['P/L',r=>cash(r.pnl)]]);
document.getElementById('candidates').innerHTML=tbl(d.candidates,[['Token',r=>r.symbol],['Liquidity',r=>cash(r.liquidity)],['1h volume',r=>cash(r.volume1h)],['1h %',r=>r.change1h.toFixed(1)+'%']]);
}catch(e){document.getElementById('status').textContent='Dashboard: '+e.message}}
refresh();setInterval(refresh,10000);
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def authorized(self):
        expected = "Basic "+base64.b64encode((USER+":"+PASSWORD).encode()).decode()
        return secrets.compare_digest(self.headers.get("Authorization",""),expected)
    def do_GET(self):
        if not self.authorized():
            self.send_response(401); self.send_header("WWW-Authenticate",'Basic realm="Paper Bot"'); self.end_headers(); return
        body = json.dumps(state()).encode() if self.path=="/state" else HTML.encode() if self.path=="/" else None
        if body is None: self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type","application/json" if self.path=="/state" else "text/html; charset=utf-8")
        self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

def main():
    if len(PASSWORD)<16: raise SystemExit("Set DASH_PASSWORD to a unique password of at least 16 characters in Railway Variables.")
    init()
    threading.Thread(target=worker,daemon=True).start()
    port=int(os.getenv("PORT","8080"))
    print("Paper bot dashboard listening on",port)
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()

if __name__=="__main__": main()
