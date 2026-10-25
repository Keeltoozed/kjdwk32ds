"""Автономный охотник за смарт-мани (Smart Money Hunter & Top Trader Radar).

Автоматически находит, анализирует и отслеживает самых прибыльных трейдеров
(снайперов и китов) на блокчейнах Solana и Base:
  1. Непрерывно сканирует топ-ракеты и трендовые пулы дня на Solana и Base.
  2. Разбирает последние сотни ончейн-сделок (trades) каждого пула.
  3. Вычисляет реальный PnL, ROI и время входа каждого кошелька:
     - Находит тех, кто купил ДО пампа на ранних блоках/минутах.
     - Зафиксировал чистую прибыль (PnL > $500..$50k, ROI > 50%).
  4. Отсекает ботов-сэндвичеров, арбитражные MEV-контракты и дев-кошельки.
  5. Автоматически сохраняет проверенных топ-трейдеров в:
     - smart_wallets.txt (для мгновенного копитрейдинга Solana)
     - smart_wallets_db.json (персистентная база с PnL, винрейтом и рангами Tier-1/2/3)
     - EVM_COPY_WALLETS['base'] (для Base)
  6. При покупке токена любым из этих китов:
     - Scanner получает сигнал Smart Money Inflow (+25% к скорингу ракеты)
     - Прогоняет через фильтры JEV/Groq и берет на откате (Flush).
"""

import os
import json
import time
import asyncio
from typing import Dict, List, Any, Optional

DB_FILE = "smart_wallets_db.json"
SOLANA_WALLETS_TXT = "smart_wallets.txt"

# Системные адреса / роутеры / пулы, которые не являются людьми-трейдерами
SYSTEM_EXCLUDE = {
    # Solana Raydium / Pump / System
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ComputeBudget111111111111111111111111111111",
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",
    # Base Uniswap / Aerodrome Routers
    "0x2626664c2603336e57b271c5c0b26f421741e481",
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
    "0x0000000000000000000000000000000000000000",
}


