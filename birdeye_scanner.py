"""Birdeye-дискавери мультичейн: /defi/token_trending с ротацией сетей.

Эндпоинт поддерживает x-chain: solana, robinhood, base, bsc (проверено по докам).
Ключ: бесплатно на bds.birdeye.so (Standard 30K CU/мес, token_trending = 50 CU).

CU-бюджет: ротация по 1 запросу за тик, интервал BIRDEYE_INTERVAL (30 мин) —
48 запросов/сутки. Free-хватит частично; на Lite ($39, 1.5M) — с запасом.
Без BIRDEYE_API_KEY петля молча спит.

EVM-минты идут в analyze_robinhood_token (не в Solana-анализатор!),
Solana — в analyze_token.
"""
import asyncio
import time

import config
from http_client import fetch_json

URL = ("https://public-api.birdeye.so/defi/token_trending"
       "?sort_by=rank&sort_type=asc&offset=0&limit=20")


async def fetch_birdeye_trending(chain: str) -> list:
    """[(address, chain), ...] трендов сети. Пусто без ключа или при 429."""
    key = getattr(config, "BIRDEYE_API_KEY", "") or ""
    if not key:
        return []
    status, data = await fetch_json(
        URL, headers={"X-API-KEY": key, "x-chain": chain},
        timeout=12, retries=1)
    if status in (400, 401, 403):
        print(f"⚠️ [BIRDEYE] API вернул {status} (недействительный ключ) — отключаем опрос Birdeye.")
        setattr(config, "BIRDEYE_API_KEY", "")
        return []
    if status != 200 or not isinstance(data, dict):
        return []
    out = []
    for item in ((data.get("data") or {}).get("tokens") or []):
        a = item.get("address", "")
        if a and a not in [x[0] for x in out]:
            out.append((a, chain))
    return out


