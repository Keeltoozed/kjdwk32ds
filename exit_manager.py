import asyncio
import time
from typing import Callable
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

# pump_fun_sniper передаёт это значение как "баланс дева неизвестен"
_UNKNOWN_DEV_BALANCE = 1_000_000_000


class ExitManager:
    """
    Интеллектуальный менеджер выхода из позиции (Smart Exit).
    Следит за поведением Dev-кошелька и скоростью транзакций.

    ИСПРАВЛЕНИЯ:
    1) Velocity считался как len(sigs) - last_len, а getSignaturesForAddress(limit=50) отдаёт максимум 50.
       У любого горячего токена (>=50 tx) на втором замере разница = 0 -> "MOMENTUM DEAD" и паник-сейл
       через ~30-45с после входа. Именно ракеты и продавались первыми. Теперь считаем НОВЫЕ подписи
       (множество уже виденных), cap 50 больше не мешает.
    2) Если у дева нет токен-аккаунта (не покупал при создании), стартовый баланс оставался 1_000_000_000,
       текущий = 0 -> мгновенный ложный "DEV DUMP". Теперь без известного баланса проверка дампа пропускается.
    3) RPC-клиент закрывается при остановке мониторинга (раньше утекал по одному на сделку).
    """
    def __init__(self, rpc_url: str):
        self.solana_client = AsyncClient(rpc_url)
        self.active_trades = {}  # token_mint -> trade_info

    async def start_monitoring(self, token_mint: str, dev_wallet: str, initial_dev_balance: int, on_panic_sell: Callable):
        """
        Запускает фоновый asyncio таск для мониторинга активной сделки.
        """
        # Динамически получаем стартовый баланс дева (с учётом 6 decimals).
        # 0 = неизвестен / дев не держит токен -> дамп-проверка пропускается.
        actual_initial_balance = initial_dev_balance
        if initial_dev_balance == _UNKNOWN_DEV_BALANCE:
            actual_initial_balance = 0
            try:
                dev_pubkey = Pubkey.from_string(dev_wallet)
                mint_pubkey = Pubkey.from_string(token_mint)
                response = await self.solana_client.get_token_accounts_by_owner(dev_pubkey, {"mint": mint_pubkey})
                if response.value:
                    account_info = await self.solana_client.get_account_info_json_parsed(response.value[0].pubkey)
                    if account_info.value and hasattr(account_info.value.data, "parsed"):
                        actual_initial_balance = int(account_info.value.data.parsed['info']['tokenAmount']['amount'])
            except Exception as e:
                print(f"⚠️ Не удалось получить стартовый баланс дева: {e}")

        self.active_trades[token_mint] = {
            "dev_wallet": dev_wallet,
            "initial_dev_balance": actual_initial_balance,
            "start_time": time.time(),
            "seen_sigs": set(),
            "last_check_time": time.time()
        }

        print(f"👁️ ExitManager started watching {token_mint}")

        try:
            while token_mint in self.active_trades:
                # 1. Защита от дампа разработчиком
                await self._check_dev_dump(token_mint, on_panic_sell)

                # 2. Микро-трейлинг по velocity (затухание импульса)
                await self._check_velocity_drop(token_mint, on_panic_sell)

                await asyncio.sleep(1.5)  # Быстрый поллинг, но не спамим RPC
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"⚠️ Error in ExitManager for {token_mint}: {e}")
        finally:
            self.active_trades.pop(token_mint, None)
            if not self.active_trades:
                try:
                    await self.solana_client.close()
                except Exception:
                    pass

    async def stop_monitoring(self, token_mint: str):
        if token_mint in self.active_trades:
            del self.active_trades[token_mint]

    async def _check_dev_dump(self, token_mint: str, on_panic_sell: Callable):
        trade = self.active_trades.get(token_mint)
        if not trade:
            return

        # Стартовый баланс дева неизвестен/нулевой — сравнивать не с чем, ложный дамп исключаем
        if int(trade.get("initial_dev_balance", 0) or 0) <= 0:
            return

        dev_pubkey = Pubkey.from_string(trade["dev_wallet"])
        mint_pubkey = Pubkey.from_string(token_mint)

        # Получаем текущий баланс Dev-кошелька
        try:
            response = await self.solana_client.get_token_accounts_by_owner(
                dev_pubkey,
                {"mint": mint_pubkey}
            )

            current_balance = 0
            if response.value:
                account_info = await self.solana_client.get_account_info_json_parsed(response.value[0].pubkey)
                if account_info.value and hasattr(account_info.value.data, "parsed"):
                    current_balance = int(account_info.value.data.parsed['info']['tokenAmount']['amount'])

            # Триггер: если Дев продал больше 10% своего изначального баланса
            if current_balance < (trade["initial_dev_balance"] * 0.9):
                print(f"🚨 DEV DUMP DETECTED ({token_mint})! Dev sold >10% of holdings.")
                print("💥 Initiating Instant Panic Sell!")
                del self.active_trades[token_mint]
                await on_panic_sell(token_mint, "DEV_DUMP")
        except Exception as e:
            print(f"Error checking dev dump: {e}")

    async def _check_velocity_drop(self, token_mint: str, on_panic_sell: Callable):
        trade = self.active_trades.get(token_mint)
        if not trade:
            return

        now = time.time()
        time_diff = now - trade["last_check_time"]
        if time_diff < 15.0:  # Оцениваем скорость раз в 15 секунд — не дёргаем RPC чаще
            return

        try:
            # Последние транзакции токена (макс 50) — считаем только НОВЫЕ подписи с прошлого замера
            sigs = await self.solana_client.get_signatures_for_address(Pubkey.from_string(token_mint), limit=50)
            vals = sigs.value or []
            current = [str(s.signature) for s in vals]
            seen = trade["seen_sigs"]
            new_txs = sum(1 for sg in current if sg not in seen)
            seen.update(current)
            if len(seen) > 1000:
                trade["seen_sigs"] = set(current)

            velocity = new_txs / time_diff  # Транзакции в секунду (Tx/s)

            # Триггер: импульс пропал.
            # Grace period 30 секунд после входа — мемкоины часто "замирают" на старте.
            # Порог 0.2 tx/s — даже 1 транзакция за 5 секунд это нормально для мемкоинов.
            if velocity < 0.2 and (now - trade["start_time"]) > 30.0:
                print(f"📉 MOMENTUM DEAD ({token_mint}). Tx Velocity dropped to {velocity:.2f} tx/s.")
                print("💥 Micro-trailing triggered. Initiating Panic Sell!")
                del self.active_trades[token_mint]
                await on_panic_sell(token_mint, "VELOCITY_DROP")
                return

            trade["last_check_time"] = now
        except Exception as e:
            print(f"Error checking tx velocity: {e}")
            # при сбое RPC не копим time_diff бесконечно (иначе следующий замер будет за 100+ сек)
            trade["last_check_time"] = now
