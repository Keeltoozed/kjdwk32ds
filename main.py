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
async def position_manager_loop(analyzer, tracker):
    print("🛡️ Запуск менеджера позиций (быстрый трекинг Stop-Loss)...")
    while True:
        try:
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
        except Exception as e:
            print(f"Ошибка в менеджере позиций: {e}")
        await asyncio.sleep(10) # Проверяем стопы каждые 10 секунд!

async def scanner_loop(analyzer, tracker):
    print("🚀 Запуск PhantBot Scanner (Поиск новых монет)...")
    while True:
        try:
            open_count = len(tracker.get_open_positions())
            if open_count < config.MAX_CONCURRENT_POSITIONS:
                print(f"🔎 Сканируем Dexscreener на устоявшиеся монеты... (Открыто: {open_count}/{config.MAX_CONCURRENT_POSITIONS})")
                tokens = await analyzer.fetch_latest_tokens()
                
                for pair in tokens:
                    mint = pair.get("tokenAddress")
                    symbol = "UNKNOWN"
                    
                    if not mint or mint in tracker.positions:
                        continue
                        
                    is_good = await analyzer.analyze_token(mint)
                    if is_good:
                        pair_data = await analyzer.fetch_token_data(mint)
                        entry_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0
                        actual_symbol = pair_data.get("baseToken", {}).get("symbol", "UNKNOWN") if pair_data else "UNKNOWN"
                        if entry_price > 0:
                            tracker.add_position(actual_symbol, mint, entry_price, config.VIRTUAL_POSITION_SIZE_USD)
                            break # Ждем следующего цикла после покупки
                    
                    # Пауза между монетами
                    await asyncio.sleep(1.5)
        except Exception as e:
            print(f"Ошибка в цикле сканера: {e}")
        await asyncio.sleep(30)

async def async_main():
    analyzer = Analyzer()
    tracker = PaperTracker()
    await asyncio.gather(
        position_manager_loop(analyzer, tracker),
        scanner_loop(analyzer, tracker)
    )

def run_background_bot():
    """Запускает асинхронный цикл в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_main())

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
                    for index, row in open_df.iterrows():
                        pnl_usd = row['current_pnl_usd']
                        pnl_pct = row['pnl_%']
                        color = "#00C851" if pnl_usd >= 0 else "#FF4444"
                        sign = "+" if pnl_usd > 0 else ""
                        
                        st.markdown(f"""
                        <div style='background-color: #1E1E1E; padding: 15px; border-radius: 8px; border-left: 5px solid {color}; margin-bottom: 10px; font-family: sans-serif;'>
                            <div style='display: flex; justify-content: space-between; align-items: center;'>
                                <h3 style='margin:0; color: #FFF;'>{row['symbol']}</h3>
                                <h3 style='margin:0; color: {color};'>{sign}${pnl_usd:.2f} ({sign}{pnl_pct:.2f}%)</h3>
                            </div>
                            <div style='display: flex; justify-content: space-between; margin-top: 10px; font-size: 0.85em; color: #BBB;'>
                                <div><span style='color:#888;'>Вход:</span><br>${row['entry_price_usd']:.8f}</div>
                                <div><span style='color:#888;'>Сейчас:</span><br>${row['current_price_usd']:.8f}</div>
                                <div><span style='color:#888;'>Пик:</span><br>${row['max_price_usd']:.8f}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("Нет активных сделок.")
                    
            with col2:
                st.subheader("📓 История сделок")
                if not closed_df.empty:
                    total_pnl = closed_df['pnl_usd'].sum()
                    st.markdown(f"""
                    <div style='background-color: #2D2D2D; padding: 20px; border-radius: 10px; text-align: center; margin-bottom: 15px;'>
                        <div style='color: #888; font-size: 1.1em; text-transform: uppercase;'>Общий PnL</div>
                        <h1 style='margin: 0; color: {"#00C851" if total_pnl >= 0 else "#FF4444"};'>
                            {"+" if total_pnl >= 0 else ""}${total_pnl:.2f}
                        </h1>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    closed_df = closed_df.tail(15).iloc[::-1] # Показываем 15 последних в обратном порядке
                    closed_df['pnl_%'] = (closed_df['pnl_usd'] / closed_df['amount_usd']) * 100
                    
                    for index, row in closed_df.iterrows():
                        p_usd = row['pnl_usd']
                        p_pct = row['pnl_%']
                        c_color = "#00C851" if p_usd >= 0 else "#FF4444"
                        c_sign = "+" if p_usd > 0 else ""
                        
                        st.markdown(f"""
                        <div style='background-color: #1A1A1A; padding: 10px 15px; border-radius: 6px; border-right: 4px solid {c_color}; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;'>
                            <div>
                                <div style='color: #FFF; font-weight: bold;'>{row['symbol']}</div>
                                <div style='color: #666; font-size: 0.75em;'>{row.get('exit_reason', 'Closed')}</div>
                            </div>
                            <div style='text-align: right; color: {c_color}; font-weight: bold;'>
                                {c_sign}${p_usd:.2f} <br> <span style='font-size: 0.8em;'>({c_sign}{p_pct:.2f}%)</span>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
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
                        <div style='display: flex; justify-content: space-between;'>
                            <h3 style='margin:0; color: #FFF;'>{t['symbol']} <span style='font-size: 0.6em; color: #888;'>{t['mint'][:8]}...pump {t.get('age_mins', 'New')}</span></h3>
                            <h3 style='margin:0; color: {"#00C851" if t.get("m5_change",0) > 0 else "#FF4444"};'>
                                {'+' if t.get("m5_change",0) > 0 else ''}{t.get('m5_change', 0):.1f}%
                            </h3>
                        </div>
                        <div style='display: flex; justify-content: space-between; margin-top: 10px; font-size: 0.9em;'>
                            <div style='color: #888;'>
                                <div style='font-size: 0.7em; text-transform: uppercase;'>Liquidity</div>
                                <div style='color: #DDD;'>${t.get('liquidity', 0):,.0f}</div>
                            </div>
                            <div style='color: #888;'>
                                <div style='font-size: 0.7em; text-transform: uppercase;'>24H Vol</div>
                                <div style='color: #DDD;'>${t.get('vol_24h', 0):,.0f}</div>
                            </div>
                            <div style='color: #888;'>
                                <div style='font-size: 0.7em; text-transform: uppercase;'>Buys/Sells (5m)</div>
                                <div><span style='color:#00C851;'>{t.get('buys', 0)}</span> / <span style='color:#FF4444;'>{t.get('sells', 0)}</span></div>
                            </div>
                        </div>
                        <div style='margin-top: 15px; border-top: 1px solid #333; padding-top: 10px; display: flex; gap: 15px; font-weight: bold; font-size: 0.9em;'>
                            <div style='color: {"#00C851" if score >= 70 else "#FF8800"};'>🧠 {score}</div>
                            <div style='color: {"#00C851" if t.get("safety",0) >= 70 else "#FF8800"};'>🛡️ {t.get('safety', 0)}</div>
                            <div style='color: {"#00C851" if t.get("momentum",0) >= 70 else "#FF8800"};'>⚡ {t.get('momentum', 0)}</div>
                            <div style='color: {"#00C851" if t.get("social",0) >= 70 else "#FF8800"};'>📣 {t.get('social', 0)}</div>
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

