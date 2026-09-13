import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

# Add the sync function
sync_func = '''
    async def sync_fomo_positions(self, ws):
        """Подхватывает позиции, открытые FOMO-сканером, чтобы трекать их цены в реальном времени через вебсокет"""
        import asyncio
        from tracker import PaperTracker
        p_tracker = self.tracker if self.tracker else PaperTracker()
        
        while self.running:
            try:
                open_positions = p_tracker.get_open_positions()
                for mint, pos in open_positions.items():
                    if mint not in self.trackers:
                        print(f"🔗 [WSS SYNC] Подключаем лайв-трекинг для {pos.symbol} (куплен сканером)")
                        state = TokenTrackerState(mint, pos.symbol, "")
                        state.is_entered = True
                        self.trackers[mint] = state
                        await ws.send(import_json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
            except Exception as e:
                print(f"Ошибка синхронизации WSS: {e}")
            await asyncio.sleep(3)

    async def garbage_collector(self, ws):'''

# Fix import json missing in sync_func by just importing it
sync_func = sync_func.replace("import_json", "json")

content = content.replace("    async def garbage_collector(self, ws):", sync_func)

# Add to connect_and_listen
connect_add = '''                    # 2. Запуск сборщика мусора и Shadow Watcher
                    asyncio.create_task(self.garbage_collector(ws))
                    asyncio.create_task(self.sync_fomo_positions(ws))'''

content = content.replace("                    # 2. Запуск сборщика мусора и Shadow Watcher\n                    asyncio.create_task(self.garbage_collector(ws))", connect_add)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
