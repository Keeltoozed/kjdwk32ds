"""Параллельный разбор кандидатов сканера (ускорение scanner_loop).

Было: scanner_loop разбирал токены СТРОГО по одному (await analyze_token в for),
а каждый токен, прошедший дешёвые фильтры, ждёт RugCheck + Helius + GoPlus +
GeckoTerminal-свечи подряд. Пока ждём сеть одного токена, остальные простаивали.

Стало: до `concurrency` токенов анализируются одновременно. Лимиты API не нарушаются:
все запросы идут через http_client.fetch_json, где уже стоят per-host семафоры и
троттлинг (DS=3, GT=1, RugCheck=2, GoPlus=1...). Параллель только перекрывает
ожидание РАЗНЫХ хостов; потолок пропускной способности задают самые узкие лимиты
(RugCheck ~30/мин, GeckoTerminal ~25/мин), а не число воркеров.

Порядок: результаты забираются В ПОРЯДКЕ РАНГА (миграции/импульс первыми).
Опережение ограничено окном = concurrency: пока потребитель занят входом в сделку,
воркеры не уходят далеко вперёд и не жгут лимиты на токены, которые будут отменены.

Состояние: analyze_token пишет в атрибуты анализатора (last_score, _last_top10,
_last_dev). В analyzer.py они сделаны task-local (ContextVar), а _analyze_one
возвращает их значения кортежем из той же задачи; apply_result() восстанавливает их
в контексте потребителя.
"""
import asyncio

MISSING = object()  # токен не был поставлен в окно -> потребитель разбирает его сам


async def _analyze_one(analyzer, mint, sem):
    async with sem:
        ok = await analyzer.analyze_token(mint)
        # Снимок состояния ВНУТРИ задачи (её контекст) — в родителе оно не видно.
        return (
            ok,
            float(getattr(analyzer, "last_score", 0.0) or 0.0),
            float(getattr(analyzer, "_last_top10", 0.0) or 0.0),
            float(getattr(analyzer, "_last_dev", 0.0) or 0.0),
        )


class ScanWindow:
    def __init__(self, analyzer, mints, concurrency=4):
        self.analyzer = analyzer
        self.mints = list(dict.fromkeys(m for m in mints if m))
        self.index = {m: i for i, m in enumerate(self.mints)}
        self.conc = max(1, int(concurrency))
        self.sem = asyncio.Semaphore(self.conc)
        self.tasks = {}
        self._next = 0
        self._fill(self.conc)  # сразу стартуем первое окно

    def _fill(self, limit):
        limit = min(limit, len(self.mints))
        while self._next < limit:
            m = self.mints[self._next]
            self.tasks[m] = asyncio.ensure_future(_analyze_one(self.analyzer, m, self.sem))
            self._next += 1

    def __len__(self):
        return len(self.mints)

    async def get(self, mint):
        """Результат разбора `mint` (кортеж) либо MISSING, если его нет в окне.
        Перед ожиданием двигает окно, чтобы воркеры были заняты следующими токенами."""
        i = self.index.get(mint)
        if i is None:
            return MISSING
        self._fill(i + self.conc + 1)
        return await self.tasks[mint]

    async def close(self):
        """Отменяет недоработавшие задачи и забирает исключения (без 'never retrieved')."""
        for t in self.tasks.values():
            if not t.done():
                t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def apply_result(analyzer, result):
    """Восстанавливает в ТЕКУЩЕМ контексте состояние, снятое внутри задачи.
    Возвращает is_good (True/False/None)."""
    ok, score, top10, dev = result
    analyzer.last_score = score
    analyzer._last_top10 = top10
    analyzer._last_dev = dev
    return ok
