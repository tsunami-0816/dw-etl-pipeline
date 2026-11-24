-- 07_procedures.sql · 5 个存储过程（D-17～D-24）
-- 调用契约（架构 §5.1）：
--   sp_etl_daily(p_date, OUT p_status, OUT p_rows)
--   sp_load_ods / sp_build_dwd / sp_build_dws / sp_build_ads (p_date, p_batch_id, OUT p_rows)
-- 设计：每层自管事务（START TRANSACTION / COMMIT / ROLLBACK），日志独立落盘；
--       DQ P0 在 dq 记录提交后 SIGNAL，DWD 不写入、ADS 不变（D-36）。

USE dw;

DROP PROCEDURE IF EXISTS sp_load_ods;
DELIMITER $$
CREATE PROCEDURE sp_load_ods(IN p_date DATE, IN p_batch_id VARCHAR(32), OUT p_rows BIGINT)
BEGIN
  DECLARE v_errno INT DEFAULT 0;
  DECLARE v_msg VARCHAR(1000) DEFAULT '';
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_msg = MESSAGE_TEXT;
    ROLLBACK;
    UPDATE etl_run_log
       SET end_at = NOW(3), status = 'failed',
           error_code = CONCAT('E_SQL_', v_errno), error_msg = LEFT(v_msg, 1000)
     WHERE batch_id = p_batch_id AND layer = 'ODS';
    COMMIT;
    RESIGNAL;
  END;

  INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, status)
  VALUES (p_batch_id, p_date, 'ODS', 'sp_load_ods', NOW(3), 'running')
  ON DUPLICATE KEY UPDATE start_at = NOW(3), status = 'running',
      error_code = NULL, error_msg = NULL, end_at = NULL, rows_out = 0;
  COMMIT;

  START TRANSACTION;
  DELETE FROM ods_user    WHERE dt = p_date;
  DELETE FROM ods_order   WHERE dt = p_date;
  DELETE FROM ods_product WHERE dt = p_date;
  DELETE FROM ods_channel WHERE dt = p_date;

  INSERT INTO ods_user(user_id, user_name, phone, city, level, channel_id, register_time, update_time, dt, batch_id)
  SELECT user_id, user_name, phone, city, level, channel_id, register_time, update_time, p_date, p_batch_id
  FROM biz.user_info
  WHERE DATE(register_time) = p_date
     OR DATE(update_time) = p_date
     OR user_id IN (SELECT DISTINCT user_id FROM biz.order_info WHERE DATE(pay_time) = p_date);

  INSERT INTO ods_order(order_id, order_no, user_id, product_id, channel_id, quantity, amount, status, pay_time, refund_time, update_time, dt, batch_id)
  SELECT order_id, order_no, user_id, product_id, channel_id, quantity, amount, status, pay_time, refund_time, update_time, p_date, p_batch_id
  FROM biz.order_info
  WHERE DATE(pay_time) = p_date;

  INSERT INTO ods_product(product_id, product_name, category_id, category_name, price, status, update_time, dt, batch_id)
  SELECT product_id, product_name, category_id, category_name, price, status, update_time, p_date, p_batch_id
  FROM biz.product_info;

  INSERT INTO ods_channel(channel_id, channel_name, channel_type, daily_cost, status, update_time, dt, batch_id)
  SELECT channel_id, channel_name, channel_type, daily_cost, status, update_time, p_date, p_batch_id
  FROM biz.channel_info;

  SET p_rows = (
    SELECT SUM(c) FROM (
      SELECT COUNT(*) c FROM ods_user WHERE dt = p_date
      UNION ALL SELECT COUNT(*) FROM ods_order WHERE dt = p_date
      UNION ALL SELECT COUNT(*) FROM ods_product WHERE dt = p_date
      UNION ALL SELECT COUNT(*) FROM ods_channel WHERE dt = p_date
    ) t
  );
  COMMIT;

  UPDATE etl_run_log SET end_at = NOW(3), status = 'success', rows_out = p_rows
   WHERE batch_id = p_batch_id AND layer = 'ODS';
  COMMIT;
END$$
DELIMITER ;

