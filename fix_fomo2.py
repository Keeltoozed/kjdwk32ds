import re
with open("fomo_scanner.py", "r") as f:
    code = f.read()

# Fix gather
old_batch = """                results = await asyncio.gather(*[analyze_one(m) for m in batch])
                
                # Увеличенная пауза, чтобы не душить исходящий канал
                await asyncio.sleep(5)"""

new_batch = """                # Обрабатываем ПОСЛЕДОВАТЕЛЬНО, чтобы не убивать сеть Render (NAT limits/Timeouts)
                results = []
                for m in batch:
                    res = await analyze_one(m)
                    results.append(res)
                    await asyncio.sleep(1) # Крошечная пауза между монетами
                
                # Увеличенная пауза между батчами
                await asyncio.sleep(3)"""

code = code.replace(old_batch, new_batch)

with open("fomo_scanner.py", "w") as f:
    f.write(code)
