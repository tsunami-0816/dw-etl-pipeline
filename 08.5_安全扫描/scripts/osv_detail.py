#!/usr/bin/env python3
"""查询 OSV 漏洞详情（严重度 / CVSS / 是否影响本地用法），供 8.5 分级定级使用。"""
from __future__ import annotations

import json
import sys
import urllib.request

IDS = sys.argv[1:]
for vid in IDS:
    req = urllib.request.Request(f"https://api.osv.dev/v1/vulns/{vid}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    print(f"=== {vid} ===")
    print("summary:", data.get("summary"))
    print("severity:", data.get("severity"))
    print("database_specific:", data.get("database_specific"))
    print("aliases:", data.get("aliases"))
    for aff in data.get("affected", []):
        print("package:", aff.get("package"), "| ranges:", aff.get("ranges"))
    print()
