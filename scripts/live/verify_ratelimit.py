"""真实验证新的共享限速器与 429 重试行为（live 检查，verify.md 0.5，不进 pytest）。

验证三件事：
  A. 8 RPM 下正常请求的实际间隔是否稳定（默认限速可用）；
  B. 受控 ≤12 RPM 探测是否触发 429、429 是否带 Retry-After；
  C. 共享性与重试路径（用脚本化传输层走真实代码，不消耗额度）：
     普通请求只限速一次 / 429 重试每次发出前都重新限速 / Retry-After 被采纳 /
     主 Agent 与子 Agent 共用同一个限速器实例。

请求纪律：
  - 每次请求 max_tokens=4，固定一个字，token 开销可忽略；
  - A 段最多 8 个请求；B 段最多 8 个请求且不超过 12 RPM；
  - 出现第一个 429 后立即停止继续加压；
  - 不跑长时间阶梯压测；不打印、不落盘 API key。

运行：
  set NO_PROXY=api.agnes-ai.cn
  PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/live/verify_ratelimit.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402

from agent.llm import LLMClient  # noqa: E402
from agent.ratelimit import RateLimiter  # noqa: E402
from agent.v1 import Paced  # noqa: E402

PROMPT = "回复一个字：通"
BASE = "https://api.agnes-ai.cn/v1"
MODEL = "agnes-3.0-flash"


def phase(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)


def _resp(text: str = "通"):
    return type("R", (), {
        "choices": [type("Ch", (), {"message": type("M", (), {
            "content": text, "tool_calls": None})()})()],
        "usage": type("U", (), {"prompt_tokens": 8, "completion_tokens": 2,
                                "total_tokens": 10})()})()


class Script:
    """脚本化传输层：记录每次 create() 的发出时刻，用来核对限速是否覆盖每一次请求。"""

    def __init__(self, items):
        self.items, self.sent = list(items), []

    def create(self, **kw):
        self.sent.append(time.monotonic())
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _with_script(limiter, script) -> LLMClient:
    c = LLMClient(api_key="probe-not-a-real-key", limiter=limiter)
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=script))
    return c


def _rate_limit_error(retry_after: str | None = None):
    import httpx
    import openai
    headers = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError(
        "probe 429", body=None,
        response=httpx.Response(429, headers=headers,
                                request=httpx.Request("POST", "http://probe.test")))


def main() -> int:
    load_dotenv(".env")
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        print("缺少 LLM_API_KEY（.env）", file=sys.stderr)
        return 1

    # 请求预算纪律：A_N + B_N ≤ 16（默认已按修复后重跑的剩余额度调小）
    a_n = int(os.environ.get("A_N", "5"))
    b_n = int(os.environ.get("B_N", "3"))
    b_rpm = float(os.environ.get("B_RPM", "12"))

    import httpx
    report: dict = {"model": MODEL, "max_tokens": 4,
                    "request_budget": {"A": a_n, "B": b_n, "cap": 16}}
    client = httpx.Client(trust_env=False, timeout=60)
    orig_post = client.post

    def one(tag: str) -> dict:
        # 必须走 client.post（可能被限速钩子替换过），不能走 orig_post——
        # 否则限速钩子根本不会被调用，测出来的"速率"是假的（2026-10-09 首轮就踩了这个坑）。
        t0 = time.monotonic()
        r = client.post(f"{BASE}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": MODEL,
                              "messages": [{"role": "user", "content": PROMPT}],
                              "max_tokens": 4})
        row = {"tag": tag, "status": r.status_code,
               "latency_s": round(time.monotonic() - t0, 2),
               "retry_after": r.headers.get("retry-after")}
        if r.status_code == 200:
            row["total_tokens"] = (r.json().get("usage") or {}).get("total_tokens")
        return row

    try:
        # ---------------- A. 8 RPM 正常节奏 ----------------
        phase(f"A. 共享限速器 @ 8 RPM：{a_n} 个短请求，核对实际间隔")
        limiter = RateLimiter(8.0)
        sent_at: list[float] = []

        def paced_post(*a, **kw):
            limiter.wait()
            sent_at.append(time.monotonic())
            return orig_post(*a, **kw)

        client.post = paced_post
        rows_a, t0 = [], time.monotonic()
        for i in range(a_n):
            rows_a.append(one(f"A{i + 1}"))
            print(f"  A{i + 1}: status={rows_a[-1]['status']} "
                  f"latency={rows_a[-1]['latency_s']}s "
                  f"tokens={rows_a[-1].get('total_tokens')}", flush=True)
        elapsed = time.monotonic() - t0
        gaps = [round(b - a, 2) for a, b in zip(sent_at, sent_at[1:])]
        ok_a = sum(1 for r in rows_a if r["status"] == 200)
        report["a_rpm8"] = {"rows": rows_a, "ok": ok_a, "n": len(rows_a),
                            "target_interval_s": limiter.interval, "actual_gaps_s": gaps,
                            "elapsed_s": round(elapsed, 1),
                            "effective_rpm": round((len(sent_at) - 1) / (elapsed / 60), 2),
                            "limiter_grants": limiter.grants,
                            "limiter_wait_total_s": round(limiter.total_wait_s, 2)}
        print(f"  → {ok_a}/{len(rows_a)} 成功；目标间隔 {limiter.interval}s，实际 {gaps}")
        print(f"  → 限速器放行 {limiter.grants} 次（应 == 发出请求数 {len(sent_at)}），"
              f"累计等待 {limiter.total_wait_s:.1f}s，"
              f"实测速率 {report['a_rpm8']['effective_rpm']} RPM")
        client.post = orig_post

        # ---------------- B. 受控 429 探测 ----------------
        phase(f"B. 受控探测：{b_n} 个请求 @ {b_rpm} RPM；出现第一个 429 立即停止加压")
        lim_b = RateLimiter(b_rpm)
        rows_b: list[dict] = []
        hit429 = False

        def paced_post_b(*a, **kw):
            lim_b.wait()
            return orig_post(*a, **kw)

        client.post = paced_post_b
        for i in range(b_n):
            row = one(f"B{i + 1}")
            rows_b.append(row)
            print(f"  B{i + 1}: status={row['status']} latency={row['latency_s']}s "
                  f"Retry-After={row['retry_after']}", flush=True)
            if row["status"] == 429:
                hit429 = True
                print("  已出现 429 —— 按纪律停止继续加压。", flush=True)
                break
        report["b_probe"] = {"rows": rows_b, "rpm": b_rpm, "hit_429": hit429,
                             "retry_after_values": [r["retry_after"] for r in rows_b
                                                    if r["retry_after"]],
                             "grants": lim_b.grants}
        print(f"  → 是否触发 429：{'是' if hit429 else '否（本轮未触发）'}；"
              f"限速器放行 {lim_b.grants} 次")
        client.post = orig_post

        # ---------------- C. 共享性与重试路径（脚本化传输层，不消耗额度） ----------------
        phase("C. 共享限速器与重试路径（脚本化传输层走真实代码）")

        lim1 = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
        sc1 = Script([_resp()])
        _with_script(lim1, sc1).chat([{"role": "user", "content": PROMPT}])
        c1 = {"requests": len(sc1.sent), "grants": lim1.grants}

        lim2 = RateLimiter(8.0)
        sc2 = Script([_rate_limit_error(), _rate_limit_error(), _resp()])
        c2 = _with_script(lim2, sc2)
        t2 = time.monotonic()
        c2.chat([{"role": "user", "content": PROMPT}])
        c2_row = {"requests": len(sc2.sent), "grants": lim2.grants,
                  "send_gaps_s": [round(b - a, 2) for a, b in zip(sc2.sent, sc2.sent[1:])],
                  "events": c2.last_call_events, "elapsed_s": round(time.monotonic() - t2, 1),
                  "limiter_wait_total_s": round(lim2.total_wait_s, 1)}

        slept: list[float] = []
        import agent.llm as llm_mod
        real_sleep = llm_mod.time.sleep
        llm_mod.time.sleep = slept.append          # 只桩掉退避 sleep，限速/请求路径保持真实
        try:
            lim3 = RateLimiter(1e9, clock=lambda: 0.0, sleep=lambda _s: None)
            sc3 = Script([_rate_limit_error(retry_after="17"), _resp()])
            c3 = _with_script(lim3, sc3)
            c3.chat([{"role": "user", "content": PROMPT}])
        finally:
            llm_mod.time.sleep = real_sleep
        c3_row = {"requests": len(sc3.sent), "backoff_sleeps": slept,
                  "events": c3.last_call_events}

        from agent.subagents import SubAgentRunner
        lim4 = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
        sc4 = Script([_resp() for _ in range(3)])
        c4 = _with_script(lim4, sc4)
        paced = Paced(c4, lim4)
        parent = SimpleNamespace(db_path="data/finsights.duckdb", store=None, cfg={},
                                 as_of=None, enabled_tools=None)
        SubAgentRunner(paced, parent, "reviewer", "system prompt", max_steps=2).run("审查")
        after_sub = lim4.grants
        paced.chat([{"role": "user", "content": PROMPT}])
        c4_row = {"shared_instance": c4.limiter is lim4,
                  "paced_adds_second_layer": paced._limiter is not None,
                  "grants_after_subagent": after_sub, "grants_after_main": lim4.grants}

        report["c_offline"] = {"c1_plain": c1, "c2_retry": c2_row,
                               "c3_retry_after": c3_row, "c4_shared": c4_row}
        print(f"  C1 普通请求：发出 {c1['requests']} 次，限速放行 {c1['grants']} 次")
        print(f"  C2 429 重试：发出 {c2_row['requests']} 次，限速放行 {c2_row['grants']} 次，"
              f"发送间隔 {c2_row['send_gaps_s']}（各 ≈7.5s → 重试也过了限速器）")
        print(f"     重试事件：{[(e['wait_source'], e['wait_s'], e['pace_wait_s']) for e in c2_row['events']]}")
        print(f"  C3 Retry-After=17 → 实际退避 {c3_row['backoff_sleeps']}s，"
              f"来源 {c3_row['events'][0]['wait_source']}")
        print(f"  C4 共用同一实例={c4_row['shared_instance']}，"
              f"Paced 叠加第二层={c4_row['paced_adds_second_layer']}，"
              f"子 Agent {c4_row['grants_after_subagent']} 次 → 主 Agent {c4_row['grants_after_main']} 次")
    finally:
        client.close()

    out = Path(os.environ.get("RATELIMIT_VERIFY_OUT", "runs/rate_limit_verify.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n记录已写入 {out}（不含 API key）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())