DROP PROCEDURE IF EXISTS sp_build_dwd;
DELIMITER $$
CREATE PROCEDURE sp_build_dwd(IN p_date DATE, IN p_batch_id VARCHAR(32), OUT p_rows BIGINT)
BEGIN
  DECLARE v_errno INT DEFAULT 0;
  DECLARE v_msg VARCHAR(1000) DEFAULT '';
  DECLARE v_total BIGINT DEFAULT 0;
  DECLARE v_null_ch BIGINT DEFAULT 0;
  DECLARE v_dup BIGINT DEFAULT 0;
  DECLARE v_neg BIGINT DEFAULT 0;
  DECLARE v_orphan BIGINT DEFAULT 0;
  DECLARE v_null_rate DECIMAL(6,4) DEFAULT 0;
  DECLARE v_dup_rate DECIMAL(6,4) DEFAULT 0;
  DECLARE v_done INT DEFAULT 0;
  DECLARE v_rule VARCHAR(16);
  DECLARE v_lvl CHAR(2);
  DECLARE v_tbl VARCHAR(64);
  DECLARE v_pk VARCHAR(128);
  DECLARE v_detail VARCHAR(500);
  DECLARE cur_dq CURSOR FOR
    SELECT rule_code, level, table_name, pk_value, issue_detail FROM tmp_dq_issue;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_msg = MESSAGE_TEXT;
    ROLLBACK;
    UPDATE etl_run_log
       SET end_at = NOW(3), status = 'failed',
           error_code = CONCAT('E_SQL_', v_errno), error_msg = LEFT(v_msg, 1000)
     WHERE batch_id = p_batch_id AND layer = 'DWD';
    COMMIT;
    RESIGNAL;
  END;

  INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, status)
  VALUES (p_batch_id, p_date, 'DWD', 'sp_build_dwd', NOW(3), 'running')
  ON DUPLICATE KEY UPDATE start_at = NOW(3), status = 'running',
      error_code = NULL, error_msg = NULL, end_at = NULL, rows_out = 0;
  COMMIT;

  -- 维表 upsert（先于明细，保证 category_id / 孤儿判断可用）
  START TRANSACTION;
  INSERT INTO dim_product(product_id, product_name, category_id, category_name, price, status)
  SELECT product_id, product_name, category_id, category_name, price, status
  FROM ods_product WHERE dt = p_date
  ON DUPLICATE KEY UPDATE product_name = VALUES(product_name), category_id = VALUES(category_id),
      category_name = VALUES(category_name), price = VALUES(price), status = VALUES(status);

  INSERT INTO dim_channel(channel_id, channel_name, channel_type, daily_cost, status)
  SELECT channel_id, channel_name, channel_type, daily_cost, status
  FROM ods_channel WHERE dt = p_date
  ON DUPLICATE KEY UPDATE channel_name = VALUES(channel_name), channel_type = VALUES(channel_type),
      daily_cost = VALUES(daily_cost), status = VALUES(status);

  -- 用户画像 upsert（每用户一行最新画像）
  INSERT INTO dwd_user_profile(user_id, user_name, city, level, channel_id, register_time, register_date, last_update_time, is_orphan, batch_id)
  SELECT user_id, user_name, city, level, COALESCE(channel_id, 0), register_time, DATE(register_time), update_time, 0, p_batch_id
  FROM ods_user WHERE dt = p_date
  ON DUPLICATE KEY UPDATE user_name = VALUES(user_name), city = VALUES(city), level = VALUES(level),
      channel_id = VALUES(channel_id), register_time = VALUES(register_time), register_date = VALUES(register_date),
      last_update_time = VALUES(last_update_time), is_orphan = 0, batch_id = VALUES(batch_id);
  COMMIT;

  -- dim_user SCD2：关旧版本（等级/城市变化）+ 插新版本/新用户
  START TRANSACTION;
  UPDATE dim_user d
    JOIN dwd_user_profile p ON p.user_id = d.user_id AND d.is_current = 1
    SET d.is_current = 0, d.end_date = DATE_SUB(p_date, INTERVAL 1 DAY)
  WHERE d.level <> p.level OR d.city <> p.city OR d.channel_id <> p.channel_id;

  INSERT INTO dim_user(user_id, user_name, level, city, channel_id, start_date, end_date, is_current, version, batch_id)
  SELECT p.user_id, p.user_name, p.level, p.city, p.channel_id, p_date, '9999-12-31', 1,
         COALESCE((SELECT MAX(version) + 1 FROM dim_user d2 WHERE d2.user_id = p.user_id), 1), p_batch_id
  FROM dwd_user_profile p
  WHERE p.user_id <> -1
    AND NOT EXISTS (SELECT 1 FROM dim_user d WHERE d.user_id = p.user_id AND d.is_current = 1)
  ON DUPLICATE KEY UPDATE user_name = VALUES(user_name), level = VALUES(level), city = VALUES(city),
      channel_id = VALUES(channel_id), is_current = 1, end_date = '9999-12-31';
  COMMIT;

  -- 去重临时表（按 order_no 取最新 update_time / ods_id）
  DROP TEMPORARY TABLE IF EXISTS tmp_ods_dedup;
  CREATE TEMPORARY TABLE tmp_ods_dedup AS
  SELECT *, ROW_NUMBER() OVER (PARTITION BY order_no ORDER BY update_time DESC, ods_id DESC) AS rn
  FROM ods_order WHERE dt = p_date;

  -- 质量计数
  SET v_total   = (SELECT COUNT(*) FROM ods_order WHERE dt = p_date);
  SET v_null_ch = (SELECT COUNT(*) FROM ods_order WHERE dt = p_date AND channel_id IS NULL);
  SET v_dup     = (SELECT COUNT(*) - COUNT(DISTINCT order_no) FROM ods_order WHERE dt = p_date);
  SET v_neg     = (SELECT COUNT(*) FROM tmp_ods_dedup WHERE rn = 1 AND amount < 0);
  SET v_orphan  = (SELECT COUNT(*) FROM tmp_ods_dedup s LEFT JOIN dwd_user_profile p ON p.user_id = s.user_id
                   WHERE s.rn = 1 AND p.user_id IS NULL);
  SET v_null_rate = v_null_ch / NULLIF(v_total, 0);
  SET v_dup_rate  = v_dup / NULLIF(v_total, 0);

  -- 异常明细临时表（游标逐条写 dq_issue）
  DROP TEMPORARY TABLE IF EXISTS tmp_dq_issue;
  CREATE TEMPORARY TABLE tmp_dq_issue (
    rule_code VARCHAR(16), level CHAR(2), table_name VARCHAR(64),
    pk_value VARCHAR(128), issue_detail VARCHAR(500)
  );
  INSERT INTO tmp_dq_issue
  SELECT 'DQ02', IF(v_dup_rate > 0.05, 'P0', 'P1'), 'ods_order',
         CAST(order_no AS CHAR), CONCAT('重复订单，被丢弃 order_id=', order_id)
  FROM tmp_ods_dedup WHERE rn > 1;
  INSERT INTO tmp_dq_issue
  SELECT 'DQ01', IF(v_null_rate > 0.05, 'P0', 'P2'), 'ods_order',
         CAST(order_no AS CHAR), '空渠道已归 0(unknown)'
  FROM tmp_ods_dedup WHERE rn = 1 AND channel_id IS NULL;
  INSERT INTO tmp_dq_issue
  SELECT 'DQ03', 'P1', 'ods_order', CAST(order_no AS CHAR), '负金额已置 0'
  FROM tmp_ods_dedup WHERE rn = 1 AND amount < 0;
  INSERT INTO tmp_dq_issue
  SELECT 'DQ04', 'P1', 'ods_order', CAST(order_no AS CHAR), CONCAT('孤儿用户 ', s.user_id, ' 已置 -1')
  FROM tmp_ods_dedup s LEFT JOIN dwd_user_profile p ON p.user_id = s.user_id
  WHERE s.rn = 1 AND p.user_id IS NULL;

  -- 质量记录独立提交（P0 时保留证据，DWD 不写入）
  START TRANSACTION;
  DELETE FROM dq_issue  WHERE dt = p_date AND rule_code IN ('DQ01', 'DQ02', 'DQ03', 'DQ04');
  DELETE FROM dq_report WHERE dt = p_date AND rule_code IN ('DQ01', 'DQ02', 'DQ03', 'DQ04');

  INSERT INTO dq_report(dt, rule_code, rule_name, level, table_name, total_rows, failed_rows, pass_rate, status, detail, batch_id) VALUES
  (p_date, 'DQ01', '渠道空值率', IF(v_null_rate > 0.05, 'P0', 'P2'), 'ods_order', v_total, v_null_ch,
   COALESCE(1 - v_null_rate, 1), IF(v_null_rate > 0.05, 'blocked', 'warn'),
   CONCAT('空渠道率=', v_null_rate, '，阈值 5%'), p_batch_id),
  (p_date, 'DQ02', '订单号重复率', IF(v_dup_rate > 0.05, 'P0', 'P1'), 'ods_order', v_total, v_dup,
   COALESCE(1 - v_dup_rate, 1), IF(v_dup_rate > 0.05, 'blocked', 'warn'),
   CONCAT('重复率=', v_dup_rate, '，阈值 5%'), p_batch_id),
  (p_date, 'DQ03', '金额为负', 'P1', 'ods_order', v_total, v_neg, COALESCE(1 - v_neg / NULLIF(v_total, 0), 1),
   IF(v_neg > 0, 'warn', 'pass'), '负金额已置 0 并打标', p_batch_id),
  (p_date, 'DQ04', '孤儿用户', 'P1', 'ods_order', v_total, v_orphan, COALESCE(1 - v_orphan / NULLIF(v_total, 0), 1),
   IF(v_orphan > 0, 'warn', 'pass'), '孤儿用户已置 -1', p_batch_id);

  SET v_done = 0;
  OPEN cur_dq;
  dq_loop: LOOP
    FETCH cur_dq INTO v_rule, v_lvl, v_tbl, v_pk, v_detail;
    IF v_done = 1 THEN LEAVE dq_loop; END IF;
    INSERT INTO dq_issue(dt, rule_code, level, table_name, pk_value, issue_detail)
    VALUES (p_date, v_rule, v_lvl, v_tbl, v_pk, v_detail)
    ON DUPLICATE KEY UPDATE issue_detail = VALUES(issue_detail);
  END LOOP;
  CLOSE cur_dq;

  IF v_null_rate > 0.05 THEN
    INSERT INTO dq_alert(dt, rule_code, level, message)
    VALUES (p_date, 'DQ01', 'P0', CONCAT('空渠道率 ', v_null_rate, ' > 5%，阻断下游'))
    ON DUPLICATE KEY UPDATE message = VALUES(message);
  END IF;
  IF v_dup_rate > 0.05 THEN
    INSERT INTO dq_alert(dt, rule_code, level, message)
    VALUES (p_date, 'DQ02', 'P0', CONCAT('订单重复率 ', v_dup_rate, ' > 5%，阻断下游'))
    ON DUPLICATE KEY UPDATE message = VALUES(message);
  END IF;
  COMMIT;

  -- P0 阻断：DWD 不写入，SIGNAL 由 sp_etl_daily 捕获为 blocked
  IF v_null_rate > 0.05 OR v_dup_rate > 0.05 THEN
    SET p_rows = 0;
    UPDATE etl_run_log SET end_at = NOW(3), status = 'blocked', rows_out = 0,
        error_code = 'E_DWD_P0', error_msg = 'DQ 空渠道率或重复率超过 5%'
     WHERE batch_id = p_batch_id AND layer = 'DWD';
    COMMIT;
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'E_DWD_P0';
  END IF;

  -- DWD 明细写入（无 P0 时）
  START TRANSACTION;
  DELETE FROM dwd_order_detail WHERE dt = p_date;
  INSERT INTO dwd_order_detail(dt, order_no, order_id, user_id, product_id, category_id, channel_id,
      quantity, amount, order_status, is_refund, pay_time, refund_time, dq_flag, batch_id)
  SELECT
    p_date,
    s.order_no,
    s.order_id,
    CASE WHEN p.user_id IS NULL THEN -1 ELSE s.user_id END,
    s.product_id,
    COALESCE(dp.category_id, 0),
    COALESCE(s.channel_id, 0),
    COALESCE(s.quantity, 1),
    CASE WHEN s.amount < 0 THEN 0 ELSE COALESCE(s.amount, 0) END,
    COALESCE(s.status, 'paid'),
    CASE WHEN s.status = 'refunded' THEN 1 ELSE 0 END,
    s.pay_time,
    s.refund_time,
    (CASE WHEN s.amount < 0 THEN 1 ELSE 0 END)
      + (CASE WHEN s.channel_id IS NULL THEN 2 ELSE 0 END)
      + (CASE WHEN p.user_id IS NULL THEN 4 ELSE 0 END),
    p_batch_id
  FROM tmp_ods_dedup s
  LEFT JOIN dwd_user_profile p ON p.user_id = s.user_id
  LEFT JOIN dim_product dp ON dp.product_id = s.product_id
  WHERE s.rn = 1;

  -- 孤儿用户画像补登（is_orphan=1），便于 DWS 新增用户统计不把孤儿算入正常新增
  INSERT INTO dwd_user_profile(user_id, user_name, city, level, channel_id, register_time, register_date, is_orphan, batch_id)
  SELECT DISTINCT s.user_id, 'orphan', 'unknown', 0, 0, NULL, NULL, 1, p_batch_id
  FROM tmp_ods_dedup s LEFT JOIN dwd_user_profile p ON p.user_id = s.user_id
  WHERE s.rn = 1 AND p.user_id IS NULL
  ON DUPLICATE KEY UPDATE is_orphan = 1;

  SET p_rows = (SELECT COUNT(*) FROM dwd_order_detail WHERE dt = p_date);
  COMMIT;

  UPDATE etl_run_log SET end_at = NOW(3), status = 'success', rows_out = p_rows
   WHERE batch_id = p_batch_id AND layer = 'DWD';
  COMMIT;