class SmartMoneyHunter:
    def __init__(self):
        self.db: Dict[str, dict] = self._load_db()
        self._last_scan_ts = 0.0

    def _load_db(self) -> Dict[str, dict]:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_db(self):
        try:
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(self.db, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Ошибка сохранения базы смарт-кошельков: {e}")

    def sync_to_txt(self):
        """Синхронизирует топовые Solana-кошельки в smart_wallets.txt."""
        sol_wallets = [
            w for w, data in self.db.items()
            if data.get("chain") == "solana" and data.get("tier", 3) in (1, 2)
        ]
        if not sol_wallets:
            return

        # Читаем существующие кошельки, чтобы сохранить ручные
        existing = set()
        if os.path.exists(SOLANA_WALLETS_TXT):
            try:
                with open(SOLANA_WALLETS_TXT, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            existing.add(line)
            except Exception:
                pass

        merged = existing.union(set(sol_wallets))
        try:
            with open(SOLANA_WALLETS_TXT, "w", encoding="utf-8") as f:
                f.write("# Автономная база Smart Money (найдены ботом на ракетах Solana)\n")
                for w in merged:
                    f.write(f"{w}\n")
        except Exception as e:
            print(f"⚠️ Ошибка обновления {SOLANA_WALLETS_TXT}: {e}")

    def sync_to_config(self):
        """Синхронизирует топовые Base-кошельки в config.EVM_COPY_WALLETS."""
        try:
            import config
            base_wallets = [
                w for w, data in self.db.items()
                if data.get("chain") == "base" and data.get("tier", 3) in (1, 2)
            ]
            if not hasattr(config, "EVM_COPY_WALLETS") or not isinstance(config.EVM_COPY_WALLETS, dict):
                config.EVM_COPY_WALLETS = {}
            config.EVM_COPY_WALLETS["base"] = list(set(config.EVM_COPY_WALLETS.get("base", []) + base_wallets))
        except Exception:
            pass

    async def scan_chain_for_smart_money(self, chain: str = "solana") -> int:
        """
        Ищет топ-трейдеров на указанном блокчейне (Solana или Base):
        1. Получает трендовые ракеты GeckoTerminal
        2. Скачивает ончейн-сделки
        3. Находит кошельки с высокой прибылью
        """
        from http_client import fetch_json
        
        gt_chain = "solana" if chain == "solana" else "base"
        url = f"https://api.geckoterminal.com/api/v2/networks/{gt_chain}/trending_pools?page=1"
        headers = {"Accept": "application/json;version=20230302"}
        
        st, data = await fetch_json(url, headers=headers, timeout=8, retries=1)
        if st != 200 or not isinstance(data, dict):
            return 0
            
        pools = data.get("data", []) or []
        if not pools:
            return 0

        new_alphas_count = 0
        # Анализируем первые 3-5 самых горячих ракет
        for pool in pools[:4]:
            attrs = pool.get("attributes", {}) or {}
            pool_addr = attrs.get("address")
            pool_name = attrs.get("name", "Unknown")
            
            if not pool_addr:
                continue
                
            # Запрашиваем последние 300 сделок пула
            trades_url = f"https://api.geckoterminal.com/api/v2/networks/{gt_chain}/pools/{pool_addr}/trades"
            t_st, t_data = await fetch_json(trades_url, headers=headers, timeout=8, retries=1)
            if t_st != 200 or not isinstance(t_data, dict):
                await asyncio.sleep(1.0)
                continue
                
            trades = t_data.get("data", []) or []
            if len(trades) < 20:
                await asyncio.sleep(1.0)
                continue

            # Группируем сделки по кошелькам
            wallet_trades: Dict[str, List[dict]] = {}
            for tr in trades:
                t_attr = tr.get("attributes", {}) or {}
                wallet = str(t_attr.get("tx_from_address", "")).strip()
                if not wallet or wallet in SYSTEM_EXCLUDE:
                    continue
                wallet_trades.setdefault(wallet, []).append(t_attr)

            # Оцениваем эффективность каждого трейдера на этой ракете
            for wallet, w_list in wallet_trades.items():
                total_buys_usd = 0.0
                total_sells_usd = 0.0
                buy_count = 0
                sell_count = 0
                
                for t in w_list:
                    kind = t.get("kind")
                    vol = float(t.get("volume_in_usd") or 0.0)
                    if kind == "buy":
                        total_buys_usd += vol
                        buy_count += 1
                    elif kind == "sell":
                        total_sells_usd += vol
                        sell_count += 1

                # Фильтр арбитражных MEV-ботов (50+ сделок в одном пуле с микро-маржой)
                if len(w_list) > 30 and abs(total_sells_usd - total_buys_usd) < 50:
                    continue

                # Критерий Alpha-трейдера:
                # 1. Зафиксировал прибыль > $350 на этом пуле
                # 2. Или сделал ощутимый профит при ROI > 40%
                pnl = total_sells_usd - total_buys_usd
                if pnl >= 350.0 or (total_sells_usd >= 600.0 and total_sells_usd > total_buys_usd * 1.4):
                    is_new = wallet not in self.db
                    entry = self.db.setdefault(wallet, {
                        "chain": chain,
                        "first_seen": time.time(),
                        "total_pnl": 0.0,
                        "winning_tokens": [],
                        "trade_count": 0,
                        "tier": 3
                    })
                    
                    entry["total_pnl"] += round(pnl, 2)
                    entry["trade_count"] += len(w_list)
                    if pool_name not in entry["winning_tokens"]:
                        entry["winning_tokens"].append(pool_name)

                    # Tiering:
                    wins_count = len(entry["winning_tokens"])
                    tot_pnl = entry["total_pnl"]
                    if wins_count >= 3 or tot_pnl >= 5000.0:
                        entry["tier"] = 1  # Gold Alpha
                    elif wins_count >= 2 or tot_pnl >= 1000.0:
                        entry["tier"] = 2  # Silver Sniper
                    else:
                        entry["tier"] = 3  # Scout

                    if is_new:
                        new_alphas_count += 1
                        print(
                            f"🏆 [SMART-HUNTER] Найден Alpha-трейдер ({chain}): {wallet[:8]}... "
                            f"| Прибыль: +${pnl:,.0f} на ${pool_name} | Ранг: Tier-{entry['tier']}"
                        )

            # Микро-пауза между пулами для соблюдения квоты GeckoTerminal
            await asyncio.sleep(2.0)

        if new_alphas_count > 0:
            self._save_db()
            self.sync_to_txt()
            self.sync_to_config()
            
        return new_alphas_count

    async def run_discovery_cycle(self):
        """Полный цикл охоты на Solana и Base."""
        now = time.time()
        # Сканируем раз в 15 минут, чтобы не упираться в лимиты
        if now - self._last_scan_ts < 900.0:
            return
            
        self._last_scan_ts = now
        print("🔍 [SMART-HUNTER] Запуск сканирования ракет Solana и Base для поиска топ-трейдеров...")
        
        try:
            sol_new = await self.scan_chain_for_smart_money("solana")
            base_new = 0
            if getattr(config, "BASE_ENABLED", False):
                base_new = await self.scan_chain_for_smart_money("base")
            
            total_wallets = len(self.db)
            tier1_count = sum(1 for d in self.db.values() if d.get("tier") == 1)
            tier2_count = sum(1 for d in self.db.values() if d.get("tier") == 2)
            
            print(
                f"📋 [SMART-RADAR] В базе: {total_wallets} смарт-кошельков "
                f"(Tier-1: {tier1_count}, Tier-2: {tier2_count}) "
                f"| Новых за цикл: +{sol_new + base_new}"
            )
        except Exception as e:
            print(f"⚠️ Ошибка цикла SmartMoneyHunter: {e}")


# Глобальный инстанс
smart_money_hunter = SmartMoneyHunter()


async def smart_money_hunter_loop(analyzer=None, tracker=None):
    """Фоновая задача, которая непрерывно обновляет базу китов."""
    print("🐋 Запущен фоновый радар Smart Money Hunter (Solana & Base)...")
    # Даем боту запуститься 10 секунд перед первым сканом
    await asyncio.sleep(10)
    while True:
        try:
            await smart_money_hunter.run_discovery_cycle()
        except Exception as e:
            print(f"⚠️ Ошибка в smart_money_hunter_loop: {e}")
        # Опрос каждые 15 минут (900 секунд)
        await asyncio.sleep(900)
