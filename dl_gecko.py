"""Закачка 1m/5m свечей с GeckoTerminal с дисковым кэшем и resume.

Использование:
  python3 dl_gecko.py mappicks      # mint -> pool address для пиков бота (Supabase)
  python3 dl_gecko.py cohort N      # N newest pump-fun пулов (по 20 на страницу)
  python3 dl_gecko.py dl            # докачать истории для data/gecko_cache/universe.json
Кэш: data/gecko_cache/pools/<pool>.csv  (time,open,high,low,close,volume)
Лимит API 30/мин — качаем медленно (~2.5 c/запрос) с ретраями на 429.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

CACHE = "data/gecko_cache"
POOLS = os.path.join(CACHE, "pools")
os.makedirs(POOLS, exist_ok=True)

BASE = "https://api.geckoterminal.com/api/v2"


def get(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r), False
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                wait = 15 + 10 * i
                print(f"  429, жду {wait}c...", flush=True)
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            last = e
            time.sleep(2 + 2 * i)
    raise RuntimeError(f"GET failed {url}: {last}")


def api(path):
    data, _ = get(BASE + path)
    return data


def universe_path():
    return os.path.join(CACHE, "universe.json")


def load_universe():
    p = universe_path()
    if os.path.exists(p):
        return json.load(open(p))
    return {}


def save_universe(u):
    json.dump(u, open(universe_path(), "w"))


def cmd_mappicks():
    sys.path.insert(0, ".")
    import config
    from supabase import create_client
    sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    mints = set()
    r = sb.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE_V3").execute()
    if r.data:
        feats = json.loads(r.data[0]["features"])
        mints.update(feats.keys())
    for tbl in ("trades_pump", "trades_raydium"):
        off = 0
        while True:
            rr = sb.table(tbl).select("mint").range(off, off + 999).execute()
            if not rr.data:
                break
            mints.update(d["mint"] for d in rr.data if d.get("mint"))
            if len(rr.data) < 1000:
                break
            off += 1000
    mints = {m for m in mints if m and not m.startswith("PORTFOLIO") and "_old_" not in m}
    print(f"Уникальных минтов бота: {len(mints)}")
    u = load_universe()
    new = 0
    for i, mint in enumerate(sorted(mints)):
        if mint in u and u[mint].get("pool"):
            continue
        try:
            d = api(f"/networks/solana/tokens/{mint}?include=top_pools")
            pools = [x for x in d.get("included", [])
                     if x.get("type") == "pool"]
            if not pools:
                print(f"  [{i+1}/{len(mints)}] {mint[:8]}: no pools", flush=True)
                save_universe(u)
                continue
            # топ-пул по резервной валюте/объёму: берём первый
            pool = pools[0]["attributes"]["address"]
            u[mint] = {"pool": pool, "type": "pick"}
            new += 1
            print(f"  [{i+1}/{len(mints)}] {mint[:8]} -> {pool[:8]}")
        except Exception as e:
            print(f"  [{i+1}/{len(mints)}] {mint[:8]} ERR {str(e)[:100]}")
        save_universe(u)
        time.sleep(2.5)
    print(f"Смапплено новых: {new}, всего в universe: {len(u)}")


def cmd_cohort(n):
    n = int(n)
    u = load_universe()
    pages = (n + 19) // 20
    got = 0
    for p in range(1, pages + 1):
        d = api(f"/networks/solana/dexes/pump-fun/pools?page={p}")
        for item in d["data"]:
            a = item["attributes"]
            addr = a["address"]
            base_mint = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
            mint = base_mint.split("_")[-1] if "_" in base_mint else ""
            key = mint or addr
            if key not in u:
                u[key] = {"pool": addr, "type": "cohort",
                          "pool_created": a.get("pool_created_at"),
                          "name": a.get("name")}
                got += 1
            if got >= n:
                break
        save_universe(u)
        print(f"  page {p}: universe={len(u)}")
        if got >= n:
            break
        time.sleep(2.5)
    print(f"Когорта: +{got}, всего в universe: {len(u)}")


def dl_pool(pool, agg_minutes=5, max_calls=10):
    """Качает свечи пула (новые дозаписываются). Возвращает число строк."""
    path = os.path.join(POOLS, f"{pool}.csv")
    have = set()
    if os.path.exists(path):
        with open(path) as f:
            for row in csv.DictReader(f):
                have.add(int(row["time"]))
    all_rows = {t: None for t in have}
    if os.path.exists(path):
        with open(path) as f:
            for row in csv.DictReader(f):
                all_rows[int(row["time"])] = row
    before = None
    calls = 0
    while calls < max_calls:
        url = (f"{BASE}/networks/solana/pools/{pool}/ohlcv/minute"
               f"?aggregate={agg_minutes}&limit=1000")
        if before:
            url += f"&before_timestamp={before}"
        try:
            d = api(url)
        except Exception as e:
            print(f"  pool {pool[:8]} ERR {str(e)[:100]}")
            break
        candles = d["data"]["attributes"]["ohlcv_list"]
        if not candles:
            break
        fresh = 0
        for c in candles:
            t = int(c[0])
            if t not in all_rows:
                fresh += 1
            all_rows[t] = {"time": t, "open": c[1], "high": c[2],
                           "low": c[3], "close": c[4], "volume": c[5]}
        calls += 1
        before = min(c[0] for c in candles)
        print(f"  pool {pool[:8]}: +{fresh} свечей (call {calls})", flush=True)
        if fresh == 0 or len(candles) < 1000:
            break
        time.sleep(2.5)
    rows = [all_rows[t] for t in sorted(all_rows)]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def cmd_dl():
    u = load_universe()
    items = [(k, v["pool"]) for k, v in u.items() if v.get("pool")]
    print(f"Пулов к закачке: {len(items)}")
    for i, (key, pool) in enumerate(items):
        n = dl_pool(pool)
        print(f"[{i+1}/{len(items)}] {key[:8]}: всего {n} свечей 5m", flush=True)
        time.sleep(2.5)
    print("Готово.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dl"
    if mode == "mappicks":
        cmd_mappicks()
    elif mode == "cohort":
        cmd_cohort(sys.argv[2] if len(sys.argv) > 2 else "200")
    else:
        cmd_dl()
