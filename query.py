import os
from supabase import create_client
import json

url = 'https://kpyiiwouhsuaohqtcrfy.supabase.co'
key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtweWlpd291aHN1YW9ocXRjcmZ5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4ODg2NDc3NCwiZXhwIjoyMTA0NDQwNzc0fQ.hDa_vGoycSL_2kOzieEm5rpNffCnslJoolxG6W_J3cE'

sb = create_client(url, key)
res = sb.table('trades_pump').select('features').eq('mint', 'PORTFOLIO_STATE_V3').execute()
features = res.data[0].get('features', '{}')
state = json.loads(features) if isinstance(features, str) else features
positions = state

for mint, p in positions.items():
    if p.get('symbol') in ['XPay', 'MCAT', 'XMoney', 'PDOG']:
        print(f"{p.get('symbol')}: Is Mature={p.get('is_mature')} Entry=${p.get('entry_price_usd')} Mint={mint}")
