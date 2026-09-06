import re

with open('analyzer.py', 'r') as f:
    content = f.read()

# 1. We will completely replace `analyze_token` (at line 161) with the smart router.
# Let's find the empty analyze_token and replace it.
content = re.sub(
    r'    async def analyze_token\(self, mint: str\) -> bool:\n.*?vol_24h = pair_data\.get\("volume", \{\}\)\.get\("h24", 0\)\n',
    r'''    async def analyze_token(self, mint: str) -> bool:
        # Smart Router
        pair_data = await self.fetch_token_data(mint)
        if not pair_data:
            return False
            
        dex_id = pair_data.get("dexId")
        import time
        created_at = pair_data.get("pairCreatedAt", 0)
        age_minutes = (time.time() * 1000 - created_at) / (1000 * 60) if created_at else 999
        
        if dex_id == "pump" and age_minutes <= 15:
            return await self.analyze_token_xgboost(mint)
        else:
            return await self.analyze_token_raydium(mint)
''', content, flags=re.DOTALL)

# 2. Let's delete the dead code at the end of `analyze_token_raydium`.
# The dead code starts after `except Exception as e:\n            return False\n`
# and goes all the way to `async def analyze_token_ws`
content = re.sub(
    r'        except Exception as e:\n            return False\n\n        # 0\. Заранее парсим транзакции.*?(?=    async def analyze_token_ws)',
    r'        except Exception as e:\n            return False\n\n',
    content, flags=re.DOTALL
)

with open('analyzer.py', 'w') as f:
    f.write(content)
