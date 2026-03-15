# Odds range analysis — which odds buckets are profitable?
sqlite3 data/bot_b_paper.db "
SELECT ROUND(entry_odds,1) AS bucket,
  COUNT(*) AS trades,
  ROUND(AVG(CASE WHEN outcome='win' THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate,
  ROUND(SUM(pnl_usdc),4) AS total_pnl
FROM trades WHERE resolved=1
GROUP BY bucket ORDER BY bucket;"

# Deviation analysis — which lag levels are profitable for Bot A?
sqlite3 data/bot_a_paper.db "
SELECT ROUND(ABS(s.chainlink_dev_pct),1) AS dev_bucket,
  COUNT(*) AS trades,
  ROUND(AVG(CASE WHEN t.outcome='win' THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate,
  ROUND(SUM(t.pnl_usdc),4) AS total_pnl
FROM trades t JOIN signals s ON t.signal_id=s.id
WHERE t.resolved=1
GROUP BY dev_bucket ORDER BY dev_bucket;"

# Confidence analysis — which score ranges are profitable for Bot B?
sqlite3 data/bot_b_paper.db "
SELECT ROUND(ABS(s.confidence_score),1) AS conf_bucket,
  COUNT(*) AS trades,
  ROUND(AVG(CASE WHEN t.outcome='win' THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate,
  ROUND(SUM(t.pnl_usdc),4) AS total_pnl
FROM trades t JOIN signals s ON t.signal_id=s.id
WHERE t.resolved=1
GROUP BY conf_bucket ORDER BY conf_bucket;"