-- Q3 优化：单次窗口扫描；只有前一行恰为前一自然日时才计算环比。
SELECT dt, kpi_code, kpi_value,
       CASE WHEN previous_dt = DATE_SUB(dt, INTERVAL 1 DAY)
            THEN (kpi_value - previous_value) / NULLIF(previous_value, 0)
            ELSE NULL
       END AS mom_rate
FROM (
  SELECT dt, kpi_code, kpi_value,
         LAG(kpi_value) OVER (PARTITION BY kpi_code ORDER BY dt) AS previous_value,
         LAG(dt) OVER (PARTITION BY kpi_code ORDER BY dt) AS previous_dt
  FROM ads_kpi_daily
) AS daily_window;
