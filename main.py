import asyncio
import threading
import time
import json
import streamlit as st
import pandas as pd
import config
from analyzer import Analyzer
from tracker import PaperTracker
from fomo_scanner import fomo_loop

# === 1. ФОНОВЫЙ ТОРГОВЫЙ БОТ ===
async def position_manager_loop(analyzer, tracker):
    from jupiter import JupiterAPI
    print("🛡️ Запуск менеджера позиций (быстрый трекинг Stop-Loss)...")
    while True:
        try:
            open_positions = tracker.get_open_positions()
            
            # ОПТИМИЗАЦИЯ СКОРОСТИ: Запрашиваем цены для ВСЕХ позиций ОДНИМ запросом
            mints_to_fetch = list(open_positions.keys())
            bulk_prices = await JupiterAPI.get_prices(mints_to_fetch) if mints_to_fetch else {}
            
            for mint, position in list(open_positions.items()):
                # 1. Берем цену из Raydium/Gecko (запросили разом для всех)
                api_price = bulk_prices.get(mint, 0.0)
                
                # 2. Берем цену из WebSocket (если она свежая)
                # В tracker.py current_price_usd изначально равна entry_price. Нам нужно понять, обновилась ли она.
                # Если она изменилась с момента покупки, значит websocket ее обновил!
                ws_price = position.current_price_usd if hasattr(position, 'current_price_usd') else 0.0
                
                # ИСПОЛЬЗУЕМ СВЕЖУЮ ЦЕНУ:
                # Если websocket поменял цену (она не равна ровно цене входа), то верим websocket!
                # Иначе, если Raydium/Gecko вернули цену > 0, верим им.
                if ws_price > 0.0 and abs(ws_price - position.entry_price_usd) > 0.00000001:
                    current_price = ws_price
                elif api_price > 0.0:
                    current_price = api_price
                else:
                    # Если никто не вернул цену (Гецко еще не знает, ВСС еще не прислал сделку), оставляем ту, что была
                    current_price = ws_price
                    
                if current_price <= 0.0:
                    minutes_held = (time.time() - position.entry_time) / 60
                    if minutes_held > 180:
                        tracker.close_position(mint, 0.0, "Rug Pull / No Liquidity")
                    continue
                    
                if current_price > position.max_price_usd:
                    position.max_price_usd = current_price
                    
                pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd
                max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd
                minutes_held = (time.time() - position.entry_time) / 60
                
                # Обновляем текущие значения для отображения в интерфейсе
                # (сохраняем один раз за цикл ниже, а не на каждой позиции,
                # чтобы не делать N записей в Supabase каждые 3 секунды)
                position.current_price_usd = current_price
                position.current_pnl_usd = position.amount_usd * pnl_pct
                
                # === ИНТЕГРАЦИЯ МАТЕМАТИКИ ДЛЯ ЗРЕЛЫХ МОНЕТ (SWING TRADING) ===
                if getattr(position, "is_mature", False):
                    from exit_managers import MatureExitManager
                    mature_exit_reason = MatureExitManager.evaluate_exit(position, current_price)
                    if mature_exit_reason:
                        tracker.close_position(mint, current_price, mature_exit_reason)
                    continue # Если это mature монета, скальперская логика ниже к ней не применяется!
                
                # === ЖЕСТКИЙ RISK MANAGEMENT (CRO LEVEL) ===
                
                # Умный расчет минимального порога для покрытия комиссий
                priority_fee_usd = 0.075 if position.amount_usd < 10.0 else 0.45
                min_fee_pct = (priority_fee_usd + 0.02 * position.amount_usd) / position.amount_usd
                
                # 🚀 УМНЫЙ ТЕЙК-ПРОФИТ (Снижаем жадность, забираем кэш)
                # 1. Первая фиксация на +35%: продаем 50% объема
                if max_pnl_pct >= 0.35 and getattr(position, "is_moonbag", False) == False:
                    tracker.partial_close_position(mint, current_price, 0.5, "Take Profit 50% (+35%)")
                    continue
                
                # 2. ТРЕЙЛИНГ-СТОП (Динамическая фиксация)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                
                if getattr(position, "is_moonbag", False):
                    # Если уже забрали 50%, даем оставшейся части дышать шире (ждем ракету)
                    if drop_from_max >= 0.20: 
                        tracker.close_position(mint, current_price, "Moonbag Trailing (20% drop)")
                        continue
                else:
                    # Активируем трейлинг из config.py
                    if max_pnl_pct >= config.TRAILING_ACTIVATION_PCT:
                        if drop_from_max >= config.TRAILING_DISTANCE_PCT:
                            tracker.close_position(mint, current_price, f"Smart Trailing (+{max_pnl_pct*100:.0f}% peak)")
                            continue
                            
                # 3. ЖЕСТКИЙ Stop Loss из config.py
                if pnl_pct <= config.STOP_LOSS_PCT:
                    tracker.close_position(mint, current_price, f"Hard Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)")
                    continue
                    
                # 4. Time Exit из config.py: если монета застыла
                if minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
                    tracker.close_position(mint, current_price, "Time-based Exit (Dead Coin)")
                    continue
            # Сохраняем обновлённые цены одним разом за цикл (файл + Supabase)
            tracker.save_portfolio()
        except Exception as e:
            print(f"Ошибка в менеджере позиций: {e}")
        # GeckoTerminal разрешает максимум 30 запросов в минуту. 
        # Если делать sleep(0.5), будет 120 запросов, что вызовет жесткий бан и ослепит бота!
        # Ставим интервал 3 секунды (20 запросов в минуту) - это максимально быстро и безопасно.
        await asyncio.sleep(3.0)

