"""联调临时 wrapper：预先创建 master=local[1] 的 SparkSession，使 spark_rebuild_dws 复用，规避 Windows local[*] worker 回连偶发失败。不修改产品代码。"""
import os
import sys
from pathlib import Path

SPARK_DIR = Path(r"D:\lg\project\工作流测试\projects\dw-etl-pipeline\05_代码\backend\spark")
BACKEND_DIR = SPARK_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
sys.path.insert(0, str(SPARK_DIR))

os.environ.setdefault("_JAVA_OPTIONS", "-Djava.net.preferIPv4Stack=true")

from pyspark.sql import SparkSession  # noqa: E402

spark = (SparkSession.builder.master("local[1]")
         .appName("dw_etl_rebuild_dws")
         .config("spark.sql.session.timeZone", "Asia/Shanghai")
         .config("spark.driver.memory", "1g")
         .config("spark.driver.host", "127.0.0.1")
         .config("spark.driver.bindAddress", "127.0.0.1")
         .getOrCreate())

import spark_rebuild_dws  # noqa: E402

sys.argv = ["spark_rebuild_dws.py", "--engine", "spark",
            "--start-date", "2026-11-20", "--end-date", "2026-11-30", "--mode", "both"]
rc = spark_rebuild_dws.main()
spark.stop()
sys.exit(rc)
