-- Q2 基线：未限制当前版本，SCD2 历史版本会放大关联行。
SELECT o.user_id, COUNT(*) AS order_rows
FROM dwd_order_detail o
LEFT JOIN dim_user u ON o.user_id = u.user_id
WHERE o.dt BETWEEN %(start_date)s AND %(end_date)s
  AND u.level = 3
GROUP BY o.user_id;
