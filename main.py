import asyncio
import threading
import time
import json
import streamlit as st
import pandas as pd
import config
from analyzer import Analyzer
from tracker import PaperTracker

# === 1. ФОНОВЫЙ ТОРГОВЫЙ БОТ ===
async def bot_loop():
    print("Starting Background Trading Loop...")
    analyzer = Analyzer()
    tracker = PaperTracker()

    while True:
        try:
            # Управление открытыми сделками
            open_positions = tracker.get_open_positions()
            for mint, position in list(open_positions.items()):
                pair_data = await analyzer.fetch_token_data(mint)
                current_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0.0
                if current_price == 0.0:
                    continue
                    
                if current_price > position.max_price_usd:
                    position.max_price_usd = current_price
                    
                pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd
                max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd
                minutes_held = (time.time() - position.entry_time) / 60
                
                # Обновляем текущие значения для отображения в интерфейсе
                position.current_price_usd = current_price
                position.current_pnl_usd = position.amount_usd * pnl_pct
                tracker.save_portfolio()
                
                
                # Логика выхода
                if pnl_pct <= config.STOP_LOSS_PCT:
                    tracker.close_position(mint, current_price, "Stop Loss")
                elif minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
                    tracker.close_position(mint, current_price, "Time-based Exit")
                elif max_pnl_pct >= config.TRAILING_ACTIVATION_PCT:
                    drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                    if drop_from_max >= config.TRAILING_DISTANCE_PCT:
                        tracker.close_position(mint, current_price, f"Trailing Stop (-{config.TRAILING_DISTANCE_PCT*100}%)")
            
            # Поиск новых позиций
            if tracker.can_open_new_position(config.MAX_CONCURRENT_POSITIONS):
                latest_tokens = await analyzer.fetch_latest_tokens()
                for token_profile in latest_tokens:
                    if token_profile.get("chainId") != "solana":
                        continue
                        
                    mint = token_profile.get("tokenAddress")
                    symbol = token_profile.get("symbol", "UNKNOWN")
                    
                    if not mint or mint in tracker.positions:
                        continue
                        
                    is_good = await analyzer.analyze_token(mint)
                    if is_good:
                        pair_data = await analyzer.fetch_token_data(mint)
                        entry_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0
                        if entry_price > 0:
                            tracker.add_position(symbol, mint, entry_price, config.VIRTUAL_POSITION_SIZE_USD)
                            break
                            
        except Exception as e:
            print(f"Ошибка в цикле бота: {e}")
            
        await asyncio.sleep(15)

def run_background_bot():
    """Запускает асинхронный цикл в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_loop())

# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===
st.set_page_config(page_title="PhantBot Dashboard", layout="wide")

# Запускаем торгового бота ровно один раз при старте сервера
@st.cache_resource
def start_bot():
    thread = threading.Thread(target=run_background_bot, daemon=True)
    thread.start()
    return thread

bot_thread = start_bot()

# Отрисовка интерфейса
st.title("🚀 PhantBot - Solana Paper Trading")
st.markdown("Панель управления алгоритмическим ботом. Бот торгует в фоновом режиме.")

# Кнопка для ручного обновления страницы
if st.button("🔄 Обновить данные"):
    pass # Streamlit автоматически перезагружает страницу при нажатии

try:
    with open(config.PAPER_PORTFOLIO_FILE, 'r') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            # Файл может быть пуст или перезаписываться фоновым потоком в этот момент
            data = {}
        
    if data:
        df = pd.DataFrame.from_dict(data, orient='index')
        
        # Разделяем на открытые и закрытые сделки
        open_df = df[df['status'] == 'open']
        closed_df = df[df['status'] == 'closed']
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("🟢 Открытые позиции")
            if not open_df.empty:
                # Добавляем % PnL для наглядности
                open_df['pnl_%'] = (open_df['current_pnl_usd'] / open_df['amount_usd']) * 100
                st.dataframe(open_df[['symbol', 'entry_price_usd', 'current_price_usd', 'max_price_usd', 'current_pnl_usd', 'pnl_%']].style.format({
                    'entry_price_usd': '${:.10f}',
                    'current_price_usd': '${:.10f}',
                    'max_price_usd': '${:.10f}',
                    'current_pnl_usd': '${:.2f}',
                    'pnl_%': '{:.2f}%'
                }).applymap(lambda x: 'color: green' if x > 0 else 'color: red' if x < 0 else '', subset=['current_pnl_usd', 'pnl_%']))
            else:
                st.info("Нет активных сделок. Бот сканирует рынок...")
                
        with col2:
            st.subheader("📓 История сделок")
            if not closed_df.empty:
                # Считаем общий PnL
                total_pnl = closed_df['pnl_usd'].sum()
                # Красим в зеленый/красный в зависимости от профита
                color = "normal" if total_pnl >= 0 else "inverse"
                st.metric(label="Общая прибыль (PnL)", value=f"${total_pnl:.2f}", delta=f"{total_pnl:.2f}", delta_color=color)
                
                closed_df['pnl_%'] = (closed_df['pnl_usd'] / closed_df['amount_usd']) * 100
                st.dataframe(closed_df[['symbol', 'entry_price_usd', 'exit_price_usd', 'pnl_usd', 'pnl_%']].style.format({
                    'entry_price_usd': '${:.10f}',
                    'exit_price_usd': '${:.10f}',
                    'pnl_usd': '${:.2f}',
                    'pnl_%': '{:.2f}%'
                }).applymap(lambda x: 'color: green' if x > 0 else 'color: red' if x < 0 else '', subset=['pnl_usd', 'pnl_%']))
            else:
                st.info("История пуста.")
            
    else:
        st.info("Файл портфеля пуст.")
        
except FileNotFoundError:
    st.info("Бот еще не совершил первую сделку (файл портфеля будет создан автоматически).")

# Автообновление (если включено)
if st.checkbox("Включить автообновление (каждые 5 сек)", value=False):
    time.sleep(5)
    st.rerun()

