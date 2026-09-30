"""Единый HTTP-клиент: общая сессия + ГЛОБАЛЬНЫЙ семафор + per-host троттлинг.

Шаг 1 анти-429: пачки запрещены.
- DexScreener: asyncio.Semaphore(3) — макс 3 одновременных запроса на весь процесс,
  остальные ждут слота в очереди. Между стартами +0.2с стаггер.
- GeckoTerminal: Semaphore(1) — строго по одному, интервал ~2.4с (25/мин).
- Остальные хосты: Semaphore(2-5) по щедрости тарифа.

Важно: семафор работает, только если ВСЕ идут через fetch_json().
Прямые aiohttp.ClientSession().get() в обход — это дырка в лимитере.
"""
import aiohttp
import asyncio
import time

_session = None

# Лимиты бесплатных API (запросов в минуту на весь процесс).
# DexScreener пары/поиск ~300/мин, профили/бусты ~60/мин,
# GeckoTerminal ~30/мин, DeFiLlama без ключа - щадим, Jupiter keyless ~30/мин (0.5 RPS).
HOST_LIMITS = {
    "api.dexscreener.com": 240.0,
    "api.geckoterminal.com": 25.0,
    "coins.llama.fi": 60.0,
    "api.llama.fi": 60.0,
    "api.coinbase.com": 60.0,
    "api.kraken.com": 60.0,
    "api.jup.ag": 25.0,
    "lite-api.jup.ag": 25.0,
    "tokens.jup.ag": 20.0,
    "quote-api.jup.ag": 20.0,
    "frontend-api.pump.fun": 20.0,
    "public-api.birdeye.so": 30.0,
    "lunarcrush.com": 10.0,
    "api.rugcheck.xyz": 30.0,
}

# Шаг 1: конкурентность по хостам. DS=3, GT=1 — требование задачи.
HOST_SEMAPHORES = {
    "api.dexscreener.com": 3,
    "api.geckoterminal.com": 1,
    "coins.llama.fi": 2,
    "api.llama.fi": 2,
    "api.coinbase.com": 2,
    "api.kraken.com": 2,
    "api.jup.ag": 1,
    "lite-api.jup.ag": 1,
    "tokens.jup.ag": 1,
    "quote-api.jup.ag": 1,
    "frontend-api.pump.fun": 1,
    "public-api.birdeye.so": 1,
    "api.rugcheck.xyz": 2,
}
DEFAULT_SEM = 5

# Стаггер между стартами запросов на один хост (анти-пачка).
# DS: 0.2с по задаче (при 3 слотах = до ~15 RPS пиком, средний держит лимит 240/мин).
HOST_STAGGER = {
    "api.dexscreener.com": 0.2,
    "api.geckoterminal.com": 0.0,  # у GT интервал 2.4с уже покрывает
}
DEFAULT_STAGGER = 0.0

_last_call: dict = {}
_sems: dict = {}
_gap_locks: dict = {}


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or "?"
    except Exception:
        return "?"


def _sem_for(host: str):
    """Ленивый семафор: в Python 3.9 примитивы привязаны к loop,
    поэтому создаём внутри рабочего цикла, один на (loop, host)."""
    try:
        loop = asyncio.get_running_loop()
        key = (id(loop), host)
        loop_ref = loop
    except RuntimeError:
        key = (0, host)
        loop_ref = None
    sem = _sems.get(key)
    if sem is None:
        n = DEFAULT_SEM
        for h, v in HOST_SEMAPHORES.items():
            if host == h or host.endswith("." + h):
                n = v
                break
        try:
            sem = asyncio.Semaphore(n, loop=loop_ref) if loop_ref is not None else asyncio.Semaphore(n)
        except TypeError:
            sem = asyncio.Semaphore(n)
        _sems[key] = sem
    return sem


def _gap_lock_for(host: str):
    try:
        loop = asyncio.get_running_loop()
        key = (id(loop), host)
        loop_ref = loop
    except RuntimeError:
        key = (0, host)
        loop_ref = None
    lock = _gap_locks.get(key)
    if lock is None:
        try:
            lock = asyncio.Lock(loop=loop_ref) if loop_ref is not None else asyncio.Lock()
        except TypeError:
            lock = asyncio.Lock()
        _gap_locks[key] = lock
    return lock


def _limit_for(host: str) -> float:
    for h, rpm in HOST_LIMITS.items():
        if host == h or host.endswith("." + h):
            return rpm
    return 0.0


def _stagger_for(host: str) -> float:
    for h, v in HOST_STAGGER.items():
        if host == h or host.endswith("." + h):
            return v
    return DEFAULT_STAGGER


async def _reserve_slot(url: str) -> float:
    """Резервирует слот старта: возвращает сколько секунд ждать.
    Бронь (метка времени) ставится под gap-локом, само ожидание — снаружи,
    поэтому 3 слота DS идут со стаггером 0.2с, а не строго по одному."""
    host = _host_of(url)
    rpm = _limit_for(host)
    stagger = _stagger_for(host)
    min_interval = (60.0 / rpm) if rpm else 0.0
    gap = max(min_interval, stagger)
    lock = _gap_lock_for(host)
    async with lock:
        now = time.monotonic()
        last = _last_call.get(host, 0.0)
        wait = gap - (now - last)
        if wait < 0:
            wait = 0.0
        _last_call[host] = now + wait
        return wait


def _note_429(host: str, penalty: float = 8.0):
    """Штраф за 429: сдвигаем бронь вперёд, чтобы очередь не долбила забаненный хост."""
    try:
        _last_call[host] = max(_last_call.get(host, 0.0), time.monotonic() + penalty)
    except Exception:
        pass


async def get_session():
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=15, connect=8)
        _session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _session


async def close_session():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()


async def fetch_json(url: str, params=None, headers=None, timeout: int = 12,
                     retries: int = 3, method: str = "GET", json_payload=None):
    """GET/POST JSON через ГЛОБАЛЬНЫЙ семафор + троттлинг + backoff на 429/5xx.

    Возвращает (status, data). data={} при неудаче. Никогда не кидает исключение.
    12 токенов встают в очередь семафора и идут по 3 (DS) / по 1 (GT).
    """
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname or "?"
    except Exception:
        host = "?"
    session = await get_session()
    hdrs = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    sem = _sem_for(host)
    last_status, last_err = 0, "?"
    for attempt in range(retries + 1):
        async with sem:
            wait = await _reserve_slot(url)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                if method == "POST":
                    ctx = session.post(url, params=params, headers=hdrs,
                                       json=json_payload, timeout=timeout)
                else:
                    ctx = session.get(url, params=params, headers=hdrs, timeout=timeout)
                async with ctx as r:
                    last_status = r.status
                    if r.status == 200:
                        try:
                            return r.status, await r.json(content_type=None)
                        except Exception:
                            return r.status, {}
                    if r.status == 404:
                        return r.status, {}
                    last_err = f"HTTP {r.status}"
                    if r.status == 429:
                        # 429 не долбим повторами впритык: штраф + длинный backoff
                        _note_429(host, penalty=8.0 + 4.0 * attempt)
            except asyncio.TimeoutError:
                last_err = "timeout"
            except Exception as e:
                last_err = f"{type(e).__name__}"
        if attempt < retries:
            # 429: 8с -> 12с -> 16с; остальное: 2с -> 5с -> 8с
            if "429" in str(last_err):
                await asyncio.sleep(8 + 4 * attempt)
            else:
                await asyncio.sleep(2 + 3 * attempt)
    print(f"🔌 API fail {host}{urlparse(url).path[:50]}: {last_err} (status {last_status})")
    return last_status, {}
