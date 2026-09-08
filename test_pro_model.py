import pandas as pd
import numpy as np
import asyncio
from ai_brain import ask_pro_oracle

async def run():
    # create dummy df with 11 features
    df = pd.DataFrame({
        'ofi': [0.1],
        'ofi_ema_5': [0.1],
        'total_vol': [100.0],
        'vol_change': [10.0],
        'vol_acceleration': [5.0],
        'volatility_15m': [0.05],
        'momentum_5m': [0.02],
        'momentum_15m': [0.04],
        'volume_buy': [60.0],
        'volume_sell': [40.0],
        'tx_count': [1]
    })
    res = await ask_pro_oracle(df)
    print("PRO Oracle Result:", res)

asyncio.run(run())
