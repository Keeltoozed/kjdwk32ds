import asyncio
from ai_brain import ask_ai_oracle

async def main():
    mock_token = {
        "symbol": "DOGE",
        "age_minutes": 15.5,
        "liquidity_usd": 15000,
        "volume_24h_usd": 50000,
        "m5_buys": 45,
        "m5_sells": 10,
        "price_change_m5_pct": 5.2,
        "price_change_h1_pct": 20.0,
        "social_networks_count": 2,
        "rsi_14": 45.0,
        "macd_histogram": 0.0001,
        "safety_score": 80
    }
    print("Отправляю тестовый запрос к ИИ...")
    result = await ask_ai_oracle(mock_token)
    print("Результат:", result)

if __name__ == "__main__":
    asyncio.run(main())
