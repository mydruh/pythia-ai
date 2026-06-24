#!/usr/bin/env bash
# Быстрый отчёт по проду Pythia. Читает базу через psql в Docker (локальный psql не нужен).
#
# Использование (URL НЕ хардкодить и НЕ коммитить — это секрет):
#   PROD_DB_URL="postgresql://...railway" bash scripts/prod_report.sh
# или:
#   bash scripts/prod_report.sh "postgresql://...railway"
set -euo pipefail

DB="${1:-${PROD_DB_URL:-}}"
if [[ -z "$DB" ]]; then
  echo "Задай PROD_DB_URL (env) или передай URL первым аргументом." >&2
  exit 1
fi

q() { docker run --rm postgres:16-alpine psql "$DB" "$@"; }

echo "=== СЕССИИ (боты) ==="
q -c "SELECT id, name, status, starting_balance AS start, round(balance::numeric,2) AS free,
  to_char(sim_end,'MM-DD HH24:MI') AS until, to_char(now() AT TIME ZONE 'UTC','MM-DD HH24:MI') AS now_utc
  FROM trading_sessions ORDER BY id;"

echo "=== ПОЗИЦИИ ==="
q -c "SELECT p.id, p.session_id AS s, p.status, p.side, p.size_usdc AS size, p.entry_price AS entry,
  p.current_price AS cur, round(p.pnl::numeric,2) AS pnl, LEFT(m.question,40) AS q, m.status AS mkt
  FROM positions p JOIN markets m ON m.id=p.market_id ORDER BY p.id;"

echo "=== P&L по сессиям (реализованный / экспозиция) ==="
q -c "SELECT session_id AS s,
  round(sum(pnl) FILTER (WHERE status='closed')::numeric,2) AS realized,
  round(sum(size_usdc) FILTER (WHERE status='open')::numeric,2) AS open_exposure,
  count(*) FILTER (WHERE status='open') AS open_n,
  count(*) FILTER (WHERE status='closed') AS closed_n
  FROM positions GROUP BY session_id ORDER BY session_id;"

echo "=== АНАЛИЗЫ: модель × вердикт ==="
q -c "SELECT model, verdict, count(*) FROM analyses GROUP BY model, verdict ORDER BY model, verdict;"

echo "=== ТОЧНОСТЬ (Brier модель vs рынок), только по разрешённым ==="
q -c "SELECT a.model, count(*) AS n,
  round(avg((a.my_prob - CASE WHEN mt.outcome=r.winning_outcome THEN 1 ELSE 0 END)^2)::numeric,4) AS brier_model,
  round(avg((a.market_prob - CASE WHEN mt.outcome=r.winning_outcome THEN 1 ELSE 0 END)^2)::numeric,4) AS brier_market
  FROM analyses a
  JOIN resolutions r ON r.market_id=a.market_id
  JOIN market_tokens mt ON mt.token_id=a.token_id
  GROUP BY a.model ORDER BY a.model;"
