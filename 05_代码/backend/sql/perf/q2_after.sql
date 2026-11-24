-- Q2 优化：idx_user_current(user_id,is_current) 并消除历史版本行放大。
SELECT o.user_id, COUNT(*) AS order_rows
FROM dwd_order_detail o
LEFT JOIN dim_user u ON o.user_id = u.user_id AND u.is_current = 1
WHERE o.dt BETWEEN %(start_date)s AND %(end_date)s
  AND u.level = 3
GROUP BY o.user_id;
