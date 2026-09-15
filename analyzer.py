
import aiohttp
from datetime import datetime, timezone
import time
import pandas as pd
try:
    import joblib, numpy as np
    class ScamFilter:
        def __init__(self, path='scam_filter_model.pkl'):
            try:
                self.model = joblib.load(path); self.enabled = True; print('AI Scam Filter: загружен')
            except Exception as e: self.enabled = False; print(f'AI Scam Filter: пропущен ({e})')
        def is_scam(self, data) -> tuple:
            if not self.enabled: return False, 0.0
            try:
                feat = np.array([[data.get('dev_holding_pct', 0), data.get('tx_velocity_1m', 0), data.get('volume_to_liq_ratio', 0), data.get('funded_from_cex', 0)]])
                return bool(self.model.predict(feat)[0]), float(self.model.predict_proba(feat)[0][1])
            except Exception as e: return False, 0.0
    SCAM_FILTER = ScamFilter()
except Exception as e:
    SCAM_FILTER = None; print(f'AI Filter ошибка: {e}')

class Analyzer:
    def __init__(self):
        self.session = None
        self._rug_cache = {}
    
    async def get_session(self):
        import aiohttp
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def fetch_latest_tokens(self):
        return []
    
    async def fetch_token_data(self, mint):
        import market_data
        return await market_data.get_token_data(mint)
    
    def is_clone(self, symbol, current_mint, current_created_at, current_fdv):
        return False
    
    async def analyze_token(self, mint):
        try:
            pair_data = await self.fetch_token_data(mint)
            if not pair_data:
                return False
            
            # Velocity Filter (быстрый)
            volume_24h = (pair_data.get("volume", {}) or {}).get("h24", 0) if pair_data else 0
            _tx = (pair_data.get("txns", {}) or {}).get("h24", {}) if pair_data else {}
            txns_5m = ((_tx.get("buys", 0) or 0) + (_tx.get("sells", 0) or 0)) / 288.0
            if volume_24h < 500 and txns_5m < 10:
                print(f"🚫 [VELOCITY FILTER] {mint}: объём {volume_24h}, транзакций {txns_5m}. Мёртвый пул.")
                return False
            
            # AI Filter (если загружен)
            if SCAM_FILTER and SCAM_FILTER.enabled:
                token_context = {
                    'dev_holding_pct': pair_data.get('dev_holding_pct', 0),
                    'tx_velocity_1m': pair_data.get('tx_velocity_1m', 0),
                    'volume_to_liq_ratio': pair_data.get('volume', {}).get('h24', 0) / max(pair_data.get('liquidity', {}).get('usd', 1), 1),
                    'funded_from_cex': pair_data.get('funded_from_cex', 0)
                }
                is_scam, conf = SCAM_FILTER.is_scam(token_context)
                if is_scam:
                    print(f"🚫 [SCAM FILTER] {mint}: скам (confidence {conf:.0%})")
                    return False
                print(f"🤖 [AI FILTER] {mint}: проходит (scam risk {conf:.0%})")
            
            # Basic analysis (simplified for stability)
            return True
        except Exception as e:
            print(f"Analyze error for {mint}: {e}")
            return False
