"""实测 Agnes 中国站 api.agnes-ai.cn 的真实限速天花板（live 检查，verify.md 0.5，不进 pytest）。

动机（2026-10-08）：官方文档自相矛盾且只覆盖国际站——
  - Token Plan FAQ §3 说免费档 Effective RPM = 10（2026-09-23 起砍半），Allowed RPM 30
  - Common Error Codes 页 429 条目仍写 "Free users are limited to RPM 20"
  - config.yaml 的 rate_limit_rpm=18 是按"约 20 RPM"设的，高于服务端天花板 → 429
本探针不引用文档，直接实测：突发容量、429 恢复窗口、可持续 RPM。

四阶段：
  P1 基线      顺序 3 请求，测单请求延迟与 usage
  P2 突发容量  并发阶梯 1→16，找到首次出现 429 的并发档位
  P3 恢复窗口  主动超发后按 5s 步进轮询，测 429 到首个成功的等待时间
  P4 可持续速率按固定间隔连发，阶梯 10/15/20/25 per minute，统计各档 429 数

只发极短请求（max_tokens=4），token 开销可忽略；每阶段之间留冷却，避免叠加成压测。

运行：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn .venv/Scripts/python scripts/live/probe_rate_limit.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv

sys.path.insert(0, ".")

BASE = "https://api.agnes-ai.cn/v1"
MODEL = "agnes-3.0-flash"
TICKET: list[str] = []          # 全局限速用：记录每次请求发出的时刻
TICKET_LOCK = threading.Lock()


def _pace_gate(min_interval: float) -> None:
    """把请求发出时刻铺在全局时间轴上，避免同瞬间扎堆。"""
    with TICKET_LOCK:
        now = time.monotonic()
        if TICKET:
            earliest = TICKET[-1] + min_interval
        else:
            earliest = now
        wake = max(now, earliest)
        TICKET.append(wake)
    d = wake - time.monotonic()
    if d > 0:
        time.sleep(d)


def one(client: httpx.Client, key: str, *, min_interval: float = 0.0) -> dict:
    """发一个极短请求，返回耗时与状态。429 的 Retry-After 一并带回来。"""
    if min_interval:
        _pace_gate(min_interval)
    t0 = time.monotonic()
    try:
        r = client.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "回复一个字：通"}],
                "max_tokens": 4,
            },
            timeout=60,
        )
        dt = time.monotonic() - t0
        out = {"ok": r.status_code == 200, "status": r.status_code, "latency_s": round(dt, 2)}
        if r.status_code == 200:
            out["tokens"] = (r.json().get("usage") or {}).get("total_tokens")
        else:
            out["retry_after"] = r.headers.get("retry-after")
            out["body"] = r.text[:160]
        return out
    except Exception as e:  # 网络层异常也要记录，不能让探针自己崩
        return {"ok": False, "status": 0, "latency_s": round(time.monotonic() - t0, 2),
                "err": f"{type(e).__name__}: {e}"[:160]}


def burst(client: httpx.Client, key: str, n: int) -> list[dict]:
    """同时发 n 个请求，看这一波能全过几个。"""
    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(lambda _: one(client, key), range(n)))


def phase(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}", flush=True)


def main() -> int:
    load_dotenv(".env")
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        print("缺少 LLM_API_KEY（.env）", file=sys.stderr)
        return 1

    # trust_env=False：不走系统代理（历史脚本靠 NO_PROXY 绕过，这里直接不进代理栈）
    client = httpx.Client(trust_env=False)
    report: dict = {"base": BASE, "model": MODEL}

    try:
        # ---------- P1 基线 ----------
        phase("P1 基线：顺序 3 请求（测单请求延迟 / 计费口径）")
        p1 = []
        for i in range(3):
            res = one(client, key)
            p1.append(res)
            print(f"  #{i + 1} status={res['status']} latency={res['latency_s']}s "
                  f"tokens={res.get('tokens')}", flush=True)
            time.sleep(1.5)
        lat = [r["latency_s"] for r in p1 if r["ok"]]
        report["p1_baseline"] = {"runs": p1,
                                 "latency_median_s": sorted(lat)[len(lat) // 2] if lat else None}
        print(f"  → 中位延迟 {report['p1_baseline']['latency_median_s']}s")

        # ---------- P2 突发容量 ----------
        phase("P2 突发容量：并发阶梯 1→16，定位首次 429")
        print("  （阶梯之间冷却 20s，让限速窗口恢复干净）", flush=True)
        p2 = []
        ceiling = None
        for n in [1, 2, 3, 4, 6, 8, 10, 12, 14, 16]:
            res = burst(client, key, n)
            ok = sum(1 for r in res if r["ok"])
            fails = [r for r in res if not r["ok"]]
            row = {"concurrency": n, "ok": ok, "failed": len(fails),
                   "statuses": sorted({r["status"] for r in fails}),
                   "max_latency_s": max(r["latency_s"] for r in res)}
            p2.append(row)
            print(f"  并发 {n:>2}：成功 {ok}/{n}  失败状态 {row['statuses'] or '无'}  "
                  f"最慢 {row['max_latency_s']}s", flush=True)
            if fails and ceiling is None:
                ceiling = n
                print(f"  ★ 首次出现失败在并发 {n} → 突发容量约 {n - 1}", flush=True)
                break
            if n == 4:
                time.sleep(20)
        report["p2_burst"] = p2
        report["p2_ceiling_guess"] = ceiling

        # ---------- P3 恢复窗口 ----------
        phase("P3 恢复窗口：先打满，再测 429 到首个成功的等待时间")
        if ceiling:
            print(f"  用并发 {ceiling} 打满以触发 429，然后每 5s 轮询", flush=True)
            burst(client, key, ceiling)
        else:
            print("  P2 未触发 429（P3 跳过）", flush=True)
        report["p3_recovery"] = []
        t429 = time.monotonic()
        recovered = None
        for i in range(36):  # 最多等 180s
            res = one(client, key)
            waited = round(time.monotonic() - t429, 1)
            if res["ok"]:
                recovered = waited
                print(f"  第 {i + 1} 次轮询（+{waited}s）：恢复成功 200", flush=True)
                break
            if i % 2 == 0:
                print(f"  第 {i + 1} 次轮询（+{waited}s）：status={res['status']} "
                      f"Retry-After={res.get('retry_after')}", flush=True)
            report["p3_recovery"].append({"waited_s": waited, "status": res["status"],
                                          "retry_after": res.get("retry_after")})
            time.sleep(5)
        report["p3_recovered_after_s"] = recovered
        print(f"  → 429 后 {recovered}s 恢复" if recovered else "  → 180s 内未恢复")

        # ---------- P4 可持续速率 ----------
        phase("P4 可持续速率：按固定间隔连发，阶梯 10/15/20/25 per minute")
        print("  （每档持续约 100s；档间冷却 30s）", flush=True)
        p4 = []
        for rpm in [10, 15, 20, 25]:
            interval = 60.0 / rpm
            dur = 100
            n = int(dur / interval)
            print(f"\n  --- {rpm} per minute（间隔 {interval:.1f}s，共 {n} 次）---", flush=True)
            res = []
            for i in range(n):
                res.append(one(client, key, min_interval=interval))
                time.sleep(0)
            ok = sum(1 for r in res if r["ok"])
            fails = [r for r in res if not r["ok"]]
            statuses = sorted({r["status"] for r in fails})
            lat_ok = sorted(r["latency_s"] for r in res if r["ok"])
            row = {"rpm": rpm, "n": n, "ok": ok, "failed": len(fails), "statuses": statuses,
                   "latency_p50": lat_ok[len(lat_ok) // 2] if lat_ok else None,
                   "latency_max": max((r["latency_s"] for r in res), default=None)}
            p4.append(row)
            print(f"  → 成功 {ok}/{n}，失败 {statuses or '无'}，"
                  f"延迟 p50={row['latency_p50']}s max={row['latency_max']}s", flush=True)
            if rpm != 25:
                time.sleep(30)
        report["p4_sustained"] = p4

        # ---------- 结论 ----------
        phase("结论（供写入 config.yaml / plan.md §12）")
        clean = [r["rpm"] for r in p4 if r["failed"] == 0]
        print(f"  突发容量约      : {report.get('p2_ceiling_guess', '未触发失败')}")
        print(f"  429 恢复窗口    : {report.get('p3_recovered_after_s')}s")
        print(f"  零失败可持续档位: {clean or '10/15/20/25 均未零失败'}")
        print(f"  建议 rate_limit_rpm = {min(clean) - 2 if clean else '需人工判断'}"
              f"  （最低零失败档再留 2 RPM 余量）")
        print(f"\n  原始记录已打印；如需入库请重定向到 docs/evidence/。")

    finally:
        client.close()

    out = os.environ.get("PROBE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 记录已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())