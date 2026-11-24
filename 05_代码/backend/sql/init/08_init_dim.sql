-- 08_init_dim.sql · 初始化日期维 + 特殊哨兵维记录（D-08 / D-09）
-- dim_date 覆盖 2026-01-01～2027-12-31；weekday 1=周一 … 7=周日。
-- 重跑幂等：ON DUPLICATE KEY UPDATE，区间外日期需先扩展本脚本再跑。

USE dw;

SET SESSION cte_max_recursion_depth = 2000;

-- 渠道 unknown 哨兵（空渠道归 0）
INSERT INTO dim_channel(channel_id, channel_name, channel_type, daily_cost, status)
VALUES (0, 'unknown', 'unknown', 0.00, 1)
ON DUPLICATE KEY UPDATE channel_name = 'unknown', channel_type = 'unknown', daily_cost = 0.00, status = 1;

-- 用户 unknown 哨兵（孤儿用户归 -1）
INSERT INTO dim_user(user_id, user_name, level, city, channel_id, start_date, end_date, is_current, version)
VALUES (-1, 'unknown', 0, 'unknown', 0, '2026-01-01', '9999-12-31', 1, 1)
ON DUPLICATE KEY UPDATE user_name = 'unknown', level = 0, city = 'unknown', channel_id = 0, is_current = 1;

-- 日期维：递归 CTE 生成 730 天
INSERT INTO dim_date(dt, year, quarter, month, day, week, weekday, is_weekend)
WITH RECURSIVE dates(d) AS (
  SELECT DATE('2026-01-01')
  UNION ALL
  SELECT d + INTERVAL 1 DAY FROM dates WHERE d < DATE('2027-12-31')
)
SELECT
  d,
  YEAR(d),
  QUARTER(d),
  MONTH(d),
  DAY(d),
  WEEK(d),
  WEEKDAY(d) + 1,
  IF(WEEKDAY(d) >= 5, 1, 0)
FROM dates
ON DUPLICATE KEY UPDATE year = VALUES(year), quarter = VALUES(quarter), month = VALUES(month),
    day = VALUES(day), week = VALUES(week), weekday = VALUES(weekday), is_weekend = VALUES(is_weekend);
