-- 04_dw_dwd_dim.sql · DWD 明细事实表 + 4 张维表（D-04 / D-05 / D-06 / D-07 / D-08）
-- DWD / DWS 按月 RANGE COLUMNS(dt) 分区 + pmax；分区键 dt 必须出现在所有唯一键中（MySQL 8 要求）。

USE dw;

CREATE TABLE IF NOT EXISTS dwd_order_detail (
  order_detail_id BIGINT      NOT NULL AUTO_INCREMENT,
  dt              DATE        NOT NULL,
  order_no        VARCHAR(32) NOT NULL,
  order_id        BIGINT      NOT NULL,
  user_id         BIGINT      NOT NULL COMMENT '孤儿用户统一置 -1',
  product_id      BIGINT      NOT NULL,
  category_id     INT         NOT NULL DEFAULT 0,
  channel_id      INT         NOT NULL DEFAULT 0 COMMENT '空渠道统一置 0(unknown)',
  quantity        INT         NOT NULL DEFAULT 1,
  amount          DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '负值已置 0',
  order_status    VARCHAR(16) NOT NULL DEFAULT 'paid' COMMENT 'paid/refunded/cancelled',
  is_refund       TINYINT     NOT NULL DEFAULT 0,
  pay_time        DATETIME    NULL,
  refund_time     DATETIME    NULL,
  dq_flag         INT         NOT NULL DEFAULT 0 COMMENT '位标记:1 负金额 2 空渠道 4 孤儿用户 8 重复去重',
  batch_id        VARCHAR(32) NULL,
  etl_time        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (order_detail_id, dt),
  UNIQUE KEY uk_dt_order_no (dt, order_no),
  KEY idx_dt_channel (dt, channel_id),
  KEY idx_dt_user (dt, user_id),
  KEY idx_dt_category (dt, category_id),
  KEY idx_dt_channel_amount (dt, channel_id, is_refund, amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细事实表，一行=一个订单号在一个业务日期的去重明细'
PARTITION BY RANGE COLUMNS(dt) (
  PARTITION p202606 VALUES LESS THAN ('2026-07-01'),
  PARTITION p202607 VALUES LESS THAN ('2026-08-01'),
  PARTITION p202608 VALUES LESS THAN ('2026-09-01'),
  PARTITION p202609 VALUES LESS THAN ('2026-10-01'),
  PARTITION p202610 VALUES LESS THAN ('2026-11-01'),
  PARTITION p202611 VALUES LESS THAN ('2026-12-01'),
  PARTITION p202612 VALUES LESS THAN ('2027-01-01'),
  PARTITION p202701 VALUES LESS THAN ('2027-02-01'),
  PARTITION p202702 VALUES LESS THAN ('2027-03-01'),
  PARTITION pmax    VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS dwd_user_profile (
  user_id          BIGINT      NOT NULL,
  user_name        VARCHAR(64) NULL,
  city             VARCHAR(32) NULL,
  level            TINYINT     NOT NULL DEFAULT 1,
  channel_id       INT         NOT NULL DEFAULT 0,
  register_time    DATETIME    NULL,
  register_date    DATE        NULL,
  last_update_time DATETIME    NULL,
  is_orphan        TINYINT     NOT NULL DEFAULT 0,
  batch_id         VARCHAR(32) NULL,
  etl_time         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id),
  KEY idx_register_date (register_date),
  KEY idx_level (level),
  KEY idx_channel (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户明细宽表，一行=一个用户最新画像';

CREATE TABLE IF NOT EXISTS dim_user (
  user_sk    BIGINT      NOT NULL AUTO_INCREMENT,
  user_id    BIGINT      NOT NULL,
  user_name  VARCHAR(64) NULL,
  level      TINYINT     NOT NULL,
  city       VARCHAR(32) NULL,
  channel_id INT         NOT NULL DEFAULT 0,
  start_date DATE        NOT NULL,
  end_date   DATE        NOT NULL DEFAULT '9999-12-31',
  is_current TINYINT     NOT NULL DEFAULT 1,
  version    INT         NOT NULL DEFAULT 1,
  batch_id   VARCHAR(32) NULL,
  PRIMARY KEY (user_sk),
  UNIQUE KEY uk_user_start (user_id, start_date),
  KEY idx_user_current (user_id, is_current),
  KEY idx_current (is_current)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户维表 SCD2，一行=一个用户的一段等级/城市有效期';

CREATE TABLE IF NOT EXISTS dim_product (
  product_id    BIGINT        NOT NULL,
  product_name  VARCHAR(128)  NULL,
  category_id   INT           NOT NULL DEFAULT 0,
  category_name VARCHAR(64)   NULL,
  price         DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  status        TINYINT       NOT NULL DEFAULT 1,
  PRIMARY KEY (product_id),
  KEY idx_category (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品维表';

CREATE TABLE IF NOT EXISTS dim_channel (
  channel_id   INT           NOT NULL,
  channel_name VARCHAR(64)   NOT NULL,
  channel_type VARCHAR(32)   NULL,
  daily_cost   DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  status       TINYINT       NOT NULL DEFAULT 1,
  PRIMARY KEY (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='渠道维表，channel_id=0 为 unknown';

CREATE TABLE IF NOT EXISTS dim_date (
  dt         DATE     NOT NULL,
  year       SMALLINT NOT NULL,
  quarter    TINYINT  NOT NULL,
  month      TINYINT  NOT NULL,
  day        TINYINT  NOT NULL,
  week       TINYINT  NOT NULL,
  weekday    TINYINT  NOT NULL COMMENT '1=周一 … 7=周日',
  is_weekend TINYINT  NOT NULL,
  PRIMARY KEY (dt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日期维表，2026-01-01～2027-12-31';
