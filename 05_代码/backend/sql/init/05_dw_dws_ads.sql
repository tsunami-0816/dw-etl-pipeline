-- 05_dw_dws_ads.sql · DWS 2 张聚合表 + ADS 4 张视角表 + Spark stg 表（D-10～D-15 / D-28）
-- ADS 拆 4 张是为对齐基线 C02「ADS 4 张表非空」，12 指标全部有落点（评审 P1-04）。

USE dw;

CREATE TABLE IF NOT EXISTS dws_daily_channel (
  dt            DATE         NOT NULL,
  channel_id    INT          NOT NULL,
  new_users     INT          NOT NULL DEFAULT 0,
  orders        INT          NOT NULL DEFAULT 0,
  paid_orders   INT          NOT NULL DEFAULT 0,
  gmv           DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_orders INT          NOT NULL DEFAULT 0,
  refund_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_rate   DECIMAL(6,4)  NOT NULL DEFAULT 0.0000,
  cost          DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  roi           DECIMAL(10,4) NOT NULL DEFAULT 0.0000,
  batch_id      VARCHAR(32)  NULL,
  etl_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, channel_id),
  KEY idx_channel_dt (channel_id, dt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日/渠道聚合，一行=一天一个渠道'
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

CREATE TABLE IF NOT EXISTS dws_daily_category (
  dt            DATE         NOT NULL,
  category_id   INT          NOT NULL,
  category_name VARCHAR(64)  NULL,
  orders        INT          NOT NULL DEFAULT 0,
  paid_orders   INT          NOT NULL DEFAULT 0,
  gmv           DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_orders INT          NOT NULL DEFAULT 0,
  refund_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_rate   DECIMAL(6,4)  NOT NULL DEFAULT 0.0000,
  batch_id      VARCHAR(32)  NULL,
  etl_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, category_id),
  KEY idx_category_dt (category_id, dt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日/品类聚合，一行=一天一个品类'
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

-- Spark 90 天重算结果只允许 overwrite 本表，再由 SQL upsert 合并进 dws_daily_channel（ADR-02：禁止 overwrite 目标表）
CREATE TABLE IF NOT EXISTS stg_dws_daily_channel_spark (
  dt            DATE         NOT NULL,
  channel_id    INT          NOT NULL,
  new_users     INT          NOT NULL DEFAULT 0,
  orders        INT          NOT NULL DEFAULT 0,
  paid_orders   INT          NOT NULL DEFAULT 0,
  gmv           DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_orders INT          NOT NULL DEFAULT 0,
  refund_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_rate   DECIMAL(6,4)  NOT NULL DEFAULT 0.0000,
  cost          DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  roi           DECIMAL(10,4) NOT NULL DEFAULT 0.0000,
  engine        VARCHAR(16)  NOT NULL DEFAULT 'spark',
  loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Spark/pandas 重算结果暂存表，仅 overwrite，不直接覆盖 DWS';

CREATE TABLE IF NOT EXISTS ads_kpi_daily (
  dt          DATE         NOT NULL,
  kpi_code    VARCHAR(32)  NOT NULL COMMENT 'NEW_USERS/GMV/REFUND_RATE',
  kpi_name    VARCHAR(64)  NOT NULL,
  kpi_value   DECIMAL(16,4) NOT NULL DEFAULT 0,
  kpi_unit    VARCHAR(16)  NOT NULL DEFAULT '',
  mom_rate    DECIMAL(10,4) NULL COMMENT '日环比，(今日-昨日)/昨日',
  ma7         DECIMAL(16,4) NULL COMMENT '近 7 日均值（含当日）',
  batch_id    VARCHAR(32)  NULL,
  update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, kpi_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='一级指标日表，一行=一天一个不按渠道拆的指标';

CREATE TABLE IF NOT EXISTS ads_channel_roi (
  dt           DATE         NOT NULL,
  channel_id   INT          NOT NULL,
  channel_name VARCHAR(64)  NOT NULL,
  cost         DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  gmv          DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  roi          DECIMAL(10,4) NOT NULL DEFAULT 0.0000,
  new_users    INT          NOT NULL DEFAULT 0,
  orders       INT          NOT NULL DEFAULT 0,
  cac          DECIMAL(14,2) NULL COMMENT '获客成本 = cost / new_users',
  update_time  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='渠道 ROI 表，一行=一天一个渠道';

CREATE TABLE IF NOT EXISTS ads_channel_daily (
  dt            DATE         NOT NULL,
  channel_id    INT          NOT NULL,
  channel_name  VARCHAR(64)  NOT NULL,
  new_users     INT          NOT NULL DEFAULT 0,
  orders        INT          NOT NULL DEFAULT 0,
  paid_orders   INT          NOT NULL DEFAULT 0,
  gmv           DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_orders INT          NOT NULL DEFAULT 0,
  refund_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_rate   DECIMAL(6,4)  NOT NULL DEFAULT 0.0000,
  aov           DECIMAL(14,2) NULL COMMENT '客单价 = gmv / paid_orders',
  update_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='渠道经营日报视图，一行=一天一个渠道';

CREATE TABLE IF NOT EXISTS ads_category_sales (
  dt            DATE         NOT NULL,
  category_id   INT          NOT NULL,
  category_name VARCHAR(64)  NULL,
  orders        INT          NOT NULL DEFAULT 0,
  gmv           DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  refund_orders INT          NOT NULL DEFAULT 0,
  refund_rate   DECIMAL(6,4)  NOT NULL DEFAULT 0.0000,
  update_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dt, category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='品类销售日报视图，一行=一天一个品类';
