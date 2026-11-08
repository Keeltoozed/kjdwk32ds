import asyncio
import websockets
import json
import time
import aiohttp

# Программы бирж/агрегаторов. pump.fun и PumpSwap сверены с документацией, остальные - общеизвестные адреса.
# Фильтр НЕ полагается только на адреса: проходит и по тексту лога (Swap/Buy/Sell), так что ошибка в адресе не выбросит биржу.
DEX_PROGRAMS = (
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun (bonding curve)
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter v6
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",   # Meteora DLMM
)
SWAP_LOG_HINTS = ("Instruction: Buy", "Instruction: Sell", "Instruction: Swap", "SwapBaseIn", "SwapBaseOut", "ray_log", "Instruction: Route")

# Известные институциональные маркетмейкеры / MEV-роутеры (Wintermute и др.) — НЕ люди-снайперы
KNOWN_MM_BOTS = {
    "MfDuWeqSHEqTFVYZ7LoexgAK9dxk7cy4DFJWjWMGVWa",  # Wintermute MM
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
}

# Стейблы, обёрнутые BTC/ETH и тяжёлые majors — не мем-ракеты, не тратим на них квоту API
SKIP_COPY_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",   # cbBTC
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # WBTC
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # WETH
    "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4",  # JLP
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   # JUP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",   # ORCA
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",  # PYTH
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",  # POPCAT
    "nosXBVoaCTtYdLvKY6Csb4AC8JCdQKKAaWYtx2ZMoo7",   # NOS (Nosana major)
}

SKIP_COPY_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USDG", "EURC", "CBBTC", "WBTC", "WETH", "TBTC",
    "JLP", "JUP", "RAY", "ORCA", "PYTH", "JTO", "KMNO", "DRIFT", "NEAR", "WNEAR",
    "ZEC", "WIF", "$WIF", "BONK", "POPCAT", "TRUMP", "RENDER", "HNT", "MET", "NOS",
    "GRASS", "IO", "TNSR", "MEW", "BOME", "PENGU", "MELANIA", "FARTCOIN", "AI16Z",
    "VIRTUAL", "GOAT", "PNUT", "ACT", "MOODENG",
}


