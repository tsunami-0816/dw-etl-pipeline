-- Q1 优化：限定日期区间，命中 (dt,channel_id,is_refund,amount) 覆盖索引。
SELECT channel_id, SUM(amount) AS gmv
FROM dwd_order_detail
WHERE dt BETWEEN %(start_date)s AND %(end_date)s
  AND is_refund = 0
GROUP BY channel_id;
