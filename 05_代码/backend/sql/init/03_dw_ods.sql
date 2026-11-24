-- 03_dw_ods.sql · ODS 层 4 张表（D-03）
-- 原样保留源行，带 dt / batch_id / load_time，按 (业务主键, dt) 重写保证同日重跑不翻倍。

CREATE DATABASE IF NOT EXISTS dw DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE dw;

CREATE TABLE IF NOT EXISTS ods_user (
  ods_id        BIGINT      NOT NULL AUTO_INCREMENT,
  user_id       BIGINT      NOT NULL,
  user_name     VARCHAR(64) NULL,
  phone         VARCHAR(20) NULL,
  city          VARCHAR(32) NULL,
  level         TINYINT     NULL,
  channel_id    INT         NULL,
  register_time DATETIME    NULL,
  update_time   DATETIME    NULL,
  dt            DATE        NOT NULL,
  batch_id      VARCHAR(32) NOT NULL,
  load_time     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ods_id),
  UNIQUE KEY uk_user_dt (user_id, dt),
  KEY idx_dt (dt),
  KEY idx_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS 用户，一行=一个用户在一次 ETL 日期的抽取';

CREATE TABLE IF NOT EXISTS ods_order (
  ods_id      BIGINT        NOT NULL AUTO_INCREMENT,
  order_id    BIGINT        NOT NULL,
  order_no    VARCHAR(32)   NOT NULL,
  user_id     BIGINT        NOT NULL,
  product_id  BIGINT        NOT NULL,
  channel_id  INT           NULL,
  quantity    INT           NULL,
  amount      DECIMAL(12,2) NULL,
  status      VARCHAR(16)   NULL,
  pay_time    DATETIME      NULL,
  refund_time DATETIME      NULL,
  update_time DATETIME      NULL,
  dt          DATE        NOT NULL,
  batch_id    VARCHAR(32) NOT NULL,
  load_time   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ods_id),
  UNIQUE KEY uk_order_id_dt (order_id, dt),
  KEY idx_order_no (order_no),
  KEY idx_dt (dt),
  KEY idx_channel (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS 订单，一行=一个订单在一次 ETL 日期的抽取';

CREATE TABLE IF NOT EXISTS ods_product (
  ods_id        BIGINT        NOT NULL AUTO_INCREMENT,
  product_id    BIGINT        NOT NULL,
  product_name  VARCHAR(128)  NULL,
  category_id   INT           NULL,
  category_name VARCHAR(64)   NULL,
  price         DECIMAL(12,2) NULL,
  status        TINYINT       NULL,
  update_time   DATETIME      NULL,
  dt            DATE        NOT NULL,
  batch_id    VARCHAR(32) NOT NULL,
  load_time   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ods_id),
  UNIQUE KEY uk_product_dt (product_id, dt),
  KEY idx_dt (dt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS 商品日快照';

CREATE TABLE IF NOT EXISTS ods_channel (
  ods_id       BIGINT        NOT NULL AUTO_INCREMENT,
  channel_id   INT           NOT NULL,
  channel_name VARCHAR(64)   NULL,
  channel_type VARCHAR(32)   NULL,
  daily_cost   DECIMAL(12,2) NULL,
  status       TINYINT       NULL,
  update_time  DATETIME      NULL,
  dt           DATE        NOT NULL,
  batch_id     VARCHAR(32) NOT NULL,
  load_time    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ods_id),
  UNIQUE KEY uk_channel_dt (channel_id, dt),
  KEY idx_dt (dt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS 渠道日快照';
