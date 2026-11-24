-- Q1 基线：无日期过滤，全表扫描后按渠道汇总。
SELECT channel_id, SUM(amount) AS gmv
FROM dwd_order_detail
WHERE is_refund = 0
GROUP BY channel_id;