from birth_tracker import BirthTracker
birth_tracker = BirthTracker()

async def birth_wss_loop(analyzer, tracker):
    import websockets
    import json
    print("👶 Запуск Роддома: сбор базы данных новых токенов для сканера (без авто-покупки на 0-секунде)...")
    uri = config.PUMPPORTAL_WSS
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Origin": "https://pumpportal.fun"
    }
    while True:
        try:
            async with websockets.connect(uri, extra_headers=headers) as ws:
                payload = {"method": "subscribeNewToken"}
                await ws.send(json.dumps(payload))
                async for message in ws:
                    data = json.loads(message)
                    mint = data.get("mint")
                    if mint:
                        # Просто сохраняем монету в базу для scanner_loop (чтобы она "настоялась" 5-20 мин)
                        birth_tracker.add_token(mint)
        except Exception as e:
            print(f"Ошибка WSS Роддома: {e}. Переподключение через 5 секунд...")
            await asyncio.sleep(5)

async def scanner_loop(analyzer, tracker):
    print("🚀 Запуск PhantBot Scanner (Поиск новых монет)...")
    processed_mints = set()
    while True:
        try:
            open_count = len(tracker.get_open_positions())
            if open_count < config.MAX_CONCURRENT_POSITIONS:
                print(f"🔎 Сканируем монеты... (Открыто: {open_count}/{config.MAX_CONCURRENT_POSITIONS})")
                
                # 1. VIP Токены (DexScreener API)
                tokens = await analyzer.fetch_latest_tokens()
                mints_to_scan = [p.get("tokenAddress") for p in tokens if p.get("tokenAddress")]
                
                # 2. Уличные Токены (Берем молодые ракеты от 5 до 20 минут)
                mature_mints = birth_tracker.get_mature_tokens(5, 20)
                if mature_mints:
                    print(f"🎂 Найдено {len(mature_mints)} перспективных монет (возраст 5-20 минут)!")
                    mints_to_scan.extend(mature_mints)
                
                # Удаляем дубликаты
                mints_to_scan = list(set(mints_to_scan))
                
                for mint in mints_to_scan:
                    if not mint or mint in tracker.positions or mint in processed_mints:
                        continue
                        
                    processed_mints.add(mint)
                    
                    # Держим память в чистоте
                    if len(processed_mints) > 1000:
                        processed_mints.clear()
                        
                    # Используем умный маршрутизатор (сам выберет XGBoost или Raydium модель)
                    is_good = await analyzer.analyze_token(mint)
                            
                    if is_good:
                        pair_data = await analyzer.fetch_token_data(mint)
                        entry_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0
                        actual_symbol = pair_data.get("baseToken", {}).get("symbol", "UNKNOWN") if pair_data else "UNKNOWN"
                        if entry_price > 0:
                            # Динамический сайзинг
                            capital = tracker.get_total_capital()
                            base_position = capital * (config.REINVEST_PERCENT / 100.0)
                            
                            liq_usd = pair_data.get("liquidity", {}).get("usd", 0) if pair_data else 0
                            max_allowed_by_pool = liq_usd * 0.01  # Максимум 1% от ликвидности
                            
                            position_size = max(4.0, min(base_position, max_allowed_by_pool, 100.0))
                            
                            if position_size < 4.0:
                                print(f"🚫 Отказ (Ликвидность): Недостаточно ликвидности (${liq_usd}) для безопасного входа.")
                                continue
                                
                            tracker.add_position(actual_symbol, mint, entry_price, position_size, is_mature=True)
                            break # Ждем следующего цикла после покупки
                    
                    # Пауза между монетами
                    await asyncio.sleep(1.5)
        except Exception as e:
            print(f"Ошибка в цикле сканера: {e}")
        await asyncio.sleep(30)

from copytrader import CopyTrader
import os

async def fomo_signal_loop(analyzer, tracker):
    print("📲 Запуск обработчика сигналов FOMO...")
    while True:
        try:
            if os.path.exists('fomo_signals.txt'):
                with open('fomo_signals.txt', 'r') as f:
                    mints = f.read().splitlines()
                
                if mints:
                    # Очищаем файл после прочтения
                    with open('fomo_signals.txt', 'w') as f:
                        f.write('')
                        
                    for mint in mints:
                        mint = mint.strip()
                        if mint and mint not in tracker.positions:
                            print(f"🚨 ПРИНЯТ ВНЕШНИЙ СИГНАЛ (FOMO): {mint}")
                            # Проверяем скам-фильтрами перед покупкой
                            is_good = await analyzer.analyze_token(mint)
                            
                            if is_good:
                                pair_data = await analyzer.fetch_token_data(mint)
                                entry_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0
                                actual_symbol = pair_data.get("baseToken", {}).get("symbol", "FOMO") if pair_data else "FOMO"
                                
                                if entry_price > 0:
                                    capital = tracker.get_total_capital()
                                    position_size = max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0)))
                                    tracker.add_position(actual_symbol, mint, entry_price, position_size)
        except Exception as e:
            print(f"Ошибка в fomo_signal_loop: {e}")
        await asyncio.sleep(1) # Проверяем файл каждую секунду для мгновенной реакции