async def birdeye_loop(analyzer, tracker):
    """Петля Birdeye: ротация сетей по 1 запросу за тик (бережём CU)."""
    if not (getattr(config, "BIRDEYE_API_KEY", "") or ""):
        return
    chains = list(getattr(config, "BIRDEYE_CHAINS",
                          ["solana"]))
    print(f"🦅 Birdeye Scanner запущен: ротация {chains}!")
    processed = {}
    tick = 0
    interval = int(getattr(config, "BIRDEYE_INTERVAL", 1800))
    while True:
        try:
            chain = chains[tick % len(chains)]
            tick += 1
            if chain != "solana":
                _en_flag = bool(getattr(config, f"{chain.upper()}_ENABLED", True))
                if not _en_flag:
                    continue
            if len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                for addr, ch in await fetch_birdeye_trending(chain):
                    if not addr or addr in tracker.positions:
                        continue
                    if ch == "solana" and (str(addr).startswith("0x") or len(str(addr).strip()) < 32):
                        continue
                    if time.time() - processed.get(addr, 0.0) < 3600:
                        continue
                    try:
                        if ch == "solana":
                            ok = await analyzer.analyze_token(addr)
                            td = await analyzer.fetch_token_data(addr) if ok else None
                        else:
                            import evm_data
                            ok = await analyzer.analyze_robinhood_token(addr, ch)
                            td = await evm_data.get_token_data(addr, ch) if ok else None
                    except Exception as e:
                        print(f"🦅 Birdeye analyze err {addr[:8]}: {type(e).__name__} ({e})")
                        continue
                    if ok is None:
                        continue
                    processed[addr] = time.time()
                    analyzer.log_scan(addr[:8], addr, "BIRDEYE", ok,
                                      getattr(analyzer, "last_score", 0.0))
                    if ok is True and len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                        price = float((td.get("priceUsd") or 0)) if td else 0
                        if price > 0:
                            sym = ((td.get("baseToken") or {}).get("symbol", addr[:8])
                                   if td else addr[:8]) or addr[:8]
                            _sig = analyzer.get_signal(addr)
                            _veto = analyzer.rocket_veto(addr, td or {}, _sig)
                            if _veto:
                                print(f"🚫 [BIRDEYE-VETO] {sym} [{ch}]: {_veto}")
                                continue
                            if ch == "solana":
                                if getattr(config, "RUG_WALLET_AUDIT_ENABLED", True):
                                    try:
                                        from rug_wallet_analysis import quick_rug_check
                                        _rw_ok_b, _rw_why_b = await quick_rug_check(addr)
                                        _why_b_l = str(_rw_why_b or "").lower()
                                        if not _rw_ok_b and not any(k in _why_b_l for k in ("429", "403", "timeout", "rpc", "http")):
                                            print(f"🚫 [BIRDEYE-RUG-WALLET] {sym} ({addr[:8]}): {_rw_why_b}")
                                            continue
                                    except Exception as _be_rw_e:
                                        print(f"⚠️ [BIRDEYE-RUG-WALLET] {sym}: {type(_be_rw_e).__name__}")
                                if getattr(config, "FCR_ENABLED", True):
                                    try:
                                        from first_candle_rug_guard import pre_entry_rug_check
                                        _fcr_b = await pre_entry_rug_check(addr, pair_data=td or {}, symbol=sym)
                                        if _fcr_b.get("verdict") == "BLOCK":
                                            print(f"🚫 [BIRDEYE-FCR] {sym} ({addr[:8]}): {_fcr_b.get('report', '')}")
                                            continue
                                    except Exception:
                                        pass
                                try:
                                    from jupiter import JupiterAPI
                                    _sell_b_ok, _sell_b_why = await JupiterAPI.is_token_sellable(addr)
                                    if not _sell_b_ok:
                                        print(f"🚫 [BIRDEYE-JUP] {sym} ({addr[:8]}): непродаваем ({_sell_b_why})")
                                        continue
                                    _jup_px_b = await JupiterAPI.get_price(addr)
                                    if _jup_px_b and _jup_px_b > 0 and price > 0:
                                        if _jup_px_b < price * 0.925 or max(_jup_px_b, price) / min(_jup_px_b, price) > 1.15:
                                            print(f"🚫 [BIRDEYE-PRICE] {sym} ({addr[:8]}): Jupiter ${_jup_px_b:.8f} vs DS ${price:.8f} — отмена!")
                                            continue
                                        price = _jup_px_b
                                except Exception:
                                    pass
                            if str((td or {}).get("dexId", "")).lower() == "pumpswap":
                                try:
                                    import goplus as _gp_be
                                    _ps_b_ok, _ps_b_canon, _ps_b_why = await _gp_be.verify_pumpswap_pool_onchain(
                                        addr,
                                        pair_address=str((td or {}).get("pairAddress", "") or ""),
                                        pair_data=td or {},
                                    )
                                    if not _ps_b_ok or config.is_pumpswap_liq_fdv_trap(td):
                                        print(f"🚫 [BIRDEYE-PUMPSWAP] {sym} ({addr[:8]}): {_ps_b_why}")
                                        continue
                                except Exception:
                                    pass
                            if getattr(config, "DYNAMIC_REINVEST_ENABLED", True):
                                size = tracker.calculate_dynamic_position_size(td or {}, chain=ch)
                                if size <= 0:
                                    continue
                            else:
                                fixed = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                                if fixed > 0:
                                    size = fixed
                                else:
                                    cap = tracker.get_total_capital()
                                    size = max(4.0, min(100.0, cap * (config.REINVEST_PERCENT / 100.0))) if cap > 0 else 4.0
                            if tracker.add_position(
                                sym, addr, price, min(size, 100.0), is_mature=True,
                                ml_features=analyzer.pack_features(td or {}),
                                ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                source=f"BIRDEYE:{_sig}",
                                pair_address=((td or {}).get("pairAddress", "") or ""),
                                pair_data=td,
                                **({"chain": ch} if ch != "solana" else {})):
                                print(f"🦅 BIRDEYE BUY {sym} [{ch}] @ ${price}")
                            break
                    await asyncio.sleep(0.3)
                if len(processed) > 1000:
                    processed.clear()
        except Exception as e:
            print(f"Ошибка в birdeye_loop: {e}")
        await asyncio.sleep(interval)
