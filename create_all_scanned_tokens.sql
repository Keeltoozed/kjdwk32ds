-- ==============================================================================
-- SQL ДЛЯ SUPABASE: Создание таблицы all_scanned_tokens
-- Выполните этот запрос в панели Supabase -> SQL Editor -> New Query -> Run
-- ==============================================================================

CREATE TABLE IF NOT EXISTS all_scanned_tokens (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    chain TEXT DEFAULT 'solana',
    scanned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    rejected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    decision TEXT DEFAULT 'SKIP',
    reason TEXT,
    score NUMERIC DEFAULT 0.0,
    rkt_score NUMERIC,
    m5_pct NUMERIC DEFAULT 0.0,
    price_at_scan NUMERIC DEFAULT 0.0,
    price_at_rejection NUMERIC DEFAULT 0.0,
    liquidity NUMERIC DEFAULT 0.0,
    fdv NUMERIC DEFAULT 0.0,
    source TEXT,
    ath_price NUMERIC DEFAULT 0.0,
    hypothetical_pnl NUMERIC DEFAULT 0.0,
    is_missed_rocket BOOLEAN DEFAULT FALSE,
    check_5m_done INTEGER DEFAULT 0,
    check_15m_done INTEGER DEFAULT 0,
    check_1h_done INTEGER DEFAULT 0,
    check_4h_done INTEGER DEFAULT 0,
    check_24h_done INTEGER DEFAULT 0,
    time_to_ath_min NUMERIC DEFAULT 0.0,
    current_pnl NUMERIC DEFAULT 0.0,
    outcome TEXT DEFAULT 'PENDING',
    features JSONB DEFAULT '{}'::jsonb,
    last_checked_at TIMESTAMP WITH TIME ZONE
);

-- Индексы для мгновенной сортировки и фильтрации
CREATE INDEX IF NOT EXISTS idx_all_scanned_scanned_at ON all_scanned_tokens (scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_all_scanned_decision ON all_scanned_tokens (decision);
CREATE INDEX IF NOT EXISTS idx_all_scanned_rocket ON all_scanned_tokens (is_missed_rocket);
CREATE INDEX IF NOT EXISTS idx_all_scanned_source ON all_scanned_tokens (source);