async def rugpull_feeder_loop():
    print("🧹 Запуск автоматического сборщика скам-рагпулов (раз в 6 часов)...")
    # Ждем 10 секунд перед первым запуском, чтобы не грузить систему на старте
    await asyncio.sleep(10)
    while True:
        try:
            import rugpull_feeder
            rugpull_feeder.feed_rugs_and_retrain()
        except Exception as e:
            print(f"Ошибка в rugpull_feeder: {e}")
        await asyncio.sleep(6 * 60 * 60)  # Спим 6 часов

async def async_main():
    from pump_fun_sniper import PumpFunSniper
    
    # Keep-Alive задача, чтобы Render не засыпал (работает в фоне)
    async def keep_alive():
        import aiohttp, os
        port = int(os.environ.get("PORT", 10000))
        url = os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}")
        print(f"🔄 Keep-Alive URL: {url}")
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(600)  # Каждые 10 минут
                try:
                    async with session.get(url) as resp:
                        print(f"💓 Keep-Alive Ping: {resp.status}")
                except Exception as e:
                    pass
    asyncio.create_task(keep_alive())

    from trade_logger import trade_logger
    from birdeye_scanner import birdeye_loop
    from sol_price import get_sol_price
    
    # Получаем актуальную цену SOL при старте
    await get_sol_price()
    
    analyzer = Analyzer()
    tracker = PaperTracker()
    copy_trader = CopyTrader(tracker, analyzer)
    sniper = PumpFunSniper(tracker)
    
    async def sol_price_updater():
        """Обновляет цену SOL каждые 5 минут"""
        while True:
            await get_sol_price()
            await asyncio.sleep(300)
    
    await asyncio.gather(
        position_manager_loop(analyzer, tracker),
        scanner_loop(analyzer, tracker),
        copy_trader.listen(),
        fomo_signal_loop(analyzer, tracker),
        fomo_loop(analyzer, tracker),
        sniper.connect_and_listen(),  # ENABLED — с AI фильтром — sniper entry kills capital (-85.8%), mature +162.5%
        trade_logger.post_trade_watcher_loop(),
        rugpull_feeder_loop(),
        sol_price_updater()
    )

def run_background_bot():
    """Запускает асинхронный цикл в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_main())

import os
# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===


st.set_page_config(page_title="PhantBot Dashboard", layout="wide")

# Запускаем торгового бота ровно один раз при старте сервера
@st.cache_resource
def start_bot():
    thread = threading.Thread(target=run_background_bot, daemon=True)
    thread.start()
    return thread

bot_thread = start_bot()

def load_dashboard_portfolio():
    """Портфель для дашборда: сначала Supabase (переживает рестарты Render),
    потом локальный файл. Возвращает dict (возможно пустой)."""
    # 1. Supabase — главный источник правды
    try:
        url = getattr(config, 'SUPABASE_URL', None)
        key = getattr(config, 'SUPABASE_KEY', None)
        if url and key:
            from supabase import create_client
            sb = create_client(url, key)
            res = sb.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE_V3").execute()
            if res.data and res.data[0].get("features"):
                data = json.loads(res.data[0]["features"])
                if data:
                    return data
    except Exception as e:
        print(f"⚠️ Дашборд: не удалось прочитать портфель из Supabase: {e}")
    # 2. Fallback: локальный файл
    try:
        with open(config.PAPER_PORTFOLIO_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        print(f"⚠️ Дашборд: не удалось прочитать локальный портфель: {e}")
        return {}

# Отрисовка интерфейса
st.title("🚀 PhantBot - Alpha Agent Dashboard")
st.markdown("Панель управления алгоритмическим ботом.")

tab1, tab2 = st.tabs(["📊 Портфель и История", "📡 Радар Рынка (Alpha Scanner)"])

with tab1:
    # Кнопка для ручного обновления страницы
    if st.button("🔄 Обновить портфель"):
        pass
        
    try:
        data = load_dashboard_portfolio()
            
        if data:
            df = pd.DataFrame.from_dict(data, orient='index')
            open_df = df[df['status'] == 'open'].copy()
            closed_df = df[df['status'] == 'closed'].copy()
            
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
                                <h3 style='margin:0; color: #FFF;'>{row['symbol'] if str(row['symbol']).strip() else row['mint'][:6] + '...'}</h3>
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
                                <div style='color: #FFF; font-weight: bold;'>{row['symbol'] if str(row['symbol']).strip() else row.name[:6] + '...'}</div>
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