END$$
DELIMITER ;

DROP PROCEDURE IF EXISTS sp_build_dws;
DELIMITER $$
CREATE PROCEDURE sp_build_dws(IN p_date DATE, IN p_batch_id VARCHAR(32), OUT p_rows BIGINT)
BEGIN
  DECLARE v_errno INT DEFAULT 0;
  DECLARE v_msg VARCHAR(1000) DEFAULT '';
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_msg = MESSAGE_TEXT;
    ROLLBACK;
    UPDATE etl_run_log
       SET end_at = NOW(3), status = 'failed',
           error_code = CONCAT('E_SQL_', v_errno), error_msg = LEFT(v_msg, 1000)
     WHERE batch_id = p_batch_id AND layer = 'DWS';
    COMMIT;
    RESIGNAL;
  END;

  INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, status)
  VALUES (p_batch_id, p_date, 'DWS', 'sp_build_dws', NOW(3), 'running')
  ON DUPLICATE KEY UPDATE start_at = NOW(3), status = 'running',
      error_code = NULL, error_msg = NULL, end_at = NULL, rows_out = 0;
  COMMIT;

  START TRANSACTION;
  INSERT INTO dws_daily_channel(dt, channel_id, new_users, orders, paid_orders, gmv, refund_orders, refund_amount, refund_rate, cost, roi, batch_id)
  SELECT
    p_date, d.channel_id,
    COALESCE(u.new_users, 0),
    COUNT(*),
    SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN 1 ELSE 0 END),
    SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN d.amount ELSE 0 END),
    SUM(d.is_refund),
    SUM(CASE WHEN d.is_refund = 1 THEN d.amount ELSE 0 END),
    COALESCE(SUM(d.is_refund) / NULLIF(SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN 1 ELSE 0 END), 0), 0),
    COALESCE(c.daily_cost, 0),
    COALESCE(SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN d.amount ELSE 0 END) / NULLIF(c.daily_cost, 0), 0),
    p_batch_id
  FROM dwd_order_detail d
  LEFT JOIN dim_channel c ON c.channel_id = d.channel_id
  LEFT JOIN (
    SELECT channel_id, COUNT(*) AS new_users FROM dwd_user_profile
    WHERE register_date = p_date AND is_orphan = 0 GROUP BY channel_id
  ) u ON u.channel_id = d.channel_id
  WHERE d.dt = p_date
  GROUP BY d.channel_id, c.daily_cost, u.new_users
  ON DUPLICATE KEY UPDATE
    new_users = VALUES(new_users), orders = VALUES(orders), paid_orders = VALUES(paid_orders),
    gmv = VALUES(gmv), refund_orders = VALUES(refund_orders), refund_amount = VALUES(refund_amount),
    refund_rate = VALUES(refund_rate), cost = VALUES(cost), roi = VALUES(roi), batch_id = VALUES(batch_id);

  INSERT INTO dws_daily_category(dt, category_id, category_name, orders, paid_orders, gmv, refund_orders, refund_amount, refund_rate, batch_id)
  SELECT
    p_date, d.category_id, MAX(dp.category_name),
    COUNT(*),
    SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN 1 ELSE 0 END),
    SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN d.amount ELSE 0 END),
    SUM(d.is_refund),
    SUM(CASE WHEN d.is_refund = 1 THEN d.amount ELSE 0 END),
    COALESCE(SUM(d.is_refund) / NULLIF(SUM(CASE WHEN d.order_status IN ('paid', 'refunded') THEN 1 ELSE 0 END), 0), 0),
    p_batch_id
  FROM dwd_order_detail d
  LEFT JOIN dim_product dp ON dp.product_id = d.product_id
  WHERE d.dt = p_date
  GROUP BY d.category_id
  ON DUPLICATE KEY UPDATE
    category_name = VALUES(category_name), orders = VALUES(orders), paid_orders = VALUES(paid_orders),
    gmv = VALUES(gmv), refund_orders = VALUES(refund_orders), refund_amount = VALUES(refund_amount),
    refund_rate = VALUES(refund_rate), batch_id = VALUES(batch_id);

  SET p_rows = (SELECT COUNT(*) FROM dws_daily_channel WHERE dt = p_date)
             + (SELECT COUNT(*) FROM dws_daily_category WHERE dt = p_date);
  COMMIT;

  UPDATE etl_run_log SET end_at = NOW(3), status = 'success', rows_out = p_rows
   WHERE batch_id = p_batch_id AND layer = 'DWS';
  COMMIT;
