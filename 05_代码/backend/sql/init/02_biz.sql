-- 02_biz.sql · 源库 4 张业务表 DDL（D-02）
-- 故意不建 order_no 唯一键：允许不同 order_id 携带相同 order_no，制造"重复订单"脏数据，
-- DWD 再按 order_no 去重并写 dq_issue；若建唯一键则 C04 去重规则无真实输入。

USE biz;

CREATE TABLE IF NOT EXISTS user_info (
  user_id       BIGINT       NOT NULL COMMENT '用户业务主键',
  user_name     VARCHAR(64)  NOT NULL,
  phone         VARCHAR(20)  NULL,
  city          VARCHAR(32)  NULL,
  level         TINYINT      NOT NULL DEFAULT 1 COMMENT '1 普通 2 银卡 3 金卡',
  channel_id    INT          NULL COMMENT '注册来源渠道，用于按渠道统计新增',
  register_time DATETIME     NOT NULL,
  update_time   DATETIME     NOT NULL,
  PRIMARY KEY (user_id),
  KEY idx_register_time (register_time),
  KEY idx_update_time (update_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟用户源表';

CREATE TABLE IF NOT EXISTS product_info (
  product_id    BIGINT        NOT NULL,
  product_name  VARCHAR(128)  NOT NULL,
  category_id   INT           NOT NULL,
  category_name VARCHAR(64)   NOT NULL,
  price         DECIMAL(12,2) NOT NULL,
  status        TINYINT       NOT NULL DEFAULT 1 COMMENT '1 在售 0 下架',
  update_time   DATETIME      NOT NULL,
  PRIMARY KEY (product_id),
  KEY idx_category (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟商品源表';

CREATE TABLE IF NOT EXISTS channel_info (
  channel_id   INT           NOT NULL,
  channel_name VARCHAR(64)   NOT NULL,
  channel_type VARCHAR(32)   NOT NULL COMMENT 'search/social/display/offline',
  daily_cost   DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '模拟日投放成本，ROI 分母',
  status       TINYINT       NOT NULL DEFAULT 1,
  update_time  DATETIME      NOT NULL,
  PRIMARY KEY (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟渠道源表';

CREATE TABLE IF NOT EXISTS order_info (
  order_id    BIGINT        NOT NULL,
  order_no    VARCHAR(32)   NOT NULL,
  user_id     BIGINT        NOT NULL,
  product_id  BIGINT        NOT NULL,
  channel_id  INT           NULL COMMENT '允许为空，模拟空渠道脏数据',
  quantity    INT           NOT NULL DEFAULT 1,
  amount      DECIMAL(12,2) NOT NULL COMMENT '允许为负，模拟金额越界脏数据',
  status      VARCHAR(16)   NOT NULL COMMENT 'paid/refunded/cancelled',
  pay_time    DATETIME      NOT NULL,
  refund_time DATETIME      NULL,
  update_time DATETIME      NOT NULL,
  PRIMARY KEY (order_id),
  KEY idx_order_no (order_no),
  KEY idx_pay_time (pay_time),
  KEY idx_user (user_id),
  KEY idx_channel (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟订单源表';
