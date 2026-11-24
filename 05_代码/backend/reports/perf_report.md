# 查询性能实测报告

> 每次执行 EXPLAIN ANALYZE，连续 5 次取中位数；本文件仅由真实运行生成。

| 查询 | 优化前 ms | 优化后 ms | 提升倍数 |
|---|---:|---:|---:|
| Q1 | 54.466 | 68.569 | 0.79 |
| Q2 | 215.355 | 204.925 | 1.05 |
| Q3 | 2.297 | 2.912 | 0.79 |

mysql.slow_log 当前查询结果行数：未查询（账号无权限）。

## Q1 执行计划

### 优化前

```text
-> Table scan on <temporary>  (actual time=113..113 rows=21 loops=1)
    -> Aggregate using temporary table  (actual time=113..113 rows=21 loops=1)
        -> Filter: (dwd_order_detail.is_refund = 0)  (cost=8696 rows=8510) (actual time=2.19..89.7 rows=77047 loops=1)
            -> Covering index scan on dwd_order_detail using idx_dt_channel_amount  (cost=8696 rows=85099) (actual time=2.17..84.1 rows=85572 loops=1)

```

### 优化后

```text
-> Table scan on <temporary>  (actual time=62..62 rows=21 loops=1)
    -> Aggregate using temporary table  (actual time=62..62 rows=21 loops=1)
        -> Filter: ((dwd_order_detail.is_refund = 0) and (dwd_order_detail.dt between '2026-08-26' and '2026-11-23'))  (cost=8545 rows=4255) (actual time=0.0507..42.5 rows=71524 loops=1)
            -> Covering index range scan on dwd_order_detail using idx_dt_channel_amount over ('2026-08-26' <= dt <= '2026-11-23')  (cost=8545 rows=42549) (actual time=0.0215..23 rows=79400 loops=1)

```

## Q2 执行计划

### 优化前

```text
-> Table scan on <temporary>  (actual time=258..258 rows=2047 loops=1)
    -> Aggregate using temporary table  (actual time=258..258 rows=2047 loops=1)
        -> Nested loop inner join  (cost=23433 rows=4255) (actual time=0.534..253 rows=8133 loops=1)
            -> Filter: (o.dt between '2026-08-26' and '2026-11-23')  (cost=8541 rows=42549) (actual time=0.0172..87.4 rows=79400 loops=1)
                -> Covering index range scan on o using idx_dt_user over ('2026-08-26' <= dt <= '2026-11-23')  (cost=8541 rows=42549) (actual time=0.015..67.3 rows=79400 loops=1)
            -> Filter: (u.`level` = 3)  (cost=0.25 rows=0.1) (actual time=0.00194..0.00197 rows=0.102 loops=79400)
                -> Index lookup on u using uk_user_start (user_id=o.user_id)  (cost=0.25 rows=1) (actual time=0.00159..0.00183 rows=1 loops=79400)

```

### 优化后

```text
-> Table scan on <temporary>  (actual time=215..215 rows=2047 loops=1)
    -> Aggregate using temporary table  (actual time=215..215 rows=2047 loops=1)
        -> Nested loop inner join  (cost=23433 rows=2127) (actual time=0.131..210 rows=8133 loops=1)
            -> Filter: (o.dt between '2026-08-26' and '2026-11-23')  (cost=8541 rows=42549) (actual time=0.0205..41 rows=79400 loops=1)
                -> Covering index range scan on o using idx_dt_user over ('2026-08-26' <= dt <= '2026-11-23')  (cost=8541 rows=42549) (actual time=0.018..20.5 rows=79400 loops=1)
            -> Filter: ((u.is_current = 1) and (u.`level` = 3))  (cost=0.25 rows=0.05) (actual time=0.00199..0.00202 rows=0.102 loops=79400)
                -> Index lookup on u using uk_user_start (user_id=o.user_id)  (cost=0.25 rows=1) (actual time=0.00159..0.00185 rows=1 loops=79400)

```

## Q3 执行计划

### 优化前

```text
-> Nested loop left join  (cost=28.6 rows=63) (actual time=0.168..0.267 rows=63 loops=1)
    -> Table scan on a  (cost=6.55 rows=63) (actual time=0.0942..0.103 rows=63 loops=1)
    -> Filter: (b.dt = (a.dt - interval 1 day))  (cost=0.252 rows=1) (actual time=0.00242..0.00247 rows=0.952 loops=63)
        -> Single-row index lookup on b using PRIMARY (dt=(a.dt - interval 1 day), kpi_code=a.kpi_code)  (cost=0.252 rows=1) (actual time=0.00226..0.00227 rows=0.952 loops=63)

```

### 优化后

```text
-> Table scan on daily_window  (cost=2.5..2.5 rows=0) (actual time=0.669..0.676 rows=63 loops=1)
    -> Materialize  (cost=0..0 rows=0) (actual time=0.668..0.668 rows=63 loops=1)
        -> Window aggregate with buffering: lag(ads_kpi_daily.dt) OVER (PARTITION BY ads_kpi_daily.kpi_code ORDER BY ads_kpi_daily.dt )   (actual time=0.388..0.598 rows=63 loops=1)
            -> Table scan on <temporary>  (cost=2.5..2.5 rows=0) (actual time=0.349..0.355 rows=63 loops=1)
                -> Temporary table  (cost=0..0 rows=0) (actual time=0.348..0.348 rows=63 loops=1)
                    -> Window aggregate with buffering: lag(ads_kpi_daily.kpi_value) OVER (PARTITION BY ads_kpi_daily.kpi_code ORDER BY ads_kpi_daily.dt )   (actual time=0.211..0.34 rows=63 loops=1)
                        -> Sort: ads_kpi_daily.kpi_code, ads_kpi_daily.dt  (cost=6.55 rows=63) (actual time=0.143..0.147 rows=63 loops=1)
                            -> Table scan on ads_kpi_daily  (cost=6.55 rows=63) (actual time=0.0546..0.0615 rows=63 loops=1)

```
