import re
with open("fomo_scanner.py", "r") as f:
    code = f.read()

# Fix batch size and sleep
old_batch = """            # Анализируем батчами по 5 параллельно
            for i in range(0, len(new_mints), 5):
                if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    break
                    
                batch = new_mints[i:i+5]
                print(f"🔍 FOMO: анализируем батч из {len(batch)} токенов...")
                
                async def analyze_one(mint):
                    try:
                        return mint, await analyzer.analyze_token(mint)
                    except Exception as e:
                        print(f"⚠️ Ошибка анализа {mint[:8]}...: {e}")
                        return mint, False
                
                results = await asyncio.gather(*[analyze_one(m) for m in batch])
                
                # Пауза между батчами
                await asyncio.sleep(2)"""

new_batch = """            # Снижаем нагрузку на сеть (Render NAT rate limits)
            for i in range(0, len(new_mints), 3):
                if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    break
                    
                batch = new_mints[i:i+3]
                print(f"🔍 FOMO: анализируем батч из {len(batch)} токенов...")
                
                async def analyze_one(mint):
                    try:
                        return mint, await analyzer.analyze_token(mint)
                    except Exception as e:
                        print(f"⚠️ Ошибка анализа {mint[:8]}...: {e}")
                        return mint, False
                
                results = await asyncio.gather(*[analyze_one(m) for m in batch])
                
                # Увеличенная пауза, чтобы не душить исходящий канал
                await asyncio.sleep(5)"""

code = code.replace(old_batch, new_batch)

with open("fomo_scanner.py", "w") as f:
    f.write(code)
