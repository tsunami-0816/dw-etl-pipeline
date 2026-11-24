-- 01_databases.sql · 建库、字符集/时区、读写账号分离（D-01 / D-47 / D-48）
-- 密码占位符 ${...} 由 scripts/run_sql.py 从环境变量渲染并校验（SAFE_SECRET）；
-- 未渲染时不得提交真实口令。root 仅用于本初始化脚本与本地故障处置。

SET NAMES utf8mb4;
SET time_zone = '+08:00';

CREATE DATABASE IF NOT EXISTS biz DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS dw  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- 读写账号分离：dw_etl 执行 ETL/造数/质量/对账，dw_reader 仅验收取数
-- dw_etl 口令取自 MYSQL_PASSWORD（与运行时连接配置同源），dw_reader 取自 MYSQL_READER_PASSWORD。
CREATE USER IF NOT EXISTS 'dw_etl'@'%' IDENTIFIED WITH mysql_native_password BY '${MYSQL_PASSWORD}';
CREATE USER IF NOT EXISTS 'dw_reader'@'%' IDENTIFIED WITH mysql_native_password BY '${MYSQL_READER_PASSWORD}';

-- dw_etl：biz 只读（造数用 seed 账号/root，不扩权）+ dw 读写 + 执行过程
GRANT SELECT ON biz.* TO 'dw_etl'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES ON dw.* TO 'dw_etl'@'%';
GRANT EXECUTE ON dw.* TO 'dw_etl'@'%';

-- dw_reader：dw 只读，用于报告查询与 pytest 断言
GRANT SELECT ON dw.* TO 'dw_reader'@'%';

FLUSH PRIVILEGES;
