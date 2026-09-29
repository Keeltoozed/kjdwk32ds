import time
import threading

class BirthTracker:
    def __init__(self):
        self.tokens = {}  # mint -> timestamp
        self.graduated = {}  # mint -> timestamp миграции (приоритет сканера!)
        self.lock = threading.Lock()
    
    def add_token(self, mint: str):
        with self.lock:
            self.tokens[mint] = time.time()
    
    def get_mature_tokens(self, min_age_minutes: int, max_age_minutes: int):
        now = time.time()
        min_age_seconds = min_age_minutes * 60
        max_age_seconds = max_age_minutes * 60
        
        with self.lock:
            mature = []
            to_remove = []
            for mint, timestamp in self.tokens.items():
                age = now - timestamp
                if age >= min_age_seconds and age <= max_age_seconds:
                    mature.append(mint)
                elif age > max_age_seconds:
                    to_remove.append(mint)
            
            for mint in to_remove:
                del self.tokens[mint]
            
            return mature

    # Лимиты очереди миграций (защита от распухания при панике/раге):
    GRAD_MAX = 200  # больше — выкидываем самые старые (FIFO) с логом
    GRAD_TTL = 900  # 15 мин: DS в пик индексирует и по 15-20 мин, 10 было впритык

    def add_graduated(self, mint: str):
        """Миграция завершена (кёрв 100%): токен — первый в очереди сканера.
        Дедуп встроен (dict). Переполнение — FIFO старых с логом."""
        with self.lock:
            self.graduated[mint] = time.time()
            if len(self.graduated) > self.GRAD_MAX:
                drop = len(self.graduated) - self.GRAD_MAX
                for _m in sorted(self.graduated, key=self.graduated.get)[:drop]:
                    del self.graduated[_m]
                print(f"⚠️ [MIGRATION QUEUE] Переполнение: выкинуто {drop} старых (очередь {self.GRAD_MAX}).")

    def graduated_age(self, mint: str) -> float:
        """Сколько секунд назад зафиксирована миграция (для метрики time_to_index)."""
        with self.lock:
            ts = self.graduated.get(mint)
            return time.time() - ts if ts else -1.0

    def drain_graduated(self, max_age_sec: int = None) -> list:
        """Забрать свежие миграции (дефолт TTL 15 мин) и почистить старые."""
        if max_age_sec is None:
            max_age_sec = self.GRAD_TTL
        now = time.time()
        with self.lock:
            out = [m for m, ts in self.graduated.items() if now - ts <= max_age_sec]
            self.graduated = {m: ts for m, ts in self.graduated.items()
                              if now - ts <= max_age_sec}
            return out

birth_tracker = BirthTracker()