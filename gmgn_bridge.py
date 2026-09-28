"""GMGN живой мост: новые монеты из trenches-потока (public_broadcast WS).

Как работает: headless-Chromium проходит Cloudflare (сырые запросы — нет),
подписывается на public_broadcast каждой сети и ловит сигналы create =
создание пула в реальном времени (Solana pump/Raydium, Base/BSC/Robinhood).
Адрес уходит в fomo_signals.txt -> существующие гейты решают (метка GMGN:).

ТЯЖЁЛЫЙ модуль: +Chromium (~200-400MB RAM). На бесплатном Render не влезет —
включать только на тарифе 2GB+ (GMGN_ENABLED=True) либо локально.
Без браузера GMGN мёртв: API и WS отдают 403 без CF-чистки.

Формат кадра: {"channel":"public_broadcast","data":[{et, sig_op_t, c, ed:{
  sig_id, d:{a=адрес, s=тикер, nm=имя, mc=капа, ct/ot=создан, m_x/m_w=соцсети}}}]}
"""
import asyncio
import json
import time

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")

CHAIN_MAP = {"sol": "solana", "bsc": "bsc", "base": "base",
             "robinhood": "robinhood", "hood": "robinhood", "eth": "eth"}

_stats = {"frames": 0, "creates": 0, "pushed": 0, "unknown_ops": set()}


def _handle_frame(raw: str):
    try:
        d = json.loads(raw)
    except Exception:
        return
    if d.get("channel") != "public_broadcast":
        return
    try:
        from fomo_api import push_signal
    except Exception:
        return
    for it in d.get("data") or []:
        if not isinstance(it, dict):
            continue
        if it.get("et") != "signal":
            continue
        ed = it.get("ed") or {}
        op = ed.get("sig_op_t", "?")
        if op != "create":
            if len(_stats["unknown_ops"]) < 10:
                _stats["unknown_ops"].add(f'{it.get("c")}:{op}')
            continue
        _stats["creates"] += 1
        dd = ed.get("d") or {}
        addr = str(dd.get("a") or "")
        ch = CHAIN_MAP.get(str(it.get("c") or dd.get("n") or "").lower(), "")
        if ch == "solana":
            ok = 32 <= len(addr) <= 44 and not addr.startswith("0x")
        elif ch in ("bsc", "base", "robinhood", "eth"):
            ok = addr.startswith("0x") and len(addr) == 42
        else:
            continue
        if not ok:
            continue
        sym = str(dd.get("s") or "?")[:12]
        mc = dd.get("mc", 0) or 0
        try:
            mc_f = float(mc)
        except Exception:
            mc_f = 0.0
        push_signal(ch, addr, f"GMGN:{sym} mc${mc_f:,.0f}")
        _stats["pushed"] += 1


async def _chain_page(ctx, route: str):
    """Страница одной сети: держит WS живым, перезагружается при тишине."""
    page = await ctx.new_page()

    def on_ws(ws):
        def on_msg(m):
            try:
                s = m if isinstance(m, str) else m.decode()
            except Exception:
                return
            _stats["frames"] += 1
            _last["ts"] = time.time()
            _handle_frame(s)

        ws.on("framereceived", lambda m: on_msg(m))

    _last = {"ts": time.time()}
    page.on("websocket", on_ws)
    while True:
        try:
            await page.goto(f"https://gmgn.ai/{route}", timeout=45000)
            _last["ts"] = time.time()
            while True:
                await asyncio.sleep(30)
                if time.time() - _last["ts"] > 180:
                    print(f"📡 GMGN {route}: тишина 3 мин — перезагрузка.")
                    break
        except Exception as e:
            print(f"⚠️ GMGN {route}: {type(e).__name__}. Рестарт 10с...")
            await asyncio.sleep(10)


async def gmgn_bridge_loop(*_a, **_k):
    try:
        import config
        if not getattr(config, "GMGN_ENABLED", False):
            return
        chains = list(getattr(config, "GMGN_CHAINS", ["sol"]) or ["sol"])
    except Exception:
        return
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        print(f"⚠️ GMGN: нет playwright ({e}) — pip install playwright + install chromium.")
        return
    print(f"📡 GMGN мост: запуск Chromium, сети {chains}...")
    async with async_playwright() as pw:
        try:
            # Худой режим: без GPU/SHM/песочницы/аудио — один браузер на все сети.
            # Легче Chromium ничего не проходит CF (проверено: curl_cffi, куки,
            # сырой WS — все 403, валидируется TLS каждого соединения).
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-dev-shm-usage", "--no-sandbox",
                      "--disable-gpu", "--mute-audio",
                      "--disable-extensions", "--disable-background-timer-throttling",
                      "--js-flags=--max-old-space-size=256"])
        except Exception as e:
            print(f"⚠️ GMGN: браузер не стартовал ({e}).")
            return
        ctx = await browser.new_context(
            user_agent=UA, viewport={"width": 800, "height": 600},
            locale="en-US")
        await asyncio.gather(*[_chain_page(ctx, r) for r in chains])