END$$
DELIMITER ;

DROP PROCEDURE IF EXISTS sp_build_ads;
DELIMITER $$
CREATE PROCEDURE sp_build_ads(IN p_date DATE, IN p_batch_id VARCHAR(32), OUT p_rows BIGINT)
BEGIN
  DECLARE v_errno INT DEFAULT 0;
  DECLARE v_msg VARCHAR(1000) DEFAULT '';
  DECLARE v_today DECIMAL(16,4) DEFAULT 0;
  DECLARE v_yesterday DECIMAL(16,4) DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_msg = MESSAGE_TEXT;
    ROLLBACK;
    UPDATE etl_run_log
       SET end_at = NOW(3), status = 'failed',
           error_code = CONCAT('E_SQL_', v_errno), error_msg = LEFT(v_msg, 1000)
     WHERE batch_id = p_batch_id AND layer = 'ADS';
    COMMIT;
    RESIGNAL;
  END;

  INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, status)
  VALUES (p_batch_id, p_date, 'ADS', 'sp_build_ads', NOW(3), 'running')
  ON DUPLICATE KEY UPDATE start_at = NOW(3), status = 'running',
      error_code = NULL, error_msg = NULL, end_at = NULL, rows_out = 0;
  COMMIT;

  START TRANSACTION;
  -- 一级指标（NEW_USERS / GMV / REFUND_RATE），先写基础值再回填窗口
  INSERT INTO ads_kpi_daily(dt, kpi_code, kpi_name, kpi_value, kpi_unit, batch_id)
  SELECT p_date, 'NEW_USERS', '新增客户数', COALESCE(SUM(new_users), 0), '人', p_batch_id FROM dws_daily_channel WHERE dt = p_date
  UNION ALL
  SELECT p_date, 'GMV', '成交额', COALESCE(SUM(gmv), 0), '元', p_batch_id FROM dws_daily_channel WHERE dt = p_date
  UNION ALL
  SELECT p_date, 'REFUND_RATE', '退款率', COALESCE(SUM(refund_orders) / NULLIF(SUM(paid_orders), 0), 0), '率', p_batch_id
  FROM dws_daily_channel WHERE dt = p_date
  ON DUPLICATE KEY UPDATE kpi_value = VALUES(kpi_value), kpi_unit = VALUES(kpi_unit), batch_id = VALUES(batch_id);

  -- 环比 mom_rate 与近 7 日均值 ma7（前一日缺失 mom=NULL；不足 7 天用已有天数）
  UPDATE ads_kpi_daily a
  JOIN (
    SELECT dt, kpi_code,
           LAG(kpi_value) OVER w AS prev_val,
           AVG(kpi_value) OVER w AS ma7_val
    FROM ads_kpi_daily
    WINDOW w AS (PARTITION BY kpi_code ORDER BY dt ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
  ) t ON t.dt = a.dt AND t.kpi_code = a.kpi_code
  SET a.mom_rate = (a.kpi_value - t.prev_val) / NULLIF(t.prev_val, 0),
      a.ma7 = t.ma7_val
  WHERE a.dt = p_date;

  -- 渠道 ROI / 渠道经营日报 / 品类销售日报（从 DWS 投影 + 维表取名）
  INSERT INTO ads_channel_roi(dt, channel_id, channel_name, cost, gmv, roi, new_users, orders, cac)
  SELECT d.dt, d.channel_id, COALESCE(c.channel_name, 'unknown'), d.cost, d.gmv, d.roi, d.new_users, d.orders,
         d.cost / NULLIF(d.new_users, 0)
  FROM dws_daily_channel d LEFT JOIN dim_channel c ON c.channel_id = d.channel_id
  WHERE d.dt = p_date
  ON DUPLICATE KEY UPDATE channel_name = VALUES(channel_name), cost = VALUES(cost), gmv = VALUES(gmv),
      roi = VALUES(roi), new_users = VALUES(new_users), orders = VALUES(orders), cac = VALUES(cac);

  INSERT INTO ads_channel_daily(dt, channel_id, channel_name, new_users, orders, paid_orders, gmv, refund_orders, refund_amount, refund_rate, aov)
  SELECT d.dt, d.channel_id, COALESCE(c.channel_name, 'unknown'), d.new_users, d.orders, d.paid_orders, d.gmv,
         d.refund_orders, d.refund_amount, d.refund_rate, d.gmv / NULLIF(d.paid_orders, 0)
  FROM dws_daily_channel d LEFT JOIN dim_channel c ON c.channel_id = d.channel_id
  WHERE d.dt = p_date
  ON DUPLICATE KEY UPDATE channel_name = VALUES(channel_name), new_users = VALUES(new_users), orders = VALUES(orders),
      paid_orders = VALUES(paid_orders), gmv = VALUES(gmv), refund_orders = VALUES(refund_orders),
      refund_amount = VALUES(refund_amount), refund_rate = VALUES(refund_rate), aov = VALUES(aov);

  INSERT INTO ads_category_sales(dt, category_id, category_name, orders, gmv, refund_orders, refund_rate)
  SELECT d.dt, d.category_id, d.category_name, d.orders, d.gmv, d.refund_orders, d.refund_rate
  FROM dws_daily_category d
  WHERE d.dt = p_date
  ON DUPLICATE KEY UPDATE category_name = VALUES(category_name), orders = VALUES(orders), gmv = VALUES(gmv),
      refund_orders = VALUES(refund_orders), refund_rate = VALUES(refund_rate);

  -- DQ05：日环比突增/突降 >50%（P2，不阻断）
  SET v_today = (SELECT kpi_value FROM ads_kpi_daily WHERE dt = p_date AND kpi_code = 'GMV');
  SET v_yesterday = (SELECT kpi_value FROM ads_kpi_daily WHERE dt = DATE_SUB(p_date, INTERVAL 1 DAY) AND kpi_code = 'GMV');
  IF v_yesterday IS NOT NULL AND v_yesterday > 0 AND ABS(v_today - v_yesterday) / v_yesterday > 0.5 THEN
    INSERT INTO dq_report(dt, rule_code, rule_name, level, table_name, total_rows, failed_rows, pass_rate, status, detail, batch_id)
    VALUES (p_date, 'DQ05', '日环比突增突降', 'P2', 'ads_kpi_daily', 1, 1, 0, 'warn',
            CONCAT('GMV 环比波动超 50%：今日=', v_today, ' 昨日=', v_yesterday), p_batch_id)
    ON DUPLICATE KEY UPDATE detail = VALUES(detail), status = 'warn';
    INSERT INTO dq_alert(dt, rule_code, level, message)
    VALUES (p_date, 'DQ05', 'P2', CONCAT('GMV 环比波动超 50%：今日=', v_today, ' 昨日=', v_yesterday))
    ON DUPLICATE KEY UPDATE message = VALUES(message);
  ELSE
    INSERT INTO dq_report(dt, rule_code, rule_name, level, table_name, total_rows, failed_rows, pass_rate, status, detail, batch_id)
    VALUES (p_date, 'DQ05', '日环比突增突降', 'P2', 'ads_kpi_daily', 1, 0, 1, 'pass', 'GMV 环比波动在 50% 以内', p_batch_id)
    ON DUPLICATE KEY UPDATE detail = VALUES(detail), status = 'pass', failed_rows = 0, pass_rate = 1;
  END IF;

  SET p_rows = (SELECT COUNT(*) FROM ads_kpi_daily WHERE dt = p_date)
             + (SELECT COUNT(*) FROM ads_channel_roi WHERE dt = p_date)
             + (SELECT COUNT(*) FROM ads_channel_daily WHERE dt = p_date)
             + (SELECT COUNT(*) FROM ads_category_sales WHERE dt = p_date);
  COMMIT;

  UPDATE etl_run_log SET end_at = NOW(3), status = 'success', rows_out = p_rows
   WHERE batch_id = p_batch_id AND layer = 'ADS';
  COMMIT;
END$$
DELIMITER ;

DROP PROCEDURE IF EXISTS sp_etl_daily;
DELIMITER $$
CREATE PROCEDURE sp_etl_daily(IN p_date DATE, OUT p_status VARCHAR(16), OUT p_rows BIGINT)
BEGIN
  DECLARE v_batch VARCHAR(32) DEFAULT '';
  DECLARE v_rows BIGINT DEFAULT 0;
  DECLARE v_total BIGINT DEFAULT 0;
  DECLARE v_lock INT DEFAULT 0;
  DECLARE v_errno INT DEFAULT 0;
  DECLARE v_msg VARCHAR(1000) DEFAULT '';
  DECLARE v_layer_rows BIGINT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_msg = MESSAGE_TEXT;
    ROLLBACK;
    SET p_status = IF(LOCATE('E_DWD_P0', v_msg) > 0, 'blocked', 'failed');
    INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, end_at, status, error_code, error_msg)
    VALUES (v_batch, p_date, 'ALL', 'sp_etl_daily', NOW(3), NOW(3), p_status,
            IF(p_status = 'blocked', 'E_DWD_P0', CONCAT('E_SQL_', v_errno)), LEFT(v_msg, 1000))
    ON DUPLICATE KEY UPDATE end_at = NOW(3), status = VALUES(status), error_code = VALUES(error_code), error_msg = VALUES(error_msg);
    COMMIT;
    DO RELEASE_LOCK(CONCAT('dw_etl_', p_date));
  END;

  SET p_status = 'failed';
  SET p_rows = 0;

  -- 并发锁：等待 0 秒，占用即失败
  SELECT GET_LOCK(CONCAT('dw_etl_', p_date), 0) INTO v_lock;
  IF v_lock = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'E_LOCK_CONCURRENT';
  END IF;

  -- 幂等预检：当日 ALL 成功且四层目标表均有数据 → skipped
  IF EXISTS (SELECT 1 FROM etl_run_log WHERE dt = p_date AND layer = 'ALL' AND status = 'success')
     AND (SELECT COUNT(*) FROM ods_order WHERE dt = p_date) > 0
     AND (SELECT COUNT(*) FROM dwd_order_detail WHERE dt = p_date) > 0
     AND (SELECT COUNT(*) FROM dws_daily_channel WHERE dt = p_date) > 0
     AND (SELECT COUNT(*) FROM ads_kpi_daily WHERE dt = p_date) > 0 THEN
    SET p_status = 'skipped';
    SET p_rows = (SELECT COALESCE(SUM(rows_out), 0) FROM etl_run_log WHERE dt = p_date AND layer IN ('ODS', 'DWD', 'DWS', 'ADS'));
    DO RELEASE_LOCK(CONCAT('dw_etl_', p_date));
  ELSE
    -- 生成批次号
    SET v_batch = CONCAT('B', DATE_FORMAT(p_date, '%Y%m%d'), '-', DATE_FORMAT(NOW(), '%H%i%s'));

    -- ODS
    IF EXISTS (SELECT 1 FROM etl_run_log WHERE dt = p_date AND layer = 'ODS' AND status = 'success')
       AND (SELECT COUNT(*) FROM ods_order WHERE dt = p_date) > 0 THEN
      SET v_layer_rows = (SELECT rows_out FROM etl_run_log WHERE dt = p_date AND layer = 'ODS');
      SET v_total = v_total + v_layer_rows;
    ELSE
      CALL sp_load_ods(p_date, v_batch, v_layer_rows);
      SET v_total = v_total + v_layer_rows;
    END IF;

    -- DWD（P0 会在此 SIGNAL，被外层 handler 捕获为 blocked）
    IF EXISTS (SELECT 1 FROM etl_run_log WHERE dt = p_date AND layer = 'DWD' AND status = 'success')
       AND (SELECT COUNT(*) FROM dwd_order_detail WHERE dt = p_date) > 0 THEN
      SET v_layer_rows = (SELECT rows_out FROM etl_run_log WHERE dt = p_date AND layer = 'DWD');
      SET v_total = v_total + v_layer_rows;
    ELSE
      CALL sp_build_dwd(p_date, v_batch, v_layer_rows);
      SET v_total = v_total + v_layer_rows;
    END IF;

    -- DWS
    IF EXISTS (SELECT 1 FROM etl_run_log WHERE dt = p_date AND layer = 'DWS' AND status = 'success')
       AND (SELECT COUNT(*) FROM dws_daily_channel WHERE dt = p_date) > 0 THEN
      SET v_layer_rows = (SELECT rows_out FROM etl_run_log WHERE dt = p_date AND layer = 'DWS');
      SET v_total = v_total + v_layer_rows;
    ELSE
      CALL sp_build_dws(p_date, v_batch, v_layer_rows);
      SET v_total = v_total + v_layer_rows;
    END IF;

    -- ADS
    IF EXISTS (SELECT 1 FROM etl_run_log WHERE dt = p_date AND layer = 'ADS' AND status = 'success')
       AND (SELECT COUNT(*) FROM ads_kpi_daily WHERE dt = p_date) > 0 THEN
      SET v_layer_rows = (SELECT rows_out FROM etl_run_log WHERE dt = p_date AND layer = 'ADS');
      SET v_total = v_total + v_layer_rows;
    ELSE
      CALL sp_build_ads(p_date, v_batch, v_layer_rows);
      SET v_total = v_total + v_layer_rows;
    END IF;

    INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, end_at, rows_out, status)
    VALUES (v_batch, p_date, 'ALL', 'sp_etl_daily', NOW(3), NOW(3), v_total, 'success')
    ON DUPLICATE KEY UPDATE end_at = NOW(3), rows_out = v_total, status = 'success',
        error_code = NULL, error_msg = NULL;
    COMMIT;
    SET p_status = 'success';
    SET p_rows = v_total;
    DO RELEASE_LOCK(CONCAT('dw_etl_', p_date));
  END IF;
END$$
DELIMITER ;
