#!/usr/bin/env python3
"""依赖 CVE 直查 OSV API（无需安装 pip-audit，适配离线/低资源环境）。

用法：python osv_audit.py <requirements.txt> [<requirements2.txt> ...]
输出：每个包的漏洞编号/摘要/修复版本；发现漏洞退出 1，全清退出 0。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

API = "https://api.osv.dev/v1/query"
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)\s*(?:#.*)?$")


def parse_requirements(path: Path) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = PIN.match(line.strip())
        if m:
            pins.append((m.group(1), m.group(2)))
    return pins


def query_osv(name: str, version: str) -> list[dict]:
    body = json.dumps({"package": {"name": name, "ecosystem": "PyPI"}, "version": version}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode()).get("vulns", [])


def main(paths: list[str]) -> int:
    pins: list[tuple[str, str]] = []
    for p in paths:
        pins.extend(parse_requirements(Path(p)))
    print(f"待审计依赖 {len(pins)} 个：{', '.join(f'{n}=={v}' for n, v in pins)}")
    findings: list[tuple[str, str, str, list[str]]] = []
    for name, version in pins:
        try:
            vulns = query_osv(name, version)
        except Exception as exc:  # 网络异常必须显式报出，不得当作「无漏洞」
            print(f"⚠️  {name}=={version} 查询失败：{type(exc).__name__}: {exc}")
            findings.append((name, version, "QUERY_FAILED", []))
            continue
        for v in vulns:
            aliases = [a for a in v.get("aliases", []) if a.startswith("CVE-")]
            fixed = sorted({r.get("fixed") for aff in v.get("affected", []) for r in aff.get("ranges", []) for r in r.get("events", []) if r.get("fixed")})
            findings.append((name, version, ", ".join(aliases) or v.get("id", "?"),
                             fixed, v.get("summary", "")[:120]))
        print(f"✅ {name}=={version} 无已知漏洞" if not vulns else f"❌ {name}=={version} 命中 {len(vulns)} 条")
    if not findings:
        print("\n结论：全部依赖无已知 CVE")
        return 0
    print("\n命中明细：")
    for item in findings:
        name, version, vuln_id, fixed = item[0], item[1], item[2], item[3]
        summary = item[4] if len(item) > 4 else ""
        print(f"  - {name}=={version} | {vuln_id} | 修复版本: {fixed or '无'} | {summary}")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python osv_audit.py <requirements.txt> [...]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1:]))
