-- Q3 基线：自连接计算日环比。
SELECT a.dt, a.kpi_code, a.kpi_value,
       (a.kpi_value - b.kpi_value) / NULLIF(b.kpi_value, 0) AS mom_rate
FROM ads_kpi_daily a
LEFT JOIN ads_kpi_daily b ON b.kpi_code = a.kpi_code AND b.dt = DATE_SUB(a.dt, INTERVAL 1 DAY);