class CopyTrader:
    BANNED_FILE = "banned_whales.json"

    def __init__(self, tracker, analyzer=None):
        self.tracker = tracker
        self.analyzer = analyzer
        self._banned = self._load_banned()
        self._hft_excluded = set(KNOWN_MM_BOTS)  # сессионный блок HFT/MEV маркетмейкеров (без записи бана в Supabase)
        self._wallet_buy_times = {}              # wallet -> [ts покупок за 60с]
        self._mint_eval_lock = set()             # минты, которые прямо сейчас проверяются (защита от 25 параллельных вызовов)
        self._mint_skip_until = {}               # mint -> ts кулдауна после пропуска [WHALE-SKIP]
        self._wstat = {}          # кошелёк -> [прибыльных копий, убыточных копий]
        self._whale_cum_pnl = {}  # кошелёк -> суммарный чистый PnL % всех его сделок у нас
        self._junk_strikes = {}   # кошелёк -> количество страйков за зомби/мусорные токены
        self._copy_wallets = {}   # mint -> кошельки, чей сигнал мы скопировали
        self.wallets = self.load_wallets()
        import os as _os, config as _cfg
        self.HELIUS_API_KEY = _os.getenv("HELIUS_API_KEY") or getattr(_cfg, "HELIUS_API_KEY", "")
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}" if self.HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
        self.wss_url = f"wss://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}" if self.HELIUS_API_KEY else "wss://api.mainnet-beta.solana.com"
        self.processed_sigs = set()
        self._req_wallet = {}   # id запроса подписки -> кошелёк
        self._sub_wallet = {}   # id подписки -> кошелёк
        self._rate = {}         # кошелёк -> времена последних уведомлений
        self._muted = {}        # кошелёк -> до какого времени игнорируем
        self.whale_buys = {}      # mint -> {wallet: ts}
        try:
            _orig_close = self.tracker.close_position
            def _hooked_close(mint, exit_price, reason, *a, **k):
                # Сначала закрываем позицию через трекер, чтобы зафиксировать точный PnL и комиссии
                res = _orig_close(mint, exit_price, reason, *a, **k)
                try:
                    _p = self.tracker.positions.get(mint)
                    if _p is not None and str(getattr(_p, "source", "")).startswith("COPY"):
                        _sym = getattr(_p, "symbol", mint[:8])
                        _pnl_usd = float(getattr(_p, "pnl_usd", 0.0) or 0.0)
                        _orig_amt = float(getattr(_p, "original_amount_usd", 0.0) or getattr(_p, "amount_usd", 0.0) or 1.0)
                        _real_pnl_pct = (_pnl_usd / _orig_amt * 100.0) if _orig_amt > 0 else 0.0
                        self._score_wallets(mint, _real_pnl_pct, exit_reason=str(reason), symbol=_sym)
                except Exception as _err:
                    print(f"⚠️ [COPY-HOOK] Ошибка скоринга кита: {_err}")
                return res
            self.tracker.close_position = _hooked_close
        except Exception as _e:
            print(f"⚠️ [COPY] не удалось повесить учёт результатов на кошельки: {_e}")
        self.consensus_done = set()
        self.whale_first_price = {}  # mint -> цена в момент покупки ПЕРВОГО кита

    def _load_banned(self) -> set:
        """Загружает список навсегда забаненных китов из локального файла."""
        import os
        banned = set()
        if os.path.exists(self.BANNED_FILE):
            try:
                with open(self.BANNED_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        banned.update(data.keys())
                    elif isinstance(data, list):
                        banned.update(data)
            except Exception as e:
                print(f"⚠️ [COPY] Ошибка чтения {self.BANNED_FILE}: {e}")
        return banned

    def ban_whale_permanently(self, wallet: str, reason: str, symbol: str = "", pnl_pct: float = 0.0):
        """Навсегда банит кошелёк кита, если его сигнал не принёс прибыли:
        1. Добавляет в self._banned
        2. Удаляет из активных подписок
        3. Записывает в banned_whales.json на диске
        4. Обновляет Supabase: ставит tier = -1 (персистентный бан в облаке)
        """
        import os
        if not wallet or wallet in self._banned:
            return
        self._banned.add(wallet)

        # 1. Запись на диск
        records = {}
        if os.path.exists(self.BANNED_FILE):
            try:
                with open(self.BANNED_FILE, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    if not isinstance(records, dict):
                        records = {k: {} for k in records}
            except Exception:
                records = {}
        records[wallet] = {
            "banned_at": time.time(),
            "reason": reason,
            "symbol": symbol,
            "pnl_pct": round(pnl_pct, 2)
        }
        try:
            with open(self.BANNED_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ [COPY] Ошибка записи в {self.BANNED_FILE}: {e}")

        # 2. Обновляем в локальной базе охотника
        try:
            from smart_money_hunter import smart_money_hunter as _smh
            if wallet in _smh.db:
                _smh.db[wallet]["tier"] = -1
        except Exception:
            pass

        # 3. Обновляем в Supabase (tier = -1: навсегда забанен)
        try:
            def _bg_sb_ban():
                try:
                    import config as _cfg
                    url = getattr(_cfg, "SUPABASE_URL", None)
                    key = getattr(_cfg, "SUPABASE_KEY", None)
                    if url and key:
                        from supabase import create_client
                        sb = create_client(url, key)
                        sb.table("smart_wallets").update({"tier": -1}).eq("wallet", wallet).execute()
                        print(f"☁️ [SUPABASE] Кит {wallet[:8]}... помечен tier=-1 (BANNED)")
                except Exception as ex:
                    print(f"⚠️ [SUPABASE-BAN] Ошибка обновления в облаке: {ex}")
            import threading
            threading.Thread(target=_bg_sb_ban, daemon=True).start()
        except Exception:
            pass

        # 4. Удаляем из активных подписок
        trader_name = self.wallets.pop(wallet, f"@{wallet[:6]}")

        print(
            f"🚫 [WHALE PERMA-BAN] Кит {trader_name} ({wallet[:8]}...) НАВСЕГДА ЗАБАНЕН!\n"
            f"   Причина: {reason}\n"
            f"   Итог: PnL {pnl_pct:+.2f}%. Сигналы этого кита исключены из торговли!"
        )

    def _record_junk_signal(self, wallet: str, trader_name: str, mint: str, reason: str, is_hard_scam: bool = False):
        """Штрафует или банит кошельки за спам/зомби-монеты:
        - Киты Tier-1/2 с высоким PnL НЕ банятся по страйкам отсева (банятся ТОЛЬКО по реальной убыточной сделке)!
        """
        if not wallet or wallet in self._banned:
            return

        # Если у кита отличная история (Tier-1/2 или PnL > 0) — не баним его за то, что токен не подошел нашему сканеру!
        if "@Tier1" in trader_name or "@Tier2" in trader_name or "+$" in trader_name:
            print(f"ℹ️ [WHALE-INFO] Кит {trader_name} прислал сигнал {mint[:8]} ({reason}). Сделка пропущена, кит остаётся активен.")
            return

        if is_hard_scam:
            self.ban_whale_permanently(
                wallet,
                reason=f"Покупка опасного скама/рагпула {mint[:8]} ({reason})",
                symbol=mint[:8],
                pnl_pct=0.0
            )
            return

        strikes = self._junk_strikes.get(wallet, 0) + 1
        self._junk_strikes[wallet] = strikes
        if strikes >= 3:
            self.ban_whale_permanently(
                wallet,
                reason=f"3 страйка за мусорные/зомби сигналы (последний: {mint[:8]} — {reason})",
                symbol=mint[:8],
                pnl_pct=0.0
            )
        else:
            print(f"⚠️ [WHALE STRIKE {strikes}/3] Кошелёк {trader_name} прислал сигнал на неликвидный токен {mint[:8]} ({reason}).")

    def _repair_falsely_banned_whales(self):
        """Восстанавливает ошибочно забаненных китов (например, за токенизированные акции TTWO или фильтры соцсетей):
        1. Проверяет Supabase smart_wallets на tier == -1.
        2. Если у кита total_pnl > 0 и нет подтвержденного слива нашей копи-сделки:
           - Возвращает tier = 1 (или 2) в Supabase
           - Удаляет из self._banned
           - Удаляет из banned_whales.json
        """
        try:
            import os, json
            import config as _cf
            url = getattr(_cf, "SUPABASE_URL", None)
            key = getattr(_cf, "SUPABASE_KEY", None)
            if not (url and key):
                return
            from supabase import create_client
            sb = create_client(url, key)

            local_banned = {}
            if os.path.exists(self.BANNED_FILE):
                try:
                    with open(self.BANNED_FILE, "r", encoding="utf-8") as f:
                        local_banned = json.load(f) or {}
                except Exception:
                    local_banned = {}

            res = sb.table("smart_wallets").select("*").eq("tier", -1).execute()
            rows = res.data or []
            restored = 0
            for r in rows:
                w = r.get("wallet")
                if not w:
                    continue
                pnl = float(r.get("total_pnl") or 0.0)
                ban_info = local_banned.get(w, {})
                reason = str(ban_info.get("reason", "")).lower()

                # Настоящий бан происходит ТОЛЬКО при убытке в реальной скопированной сделке.
                # Все баны за проверки токенов (TTWO, no-socials, скам-аудит, страйки) — ошибочные и снимаются.
                is_false_ban = (
                    "no-socials" in reason
                    or "ttwo" in reason
                    or "ии выявил скам" in reason
                    or "страйк" in reason
                    or w.startswith("3kc6")
                    or w.startswith("Dtvm")  # Принёс PAWS +18.21%, получил ложный бан за -0.70% комиссий на флете DACITY
                    or (pnl >= 300.0 and "сделка дала убыток" not in reason and "убыток" not in reason)
                )

                if is_false_ban:
                    new_tier = 1 if pnl >= 3000.0 else (2 if pnl >= 1000.0 else 3)
                    sb.table("smart_wallets").update({"tier": new_tier}).eq("wallet", w).execute()
                    self._banned.discard(w)
                    if w in local_banned:
                        del local_banned[w]
                    try:
                        from smart_money_hunter import smart_money_hunter as _smh
                        if w in _smh.db:
                            _smh.db[w]["tier"] = new_tier
                    except Exception:
                        pass
                    restored += 1
                    print(f"♻️ [WHALE-RESTORE] Кит @Tier{new_tier}_{w[:4]}_+${pnl:,.0f} ({w[:8]}...) успешно РАЗБАНЕН и возвращён в WSS! Снят ложный бан.")

            if restored > 0 and os.path.exists(self.BANNED_FILE):
                try:
                    with open(self.BANNED_FILE, "w", encoding="utf-8") as f:
                        json.dump(local_banned, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ [WHALE-REPAIR] Ошибка автоматического разбана китов: {e}")

    def load_wallets(self):
        """Кошельки для копирования.
        1. Читает всех китов из Supabase / SmartMoneyHunter (1500-2000 кошельков).
        2. Исключает навсегда забаненных (tier <= 0 или в banned_whales.json).
        3. Ранжирует всех китов по PnL и подтвержденным победам (от лучших к худшим).
        4. Отбирает ТОП-80 самых прибыльных китов для активной WSS-подписки Helius (без 429 и лимитов сокета).
        5. При убытке/нуле от кита -> мгновенный перманентный бан (tier=-1 в Supabase),
           и его место в ТОП-80 сразу занимает следующий кит из базы!
        """
        import os
        import config as _cf

        # Разбаниваем ошибочно забаненных китов перед сборкой пула
        self._repair_falsely_banned_whales()

        candidates = {}  # wallet -> dict
        
        # 1. Живая база охотника (уже загружена из Supabase)
        try:
            from smart_money_hunter import smart_money_hunter as _smh
            for w, data in (_smh.db or {}).items():
                if isinstance(data, dict) and data.get("chain", "solana") == "solana":
                    candidates[w] = data
        except Exception:
            pass

        # 2. Локальный smart_wallets_db.json
        if os.path.exists("smart_wallets_db.json"):
            try:
                with open("smart_wallets_db.json", "r", encoding="utf-8") as f:
                    for w, data in json.load(f).items():
                        if w not in candidates and isinstance(data, dict):
                            candidates[w] = data
            except Exception:
                pass

        # 3. Резервная прямая подгрузка из Supabase (с пагинацией для 1500+ кошельков)
        if len(candidates) < 10:
            try:
                url = getattr(_cf, "SUPABASE_URL", None)
                key = getattr(_cf, "SUPABASE_KEY", None)
                if url and key:
                    from supabase import create_client
                    sb = create_client(url, key)
                    offset = 0
                    page_size = 1000
                    while True:
                        res = sb.table("smart_wallets").select("*").range(offset, offset + page_size - 1).execute()
                        rows = res.data or []
                        for r in rows:
                            w = r.get("wallet")
                            if w:
                                candidates[w] = {
                                    "chain": r.get("chain") or "solana",
                                    "tier": int(r.get("tier") or 3),
                                    "total_pnl": float(r.get("total_pnl") or 0.0),
                                    "trade_count": int(r.get("trade_count") or 0),
                                }
                        if len(rows) < page_size:
                            break
                        offset += page_size
            except Exception as e:
                print(f"⚠️ [COPY] Ошибка чтения Supabase: {e}")

        min_pnl = float(getattr(_cf, "COPY_MIN_PNL", 300.0))
        # На пуле публичных RPC распределяем до 80 китов по 20 на сокет; с Helius ключом — до 100
        _has_helius = bool(getattr(self, "HELIUS_API_KEY", "")) and not getattr(self, "_helius_disabled", False)
        max_active = int(getattr(_cf, "COPY_MAX_ACTIVE_WHALES", 80)) if not _has_helius else 100

        valid_whales = []
        for w, d in candidates.items():
            if w in self._banned or w in getattr(self, "_hft_excluded", set()) or w in KNOWN_MM_BOTS or w.startswith("MfDu"):
                continue
            tier = int(d.get("tier", 3) or 3)
            if tier <= 0:  # tier <= 0 означает забанен в Supabase
                self._banned.add(w)
                continue
            pnl = float(d.get("total_pnl", 0.0) or 0.0)
            trades = int(d.get("trade_count", 0) or 0)

            # Отсекаем HFT/арбитражных ботов (>350 сделок в базе) и кошельки с низким PnL
            if trades > 350:
                continue
            if pnl < min_pnl and tier not in (1, 2):
                continue

            tier_mult = 2.0 if tier == 1 else (1.4 if tier == 2 else 1.0)
            rank_score = (pnl * tier_mult) + (trades * 10.0)
            valid_whales.append((w, rank_score, tier, pnl, trades))

        # Сохраняем полный пул китов для скоринга в анализаторе и памп.фане
        self.all_whales_pool = {w for w, _, _, _, _ in valid_whales}

        # Сортируем: сверху самые прибыльные и проверенные киты
        valid_whales.sort(key=lambda x: x[1], reverse=True)

        # Отбираем ТОП-N для активной WSS подписки
        active_wallets = {}
        for w, _, tier, pnl, trades in valid_whales[:max_active]:
            active_wallets[w] = f"@Tier{tier}_{w[:4]}_+${pnl:,.0f}"

        # Дефолтный fallback если база пуста
        if not active_wallets:
            default_whales = {
                "5FGoPPj1nL8LCnfVnpTmreqQtqLuMXXAwuS1uahMrp8V": "@DumbCrayon",
                "2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V": "@Salem1299",
                "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm": "@Whale_TopPnL_1",
                "7rhxnLV8C76MiKSnSqhMYnNvBjRHdJP36yGjwmF3tV3x": "@Whale_TopPnL_2",
                "3FqQ42gknhMNNXnRMm7e4RU83Gk7VdcpRDQsgpn3eWPf": "@Whale_TopPnL_3",
            }
            for w, name in default_whales.items():
                if w not in self._banned:
                    active_wallets[w] = name

        print(
            f"🐋 [COPYTRADER] Загружено китов: {len(active_wallets)} активных в WSS "
            f"(из {len(candidates)} в базе Supabase) | Забанено за убытки: {len(self._banned)}"
        )
        return active_wallets

    def _score_wallets(self, mint, pnl_pct, exit_reason: str = "", symbol: str = ""):
        """Учёт результата КОПИИ по кошелькам.
        Если сделка дала убыток или 0 прибыли (pnl_pct <= 0) -> КИТ БАНИТСЯ НАВСЕГДА!
        """
        import config as _c
        whales = list(self._copy_wallets.get(mint, set()))
        if not whales:
            return

        for w in whales:
            st = self._wstat.setdefault(w, [0, 0])
            cum_pnl = float(self._whale_cum_pnl.get(w, 0.0)) + float(pnl_pct)
            self._whale_cum_pnl[w] = cum_pnl
            if pnl_pct > 0.0: # Любая чистая прибыль > $0 после комиссий DEX и Jito = победа кита
                st[0] += 1
                trader_name = self.wallets.get(w, f"@{w[:6]}")
                print(
                    f"🏆 [WHALE PROFIT] Кит {trader_name} принёс чистую прибыль {pnl_pct:+.2f}% на {symbol} "
                    f"(суммарно {cum_pnl:+.2f}%)! Статистика: {st[0]} побед / {st[1]} поражений. Оставляем в топе."
                )
            elif cum_pnl > 2.0 and pnl_pct > -5.0:
                # Кит уже принёс нам ощутимый плюс (например PAWS +18.2%) и закрыл царапину на комиссиях (-0.7%):
                # мы по-прежнему в чистом плюсе по этому киту (+17.5%) — НЕ баним проверенного снайпера!
                st[1] += 1
                trader_name = self.wallets.get(w, f"@{w[:6]}")
                print(
                    f"🛡️ [WHALE SURPLUS SHIELD] Кит {trader_name} закрыл {symbol} в {pnl_pct:+.2f}%, "
                    f"но его чистый профит для нас остаётся +{cum_pnl:.2f}% — сохраняем кита в строю!"
                )
            else:
                # Суммарно мы на ките не заработали (<= 0%) или словили жесткий слив (<= -5%) -> БАН НАВСЕГДА!
                st[1] += 1
                trader_name = self.wallets.get(w, f"@{w[:6]}")
                reason = f"Сигнал на {symbol} дал {pnl_pct:+.2f}% (суммарно {cum_pnl:+.2f}%). Причина выхода: {exit_reason}"
                self.ban_whale_permanently(w, reason=reason, symbol=symbol, pnl_pct=pnl_pct)
        self._copy_wallets.pop(mint, None)

    def _handle_ws_message(self, message, req_wallet: dict = None, sub_wallet: dict = None) -> None:
        """Одно сообщение WSS. Проваленные транзакции (у ботов их много) и перегруз очереди отбрасываем,
        иначе на каждую запись уходит 1-3 запроса getTransaction -> 429 у Helius и разрывы соединения."""
        try:
            data = json.loads(message)
        except Exception:
            return
        _rw = req_wallet if req_wallet is not None else self._req_wallet
        _sw = sub_wallet if sub_wallet is not None else self._sub_wallet
        # ответ на подписку: запоминаем, какой id подписки принадлежит какому кошельку на ЭТОМ сокете
        if "id" in data and "result" in data and "method" not in data:
            w = _rw.pop(data["id"], None)
            if w is not None:
                _sw[data["result"]] = w
            return
        if data.get("method") != "logsNotification":
            return
        try:
            val = data["params"]["result"]["value"]
        except Exception:
            return
        if val.get("err"):
            return                      # транзакция провалилась - покупки не было
        sig = val.get("signature")
        if not sig or sig in self.processed_sigs:
            return

        # 1) не своп/покупка (переводы, стейкинг, вотчдоги и т.п.) - выбрасываем по логам, без запросов к сети
        logs = val.get("logs") or []
        try:
            import config as _cf
            if getattr(_cf, "COPY_PREFILTER", True) and logs:
                progs = tuple(getattr(_cf, "COPY_DEX_PROGRAMS", DEX_PROGRAMS))
                if not any((p in l) for l in logs for p in progs) and not any((h in l) for l in logs for h in SWAP_LOG_HINTS):
                    self._pre_dropped = getattr(self, "_pre_dropped", 0) + 1
                    return
        except Exception:
            pass

        # 2) Авто-отсечение HFT / MEV / маркетмейкер-ботов (например Wintermute MfDu...):
        # живой человек-снайпер никогда не отправляет >12 DEX-транзакций за 60 секунд!
        wallet = _sw.get(data["params"].get("subscription"))
        if wallet:
            if wallet in self._banned or wallet in self._hft_excluded:
                return
            now_ws = time.time()
            w_times = [t for t in self._rate.get(wallet, []) if (now_ws - t) <= 60.0]
            w_times.append(now_ws)
            self._rate[wallet] = w_times
            if len(w_times) > 12:
                w_name = self.wallets.pop(wallet, wallet[:8])
                self._hft_excluded.add(wallet)
                print(f"🤖 [HFT-BOT-EXCLUDED] {w_name} ({wallet[:8]}...): >12 DEX-свопов/мин (маркетмейкер/MEV-роутер) — отключён от копитрейдинга (защита API от 429)!")
                return
        if getattr(self, "_pending", 0) >= 500:
            self._dropped = getattr(self, "_dropped", 0) + 1
            if self._dropped % 200 == 1:
                print(f"⚠️ Копитрейдер: очередь перегружена ({self._pending}), пропущено {self._dropped} (отсеяно префильтром: {getattr(self, '_pre_dropped', 0)})")
            return
        self._pending = getattr(self, "_pending", 0) + 1
        asyncio.create_task(self._guarded_process(sig, hint_wallet=wallet))

    async def _guarded_process(self, sig: str, hint_wallet: str = None) -> None:
        if not hasattr(self, "_sem") or self._sem is None:
            self._sem = asyncio.Semaphore(20)
        try:
            async with self._sem:
                await self.process_transaction(sig, hint_wallet=hint_wallet)
        except Exception as e:
            print(f"⚠️ Копитрейдер: ошибка обработки {sig[:8]}: {type(e).__name__}")
        finally:
            self._pending = max(0, getattr(self, "_pending", 1) - 1)

    async def _subscribe_new(self, ws, subscribed: set) -> int:
        """Подписывается на кошельки, на которые ещё нет подписки (в т.ч. найденные охотником уже после старта)."""
        n = 0
        for wallet in list(self.wallets.keys()):
            if wallet in subscribed:
                continue
            self._req_id = getattr(self, "_req_id", 0) + 1
            self._req_wallet[self._req_id] = wallet
            await ws.send(json.dumps({
                "jsonrpc": "2.0", "id": self._req_id, "method": "logsSubscribe",
                "params": [{"mentions": [wallet]}, {"commitment": "processed"}]}))
            subscribed.add(wallet)
            n += 1
            await asyncio.sleep(0.05)   # не заливаем сервер сотней подписок за один миг
        return n

    async def _refresh_loop(self, ws, subscribed: set):
        """Раз в 2 минуты перечитывает базу охотника и подписывается на новых китов, не переподключаясь."""
        while True:
            await asyncio.sleep(120)
            try:
                self.wallets = self.load_wallets()
                added = await self._subscribe_new(ws, subscribed)
                if added:
                    print(f"👥 Копитрейдер: подписался на {added} новых китов (всего {len(subscribed)})")
            except Exception as e:
                print(f"⚠️ Копитрейдер: ошибка обновления базы китов: {e}")
                return

    async def get_token_price(self, mint):
        try:
            import market_data
            p = await market_data.get_price(mint)
            if p > 0:
                return p
        except:
            pass
        try:
            from http_client import fetch_json
            status, data = await fetch_json(
                f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                timeout=8, retries=1)
            if status == 200 and data:
                pairs = data.get("pairs") or []
                if pairs:
                    return float(pairs[0].get("priceUsd", 0))
        except:
            pass
        return 0.0001

    async def get_http_session(self):
        if not hasattr(self, "_session") or self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=60, keepalive_timeout=60)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def fetch_transaction(self, signature):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        
        rpc_endpoints = []
        if getattr(self, "HELIUS_API_KEY", "") and not getattr(self, "_helius_disabled", False):
            rpc_endpoints.append(self.rpc_url)
        rpc_endpoints.extend([
            "https://solana-rpc.publicnode.com/",
            "https://api.mainnet-beta.solana.com/",
        ])
        
        session = await self.get_http_session()
        for rpc in rpc_endpoints:
            try:
                async with session.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        if "helius" in rpc.lower():
                            self._helius_disabled = True
                        continue
            except Exception:
                continue
        return None

    async def _execute_copy_buy(self, wallet: str, trader_name: str, mint: str, symbol: str, cur_price: float = 0.0):
        """Единая логика безопасного входа в сделку по сигналу кита (из WSS или PumpPortal).
        Принцип: если проверенный прибыльный кит покупает — мы покупаем за ним!
        Проверяем только безопасность (не ханипот / не фриз / не дрейн).
        Если сделка закрывается в минус — кит навсегда банится в Supabase (tier = -1)."""
        import config

        if mint in SKIP_COPY_MINTS:
            return

        now_c = time.time()
        if len(self._mint_skip_until) > 1500:
            self._mint_skip_until = {m: ts for m, ts in self._mint_skip_until.items() if ts > now_c}
        if now_c < self._mint_skip_until.get(mint, 0.0):
            return
        if mint in self._mint_eval_lock:
            return

        # 1. Проверка общего лимита позиций, квоты копитрейдера (<=2 слотов) и лимита 1 сделка на 1 кита
        open_positions = self.tracker.get_open_positions()
        if len(open_positions) >= config.MAX_CONCURRENT_POSITIONS:
            print(f"🚫 [COPY] Лимит позиций ({len(open_positions)}/{config.MAX_CONCURRENT_POSITIONS}). Пропускаем копитрейд {mint[:8]}.")
            return
        max_copy_slots = int(getattr(config, "COPY_MAX_CONCURRENT_POSITIONS", 2))
        open_copy_positions = [p for p in open_positions.values() if str(getattr(p, "source", "")).startswith("COPY")]
        if len(open_copy_positions) >= max_copy_slots:
            print(f"🚫 [COPY] Заняты все слоты копитрейда ({len(open_copy_positions)}/{max_copy_slots}) — бережём слоты для основного сканера ракет.")
            return
        for _op_m, _op_p in open_positions.items():
            if wallet in self._copy_wallets.get(_op_m, set()):
                print(f"🚫 [COPY] У кита {trader_name} уже открыта сделка {_op_p.symbol} — не открываем пачку параллельных копий от одного кошелька.")
                return

        if mint in open_positions:
            return

        self._mint_eval_lock.add(mint)
        try:
            # 2. Получаем актуальные данные пары с DexScreener для реального символа и цены
            price_usd = cur_price
            clean_symbol = (symbol or mint[:8]).strip().upper()
            if clean_symbol in SKIP_COPY_SYMBOLS:
                self._mint_skip_until[mint] = time.time() + 1800.0
                return
            pair_data = None
            try:
                from grok_jev import grok_jev as _gj
                pair_data = await _gj.fetch_pair(mint, "solana")
                if pair_data:
                    base_info = pair_data.get("baseToken") or {}
                    real_sym = str(base_info.get("symbol") or "").strip().upper()
                    if real_sym and not real_sym.startswith("COPY_") and real_sym != "UNKNOWN":
                        clean_symbol = real_sym
                    _px = float(pair_data.get("priceUsd", 0) or 0)
                    if _px > 0:
                        price_usd = _px
            except Exception:
                pass

            if clean_symbol in SKIP_COPY_SYMBOLS:
                self._mint_skip_until[mint] = time.time() + 1800.0
                return

            if not price_usd or price_usd <= 0:
                price_usd = await self.get_token_price(mint)

            # 3. Глубокий ИИ-анализ токена кита через Analyzer
            if self.analyzer:
                print(f"🤖 Передаем токен {clean_symbol} ({mint[:8]}) кита {trader_name} ИИ на глубокий анализ...")
                is_safe, ai_reason = await self.analyzer.analyze_whale_token(mint, trader_name=trader_name, prefetched_pair=pair_data)
                if not is_safe:
                    self._mint_skip_until[mint] = time.time() + 180.0
                    print(f"🚫 [WHALE-SKIP] ИИ забраковал токен {clean_symbol} ({mint[:8]}) кита {trader_name}: {ai_reason}. Сделка пропущена, кит остаётся активен.")
                    return
                else:
                    print(f"✅ ИИ одобрил токен {clean_symbol} кита {trader_name} ({ai_reason})! Покупаем!")
            elif getattr(config, "COPY_USE_ANTISCAM", True) and pair_data:
                try:
                    from grok_jev import grok_jev as _gj
                    _ok, _why, _hs = await _gj.full_anti_scam_audit(mint, clean_symbol, "solana", pair_data)
                    if not _ok:
                        self._mint_skip_until[mint] = time.time() + 180.0
                        print(f"🚫 [WHALE-SKIP] Токен {clean_symbol} ({mint[:8]}) кита {trader_name} не прошёл антискам ({_why}). Пропуск сделки.")
                        return
                except Exception as _e:
                    print(f"⚠️ [COPY] Антискам-проверка ({type(_e).__name__}), продолжаем: {_e}")

            # 4. Проверка ликвидности пула и синхронизация с живой ценой Jupiter
            if pair_data:
                liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
                if 0 < liq < 500.0:
                    self._mint_skip_until[mint] = time.time() + 120.0
                    print(f"🚫 [COPY] Слишком низкая ликвидность пула (${liq:.0f} < $500) для {clean_symbol}. Пропуск.")
                    return

            dex_id_cp = str((pair_data or {}).get("dexId") or "").lower()
            if dex_id_cp not in ("pump", "pumpfun"):
                try:
                    from jupiter import JupiterAPI
                    j_px = await JupiterAPI.get_price(mint)
                    if j_px and j_px > 0:
                        if price_usd > 0:
                            r_cp = max(j_px, price_usd) / min(j_px, price_usd)
                            if r_cp > 1.12:
                                self._mint_skip_until[mint] = time.time() + 90.0
                                print(f"🚫 [COPY-PRICE-MISMATCH] {clean_symbol} ({mint[:8]}): расхождение Jupiter (${j_px:.8f}) vs DS (${price_usd:.8f}) {r_cp:.2f}x — защита от ложного стопа, пропуск.")
                                return
                        price_usd = j_px
                except Exception:
                    pass

            if not price_usd or price_usd <= 0:
                return

            # 5. Размер позиции ($4.00 базовый вход)
            capital = self.tracker.get_total_capital()
            _pct = float(getattr(config, "COMPOUNDING_PCT", 0.10))
            _base = float(getattr(config, "BASE_TRADE_AMOUNT_USD", 4.0))
            _cap = float(getattr(config, "COPY_MAX_TICKET_USD", 4.0))
            position_size = max(_base, min(_cap, capital * _pct))

            # 6. Запоминаем кошелёк кита для этого минта и открываем позицию (с полными фичами и ML-скором!)
            if mint not in self._copy_wallets:
                self._copy_wallets[mint] = set()
            self._copy_wallets[mint].add(wallet)
            self.consensus_done.add(mint)

            _copy_ml_conf = 25.0
            _copy_ml_feats = {}
            if self.analyzer and pair_data:
                try:
                    _copy_ml_conf = float(getattr(self.analyzer, "rocket_scores", {}).get(mint, getattr(self.analyzer, "last_score", 0.0)) or 0.0)
                    if _copy_ml_conf <= 0.0:
                        _copy_ml_conf = float(self.analyzer.score_rocket_xgb(mint, pair_data) or 0.0)
                    _copy_ml_feats = self.analyzer.pack_features(pair_data)
                except Exception:
                    pass

            self.tracker.add_position(
                clean_symbol,
                mint,
                price_usd,
                amount_usd=position_size,
                is_mature=True,
                ml_features=_copy_ml_feats,
                ml_confidence=_copy_ml_conf,
                source=f"COPY:{trader_name}",
                pair_address=((pair_data or {}).get("pairAddress", "") or ""),
            )
            print(f"✅ 🐋 [COPY-EXECUTED] Успешно вошли по сделке кита {trader_name} в {clean_symbol} на ${position_size:.2f} (цена: ${price_usd})!")
        finally:
            self._mint_eval_lock.discard(mint)

    async def handle_whale_buy(self, wallet: str, mint: str, symbol: str, sol_amount: float = 0.0, mc_sol: float = 0.0):
        """Прямой вызов при обнаружении покупки кита из базы Supabase (например, через PumpPortal stream).
        Срабатывает мгновенно для ВСЕХ 1583 китов без WSS-ограничений!"""
        if not wallet or wallet in self._banned:
            return
        open_pos = self.tracker.get_open_positions()
        if mint in open_pos:
            return
        trader_name = self.wallets.get(wallet, f"@Tier1_{wallet[:4]}")
        cur_price = await self.get_token_price(mint)
        now = time.time()
        self.whale_buys.setdefault(mint, {})
        self.whale_buys[mint][wallet] = now
        self.whale_buys[mint] = {w: t for w, t in self.whale_buys[mint].items() if now - t <= 600}
        n_whales = len(self.whale_buys[mint])
        import config as _cfg
        _consensus = getattr(_cfg, 'WHALE_CONSENSUS', 1)
        if n_whales < _consensus:
            return
        print(f"🐋 [PUMP WHALE COPY] Сигнал кита {trader_name} на {symbol} ({mint[:8]}) -> исполняем копирование!")
        await self._execute_copy_buy(wallet, trader_name, mint, symbol, cur_price)

    async def process_transaction(self, signature, hint_wallet: str = None):
        if signature in self.processed_sigs: return
        self.processed_sigs.add(signature)
        if len(self.processed_sigs) > 5000: self.processed_sigs.clear()
        
        # Первая быстрая попытка, затем до 2 повторов по мере финализации блока (processed -> confirmed)
        tx_data = await self.fetch_transaction(signature)
        if not tx_data or "result" not in tx_data or not tx_data["result"]:
            await asyncio.sleep(0.75)
            tx_data = await self.fetch_transaction(signature)
            if not tx_data or "result" not in tx_data or not tx_data["result"]:
                await asyncio.sleep(1.1)
                tx_data = await self.fetch_transaction(signature)
                if not tx_data or "result" not in tx_data or not tx_data["result"]:
                    return

        meta = tx_data["result"].get("meta", {})
        if not meta or meta.get("err"): return # Ошибка транзакции (Failed)

        pre_bals = meta.get("preTokenBalances", [])
        post_bals = meta.get("postTokenBalances", [])

        # Быстрый O(1) поиск китов в транзакции через пересечение множеств (+ hint_wallet с подписки сокета)
        tx_owners = {b.get("owner") for b in pre_bals if b.get("owner")} | {b.get("owner") for b in post_bals if b.get("owner")}
        matching_whales = tx_owners.intersection(self.wallets.keys())
        if not matching_whales and hint_wallet and hint_wallet in self.wallets and hint_wallet in tx_owners:
            matching_whales = {hint_wallet}
        if not matching_whales:
            return

        for wallet in matching_whales:
            trader_name = self.wallets[wallet]
            pre_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in pre_bals if b.get("owner") == wallet}
            post_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in post_bals if b.get("owner") == wallet}

            all_mints = set(pre_dict.keys()) | set(post_dict.keys())
            for mint in all_mints:
                if len(mint) < 30 or mint in SKIP_COPY_MINTS:
                    continue
                
                pre_amt = pre_dict.get(mint, 0.0)
                post_amt = post_dict.get(mint, 0.0)

                # 1. ОТСЛЕЖИВАНИЕ СБРОСА КИТА: если кит продаёт монету, которая у нас открыта — выходим вслед за ним!
                if post_amt < pre_amt and pre_amt > 0:
                    open_pos = self.tracker.get_open_positions()
                    _own = str(getattr(open_pos.get(mint), "source", "")).startswith("COPY")
                    import config as _cx
                    if mint in open_pos and (_own or not getattr(_cx, "COPY_EXIT_ONLY_OWN", True)):
                        sell_pct = (pre_amt - post_amt) / pre_amt
                        cur_p = await self.get_token_price(mint)
                        print(f"🐋 [WHALE DUMP] {trader_name} продал {sell_pct*100:.0f}% доли {mint[:12]}! Срочно выходим вслед за китом...")
                        if sell_pct >= 0.70:
                            self.tracker.close_position(mint, cur_p, f"Whale Exit ({trader_name} dump {sell_pct*100:.0f}%)")
                        elif getattr(_cx, "COPY_FRONTRUN_ENABLED", False):
                            self.tracker.partial_close_position(mint, cur_p, 0.50, f"Whale Frontrun ({trader_name} sold {sell_pct*100:.0f}%)")

                elif post_amt > pre_amt: # Баланс вырос = ПОКУПКА
                    import config
                    now = time.time()
                    if wallet in self._banned or wallet in self._hft_excluded:
                        continue
                    # Дополнительный детектор HFT-маркетмейкера по частоте покупок (>5 покупок за 60с = робот)
                    b_times = [t for t in self._wallet_buy_times.get(wallet, []) if (now - t) <= 60.0]
                    b_times.append(now)
                    self._wallet_buy_times[wallet] = b_times
                    if len(b_times) > 5:
                        self._hft_excluded.add(wallet)
                        self.wallets.pop(wallet, None)
                        print(f"🤖 [HFT-BOT-EXCLUDED] {trader_name} ({wallet[:8]}...): >5 покупок за 60с — маркетмейкер/арбитраж-бот исключён!")
                        break
                    if now < self._mint_skip_until.get(mint, 0.0) or mint in self._mint_eval_lock:
                        continue
                    open_pos = self.tracker.get_open_positions()
                    if mint in open_pos:
                        continue
                    self.whale_buys.setdefault(mint, {})
                    self.whale_buys[mint][wallet] = now
                    self.whale_buys[mint] = {w: t for w, t in self.whale_buys[mint].items() if now - t <= 600}
                    n_whales = len(self.whale_buys[mint])
                    _consensus = getattr(config, 'WHALE_CONSENSUS', 1)
                    if n_whales == 1:
                        self.whale_first_price[mint] = await self.get_token_price(mint)
                        if _consensus > 1:
                            print(f"👁️ WHALE WATCH: {trader_name} купил {mint[:12]}... слежу за ценой (вход на {_consensus}-м ките).")
                        else:
                            print(f"🐋 WHALE SIGNAL: {trader_name} купил {mint[:12]}... мгновенный вход (consensus=1)!")
                    print(f"🚨 COPYTRADE: {trader_name} купил {mint[:12]}... (китов за 10 мин: {n_whales}/{_consensus})")
                    if n_whales < _consensus:
                        continue  # ждём раннего консенсуса
                    # Guard от опоздания: цена не должна улететь от точки входа первого кита
                    cur_price = await self.get_token_price(mint)
                    first_price = self.whale_first_price.get(mint, 0)
                    max_runup = getattr(config, 'WHALE_MAX_RUNUP', 1.35)
                    if first_price > 0 and cur_price > first_price * max_runup:
                        print(f"🚫 [WHALE LATE] {mint[:12]}: цена +{(cur_price/first_price-1)*100:.0f}% с покупки 1-го кита — киты уже надули, входим в их выход.")
                        continue
                    print(f"🐋 SMART MONEY CONSENSUS: {n_whales} кита, цена ещё не улетела (+{(cur_price/first_price-1)*100:.0f}%) — РАННИЙ вход!")
                    await self._execute_copy_buy(wallet, trader_name, mint, "", cur_price)

    async def _listen_single_socket(self, wss_url: str, wallet_subset: dict):
        """Слушает выделенный пул китов (20 адресов) на конкретном RPC сокете с изолированной таблицей подписок."""
        name = wss_url.split("?")[0].replace("wss://", "").split("/")[0]
        fail_count = 0
        while True:
            try:
                async with websockets.connect(wss_url, ping_interval=20, ping_timeout=60, close_timeout=5, max_size=2 ** 22) as ws:
                    print(f"👥 [WSS-POOL] {name}: подключен, слушаем {len(wallet_subset)} китов...")
                    fail_count = 0
                    req_wallet = {}
                    sub_wallet = {}
                    local_req_id = 0
                    for wallet in list(wallet_subset.keys()):
                        if wallet in self._banned:
                            continue
                        local_req_id += 1
                        req_wallet[local_req_id] = wallet
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": local_req_id, "method": "logsSubscribe",
                            "params": [{"mentions": [wallet]}, {"commitment": "processed"}]
                        }))
                        await asyncio.sleep(0.06)
                    async for message in ws:
                        self._handle_ws_message(message, req_wallet=req_wallet, sub_wallet=sub_wallet)
            except Exception as e:
                err = str(e)
                fail_count += 1
                if "helius" in wss_url.lower() and ("429" in err or "rejected" in err or fail_count >= 2):
                    print(f"⚠️ [HELIUS-QUOTA] Helius WSS вернул HTTP 429 (исчерпан месячный лимит бесплатного ключа). Авто-переход на бесплатный пул публичных нод Solana!")
                    return False
                if "429" in err or "rejected" in err:
                    if fail_count >= 2:
                        print(f"⚠️ [WSS-POOL] {name}: лимит подключений (429) — тихий сон 60с для предотвращения бана...")
                        await asyncio.sleep(60)
                        continue
                print(f"⚠️ [WSS-POOL] обрыв {name}: {err[:80] or type(e).__name__}. Реконнект через 12с...")
                await asyncio.sleep(12)

    async def listen(self):
        try:
            import config as _c
            if not getattr(_c, "USE_COPYTRADE", False):
                print("👥 Копитрейдер ВЫКЛ (USE_COPYTRADE=False): чужие пампы не покупаем.")
                return
        except Exception:
            pass

        self.wallets = self.load_wallets()
        all_wallets = list(self.wallets.items())
        if not all_wallets:
            print("⚠️ [COPYTRADER] Нет доступных кошельков для копирования.")
            return

        # 1. Если есть персональный ключ Helius и он не заблокирован по 429, пробуем его
        if getattr(self, "HELIUS_API_KEY", "") and not getattr(self, "_helius_disabled", False):
            print(f"🚀 [COPYTRADER] Проверка Helius WSS на {len(self.wallets)} китов...")
            res = await self._listen_single_socket(self.wss_url, self.wallets)
            if res is False:
                self._helius_disabled = True  # Отключаем Helius на время этой сессии при 429

        # 2. Основной пул: распределяем до 40 топ-китов на проверенный сокет publicnode
        endpoints = [
            "wss://solana-rpc.publicnode.com/",
            "wss://solana-rpc.publicnode.com/",
        ]

        chunk_size = 20
        tasks = []
        for i, ep in enumerate(endpoints):
            chunk = dict(all_wallets[i * chunk_size : (i + 1) * chunk_size])
            if chunk:
                tasks.append(self._listen_single_socket(ep, chunk))

        if not tasks:
            tasks.append(self._listen_single_socket(endpoints[0], dict(all_wallets[:20])))

        print(f"👥 [COPYTRADER] Запущен параллельный пул из {len(tasks)} публичных WSS-сокетов (всего {len(self.wallets)} активных китов)...")
        await asyncio.gather(*tasks, return_exceptions=True)