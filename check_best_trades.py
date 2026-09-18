import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

response = supabase.table("trades_pump").select("*").execute()
trades = response.data

# Сортируем по PNL
best_pnl = sorted([t for t in trades if t.get('pnl') is not None], key=lambda x: x['pnl'], reverse=True)
print("=== ТОР-3 по закрытому PNL ===")
for t in best_pnl[:3]:
    print(f"Mint: {t['mint']}, PNL: {t['pnl']}%, Reason: {t.get('exit_reason')}")

print("\n=== ТОП-3 Упущенные ракеты (Вышли рано, а оно полетело) ===")
# missed_pnl или post_exit_ath
rockets = sorted([t for t in trades if t.get('missed_pnl') is not None], key=lambda x: x['missed_pnl'], reverse=True)
for t in rockets[:3]:
    print(f"Mint: {t['mint']}, Наш PNL: {t['pnl']}%, Могло бы быть: +{t['missed_pnl']}%, Reason: {t.get('exit_reason')}")
