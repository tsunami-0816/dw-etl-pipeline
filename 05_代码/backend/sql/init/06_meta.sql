-- 06_meta.sql · 元数据与治理表（D-25 / D-35）
-- etl_run_log(batch_id, layer) 唯一键保证同批同层只一条记录；dq_issue 唯一键防重复执行翻倍。

USE dw;

CREATE TABLE IF NOT EXISTS etl_run_log (
  log_id     BIGINT      NOT NULL AUTO_INCREMENT,
  batch_id   VARCHAR(32) NOT NULL,
  dt         DATE        NOT NULL,
  layer      VARCHAR(16) NOT NULL COMMENT 'ODS/DWD/DWS/ADS/SPARK/RECONCILE/QUALITY/ALL',
  proc_name  VARCHAR(64) NULL,
  start_at   DATETIME(3) NOT NULL,
  end_at     DATETIME(3) NULL,
  rows_in    BIGINT      NOT NULL DEFAULT 0,
  rows_out   BIGINT      NOT NULL DEFAULT 0,
  status     VARCHAR(16) NOT NULL COMMENT 'running/success/failed/blocked/skipped',
  error_code VARCHAR(32) NULL,
  error_msg  VARCHAR(1000) NULL,
  PRIMARY KEY (log_id),
  UNIQUE KEY uk_batch_layer (batch_id, layer),
  KEY idx_dt_status (dt, status),
  KEY idx_layer_status (layer, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务运行日志';

CREATE TABLE IF NOT EXISTS dq_report (
  report_id   BIGINT      NOT NULL AUTO_INCREMENT,
  dt          DATE        NOT NULL,
  rule_code   VARCHAR(16) NOT NULL,
  rule_name   VARCHAR(128) NOT NULL,
  level       CHAR(2)     NOT NULL COMMENT 'P0/P1/P2',
  table_name  VARCHAR(64) NOT NULL,
  total_rows  BIGINT      NOT NULL DEFAULT 0,
  failed_rows BIGINT      NOT NULL DEFAULT 0,
  pass_rate   DECIMAL(6,4) NOT NULL DEFAULT 1.0000,
  status      VARCHAR(16) NOT NULL COMMENT 'pass/warn/blocked',
  detail      VARCHAR(1000) NULL,
  batch_id    VARCHAR(32) NULL,
  run_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (report_id),
  UNIQUE KEY uk_dt_rule (dt, rule_code),
  KEY idx_dt_level (dt, level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量规则汇总报告';

CREATE TABLE IF NOT EXISTS dq_issue (
  issue_id     BIGINT      NOT NULL AUTO_INCREMENT,
  dt           DATE        NOT NULL,
  rule_code    VARCHAR(16) NOT NULL,
  level        CHAR(2)     NOT NULL,
  table_name   VARCHAR(64) NOT NULL,
  pk_value     VARCHAR(128) NOT NULL DEFAULT '',
  issue_detail VARCHAR(500) NULL,
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (issue_id),
  UNIQUE KEY uk_dt_rule_pk (dt, rule_code, pk_value),
  KEY idx_dt_rule (dt, rule_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量异常明细';

CREATE TABLE IF NOT EXISTS dq_alert (
  alert_id   BIGINT      NOT NULL AUTO_INCREMENT,
  dt         DATE        NOT NULL,
  rule_code  VARCHAR(16) NOT NULL,
  level      CHAR(2)     NOT NULL,
  message    VARCHAR(500) NOT NULL,
  status     VARCHAR(16) NOT NULL DEFAULT 'open' COMMENT 'open/acked/closed',
  created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  acked_at   DATETIME    NULL,
  PRIMARY KEY (alert_id),
  KEY idx_status_level (status, level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量告警表';
