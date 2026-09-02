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
                            real_symbol = pair_data.get("baseToken", {}).get("symbol", symbol)
                            tracker.add_position(real_symbol, mint, entry_price, config.VIRTUAL_POSITION_SIZE_USD)
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
st.title("🚀 PhantBot - Alpha Agent Dashboard")
st.markdown("Панель управления алгоритмическим ботом.")

tab1, tab2 = st.tabs(["📊 Портфель и История", "📡 Радар Рынка (Alpha Scanner)"])

with tab1:
    # Кнопка для ручного обновления страницы
    if st.button("🔄 Обновить портфель"):
        pass
        
    try:
        with open(config.PAPER_PORTFOLIO_FILE, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
            
        if data:
            df = pd.DataFrame.from_dict(data, orient='index')
            open_df = df[df['status'] == 'open']
            closed_df = df[df['status'] == 'closed']
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("🟢 Открытые позиции")
                if not open_df.empty:
                    open_df['pnl_%'] = (open_df['current_pnl_usd'] / open_df['amount_usd']) * 100
                    st.dataframe(open_df[['symbol', 'entry_price_usd', 'current_price_usd', 'max_price_usd', 'current_pnl_usd', 'pnl_%']].style.format({
                        'entry_price_usd': '${:.10f}',
                        'current_price_usd': '${:.10f}',
                        'max_price_usd': '${:.10f}',
                        'current_pnl_usd': '${:.2f}',
                        'pnl_%': '{:.2f}%'
                    }).applymap(lambda x: 'color: green' if x > 0 else 'color: red' if x < 0 else '', subset=['current_pnl_usd', 'pnl_%']))
                else:
                    st.info("Нет активных сделок.")
                    
            with col2:
                st.subheader("📓 История сделок")
                if not closed_df.empty:
                    total_pnl = closed_df['pnl_usd'].sum()
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
        st.info("Бот еще не совершил первую сделку.")

with tab2:
    st.subheader("🔥 Последние проанализированные токены (Alpha Agent)")
    st.markdown("Здесь отображаются монеты, которые бот сканирует прямо сейчас, с расчетом рейтинга в стиле **MemeSniper / GMGNAI**.")
    
    try:
        with open("scanned_tokens.json", 'r') as f:
            scanned_data = json.load(f)
            
        if scanned_data:
            for t in scanned_data:
                score = t.get('score', 0)
                color = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
                
                with st.container():
                    st.markdown(f"""
                    <div style='background-color: #1E1E1E; padding: 15px; border-radius: 10px; border-left: 5px solid {"#00C851" if score >= 70 else "#FF8800"}; margin-bottom: 10px;'>
                        <h3 style='margin:0; color: #FFF;'>{color} {t['symbol']} <span style='font-size: 0.6em; color: #888;'>{t['mint'][:8]}...</span></h3>
                        <div style='display: flex; justify-content: space-between; margin-top: 10px;'>
                            <div style='color: #BBB;'>
                                <div>💧 Liq: <b>${t.get('liquidity', 0):,.0f}</b></div>
                                <div>📊 Vol 24h: <b>${t.get('vol_24h', 0):,.0f}</b></div>
                            </div>
                            <div style='color: #BBB;'>
                                <div>🟩 Buys (5m): <b style='color:#00C851;'>{t.get('buys', 0)}</b></div>
                                <div>🟥 Sells (5m): <b style='color:#FF4444;'>{t.get('sells', 0)}</b></div>
                            </div>
                            <div style='text-align: right;'>
                                <div style='font-size: 0.8em; color: #888;'>Alpha Score</div>
                                <h2 style='margin: 0; color: {"#00C851" if score >= 70 else "#FF8800"};'>{score}/100</h2>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("Бот пока не проанализировал ни одной монеты.")
    except Exception as e:
        st.info("Ожидание данных от сканера...")

# Автообновление (если включено)
if st.checkbox("Включить автообновление (каждые 5 сек)", value=False):
    time.sleep(5)
    st.rerun()

