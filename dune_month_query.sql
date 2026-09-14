-- Месяц минутных цен pump.fun токенов для бектеста.
-- Как запускать: https://dune.com → New Query → выбрать движок (Dune SQL),
-- вставить запрос, Run, дождаться, Export → CSV → сохранить файл как
-- dune_month.csv в папку бота. Бектест:  python3 backtest_live_logic.py dune_month.csv
--
-- Логика: все сделки, где одна из сторон — токен на ...pump (все pump.fun
-- минты заканчиваются на 'pump'), за последние 30 дней. Цена минуты =
-- суммарный USD-объём / суммарное кол-во токенов (VWAP минуты).

WITH t AS (
    SELECT
        block_time,
        CASE
            WHEN token_bought_mint LIKE '%pump' THEN token_bought_mint
            ELSE token_sold_mint
        END AS mint,
        CASE
            WHEN token_bought_mint LIKE '%pump' THEN token_bought_amount
            ELSE token_sold_amount
        END AS tok_amt,
        amount_usd
    FROM dex.trades
    WHERE blockchain = 'solana'
      AND block_time >= now() - INTERVAL '30' DAY
      AND (
          token_bought_mint LIKE '%pump'
          OR token_sold_mint LIKE '%pump'
      )
      AND amount_usd > 0
)
SELECT
    date_trunc('minute', block_time) AS minute,
    mint,
    SUM(amount_usd) / NULLIF(SUM(tok_amt), 0) AS price_usd
FROM t
GROUP BY 1, 2
ORDER BY 2, 1;
