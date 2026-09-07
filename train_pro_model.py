import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import precision_score
import joblib
import os
import warnings

warnings.filterwarnings('ignore')

class ProQuantTrainer:
    def __init__(self, data_path="local_history.csv", model_path="pro_model.pkl"):
        self.data_path = data_path
        self.model_path = model_path
        
        # Параметры Triple-Barrier Method
        self.tp_pct = 0.50  # Take-Profit +50%
        self.sl_pct = -0.20 # Stop-Loss -20%
        self.time_stop_minutes = 15 # Максимальное время удержания

    def load_data(self) -> pd.DataFrame:
        """
        Собирает локальный датасет из архива (archive/chunk_1.csv),
        создавая 1-минутные свечи (OHLCV).
        """
        if os.path.exists(self.data_path):
            print(f"📥 Загрузка локальных данных из {self.data_path}...")
            df = pd.read_csv(self.data_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
            
        print(f"⚠️ Файл {self.data_path} не найден. Собираю историю из архива (chunk_1.csv)...")
        chunk_file = "archive/chunk_1.csv"
        if not os.path.exists(chunk_file):
            print("❌ Нет файлов в archive/. Генерирую mock-данные (Один токен)...")
            dates = pd.date_range(start='2026-09-01', periods=10000, freq='1min')
            returns = np.random.normal(0, 0.05, len(dates))
            df = pd.DataFrame({
                'base_coin': 'MOCK_TOKEN',
                'timestamp': dates,
                'price': 100 * np.exp(np.cumsum(returns)),
                'volume_buy': np.random.exponential(1000, len(dates)),
                'volume_sell': np.random.exponential(1000, len(dates)),
                'tx_count': np.random.poisson(20, len(dates))
            })
            df.to_csv(self.data_path, index=False)
            return df

        print("⏳ Читаю архив тиковых данных (первые 50 000 сделок для бэктеста)...")
        raw = pd.read_csv(chunk_file, nrows=50000)
        
        # Исключаем битые строки
        raw = raw[raw['base_coin_amount'] > 0]
        
        # 1. Цена: (Quote / 1e9 SOL) / (Base / 1e6 Token)
        raw['price'] = (raw['quote_coin_amount'] / 1e9) / (raw['base_coin_amount'] / 1e6)
        
        raw['timestamp'] = pd.to_datetime(raw['block_time'])
        
        # 3. Объемы сделки
        raw['volume_buy'] = np.where(raw['direction'] == 'buy', raw['quote_coin_amount'] / 1e9, 0)
        raw['volume_sell'] = np.where(raw['direction'] == 'sell', raw['quote_coin_amount'] / 1e9, 0)
        
        # Оставляем только нужные тиковые колонки
        df = raw[['base_coin', 'timestamp', 'price', 'volume_buy', 'volume_sell']].copy()
        
        # Считаем tx_count как 1 тик
        df['tx_count'] = 1
        
        # Оставляем токены, у которых хотя бы 50 тиков (чтобы можно было считать скользящие окна 15)
        counts = df['base_coin'].value_counts()
        valid_coins = counts[counts >= 50].index
        df = df[df['base_coin'].isin(valid_coins)]
        
        df = df.sort_values(['timestamp']).reset_index(drop=True)
        print(f"💾 Сохраняю собранную тиковую историю в {self.data_path} (Строк: {len(df)})...")
        df.to_csv(self.data_path, index=False)
        return df

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Micro-structure Feature Engineering (Квантовые признаки).
        Группировка строго по токену (base_coin), чтобы не смешивать цены разных монет!
        """
        print("🧬 Генерация квантовых признаков...")
        df = df.copy()
        
        total_vol = df['volume_buy'] + df['volume_sell']
        df['ofi'] = np.where(total_vol > 0, (df['volume_buy'] - df['volume_sell']) / total_vol, 0)
        df['ofi_ema_5'] = df.groupby('base_coin')['ofi'].transform(lambda x: x.ewm(span=5, adjust=False).mean())
        
        df['total_vol'] = total_vol
        df['vol_change'] = df.groupby('base_coin')['total_vol'].diff()
        df['vol_acceleration'] = df.groupby('base_coin')['vol_change'].diff()
        
        df['log_return'] = df.groupby('base_coin')['price'].transform(lambda x: np.log(x / x.shift(1)))
        df['volatility_15m'] = df.groupby('base_coin')['log_return'].transform(lambda x: x.rolling(window=15).std() * np.sqrt(15))
        
        df['momentum_5m'] = df.groupby('base_coin')['price'].transform(lambda x: x.pct_change(5))
        df['momentum_15m'] = df.groupby('base_coin')['price'].transform(lambda x: x.pct_change(15))
        
        df = df.dropna().reset_index(drop=True)
        return df

    def apply_triple_barrier(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Квантовая разметка цели (Triple-Barrier Method) изолированно для каждого токена.
        """
        print("🎯 Разметка по методу Тройного Барьера (Triple-Barrier)...")
        
        def barrier_logic(coin_df):
            targets = np.zeros(len(coin_df))
            prices = coin_df['price'].values
            n = len(coin_df)
            for i in range(n):
                entry_price = prices[i]
                upper = entry_price * (1 + self.tp_pct)
                lower = entry_price * (1 + self.sl_pct)
                end_idx = min(i + self.time_stop_minutes, n)
                
                future_prices = prices[i+1:end_idx]
                hit_tp, hit_sl = False, False
                for p in future_prices:
                    if p >= upper:
                        hit_tp = True
                        break
                    elif p <= lower:
                        hit_sl = True
                        break
                
                if hit_tp and not hit_sl:
                    targets[i] = 1
                else:
                    targets[i] = 0
            coin_df['target'] = targets
            return coin_df
            
        # Применяем барьер для каждого токена отдельно
        df = df.groupby('base_coin', group_keys=False).apply(barrier_logic)
        print(f"📊 Распределение таргетов: {df['target'].value_counts(normalize=True).to_dict()}")
        return df

    def train_and_backtest(self, df: pd.DataFrame):
        """Обучение и бэктест."""
        df = df.sort_values('timestamp').reset_index(drop=True)
        features = [col for col in df.columns if col not in ['timestamp', 'target', 'price', 'log_return', 'base_coin']]
        X = df[features]
        y = df['target']
        
        print(f"\n🧠 Начинаю Purged Time-Series обучение...")
        tscv = TimeSeriesSplit(n_splits=5, gap=self.time_stop_minutes)
        
        models, test_indices, preds_all, y_true_all = [], [], [], []
        
        for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
            X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
            X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]
            
            # Если в трейне нет позитивов (бывает на мелких кусках), пропускаем фолд
            if sum(y_train == 1) == 0:
                continue
                
            ratio = len(y_train[y_train == 0]) / max(len(y_train[y_train == 1]), 1)
            conservative_weight = ratio * 0.15 # Жесткий штраф за ложный вход
            
            model = xgb.XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.05,
                scale_pos_weight=conservative_weight, random_state=42, n_jobs=-1
            )
            
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            
            models.append(model)
            preds_all.extend(preds)
            y_true_all.extend(y_test)
            
            prec = precision_score(y_test, preds, zero_division=0)
            print(f"Fold {fold+1}: Precision = {prec:.4f}")

        if not models:
            print("❌ Недостаточно прибыльных примеров для обучения.")
            return

        print("\n💾 Обучение финальной Production модели...")
        final_ratio = len(y[y == 0]) / max(len(y[y == 1]), 1)
        final_model = xgb.XGBClassifier(
            n_estimators=150, max_depth=5, learning_rate=0.05, 
            scale_pos_weight=final_ratio * 0.15, random_state=42, n_jobs=-1
        )
        final_model.fit(X, y)
        joblib.dump(final_model, self.model_path)
        print(f"✅ Модель сохранена в {self.model_path}")
        
        self.generate_backtest_report(y_true_all, preds_all, final_model, features)

    def generate_backtest_report(self, y_true, y_pred, model, feature_names):
        y_true = np.array(y_true)
        y_pred = np.array(y_pred)
        
        trades_taken = np.sum(y_pred == 1)
        winning_trades = np.sum((y_pred == 1) & (y_true == 1))
        losing_trades = np.sum((y_pred == 1) & (y_true == 0))
        
        win_rate = winning_trades / max(trades_taken, 1)
        avg_loss = abs(self.sl_pct)
        ev_per_trade = (win_rate * self.tp_pct) - ((1 - win_rate) * avg_loss)
        
        equity = 1.0
        equity_curve = [equity]
        
        for t_true, t_pred in zip(y_true, y_pred):
            if t_pred == 1:
                if t_true == 1: equity *= (1 + self.tp_pct)
                else: equity *= (1 - avg_loss)
                equity_curve.append(equity)
                
        peak = 1.0
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak: peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd: max_dd = dd

        print("\n" + "="*50)
        print("📈 ПРОФЕССИОНАЛЬНЫЙ БЭКТЕСТ-ОТЧЕТ (TEST SET)")
        print("="*50)
        print(f"Всего сигналов на вход (Trades): {trades_taken}")
        print(f"Прибыльных сделок (Wins):        {winning_trades}")
        print(f"Убыточных сделок (Losses):       {losing_trades}")
        print(f"Win Rate (Точность входа):       {win_rate*100:.2f}%")
        print(f"Expected Value (EV/Trade):       {ev_per_trade*100:.2f}%")
        print(f"Max Drawdown (Просадка):         {max_dd*100:.2f}%")
        print(f"Итоговый PnL (без учета комиссий): {(equity_curve[-1] - 1)*100:.2f}%")
        print("="*50)
        
        print("\n🌟 Feature Importance (ТОП-5 факторов для ИИ):")
        importances = model.feature_importances_
        feature_importance = pd.DataFrame({'Feature': feature_names, 'Importance': importances})
        feature_importance = feature_importance.sort_values(by='Importance', ascending=False).head(5)
        for _, row in feature_importance.iterrows():
            print(f"- {row['Feature']:<20}: {row['Importance']:.4f}")

if __name__ == "__main__":
    trainer = ProQuantTrainer(data_path="local_history.csv", model_path="pro_model.pkl")
    df_raw = trainer.load_data()
    df_features = trainer.engineer_features(df_raw)
    df_labeled = trainer.apply_triple_barrier(df_features)
    trainer.train_and_backtest(df_labeled)
