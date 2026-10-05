"""FinSights 看板（dashboard_design.md）：实验列表 → 全部 query → 单条详情。

启动：.venv/Scripts/python -m streamlit run dashboard/app.py --server.headless true
浏览只读；只有点击"开始分析"才会调用模型。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.index import build_index, experiment_rows, mark_reviewed, reviewed_marks

st.set_page_config(page_title="FinSights 看板", layout="wide")

if "pending_proc" not in st.session_state:
    st.session_state.pending_proc = None
if "pending_dir" not in st.session_state:
    st.session_state.pending_dir = None

experiments = build_index(refresh=st.session_state.pop("refresh_index", False))
exp_names = [f"{e['experiment_id']} · {e['name']} · {e.get('created') or '?'}"
             for e in experiments]
st.title("FinSights 实验与 Query 看板")
c1, c2 = st.columns([4, 1])
with c1:
    sel = st.selectbox("实验", exp_names) if exp_names else None
with c2:
    st.write("")
    if st.button("刷新索引"):
        st.session_state.refresh_index = True
        st.rerun()
if not sel:
    st.info("runs/ 下暂无实验。请先用 agent.cli 跑一条 query。")
    st.stop()
exp = experiments[exp_names.index(sel)]
rows = experiment_rows(exp)
reviewed = reviewed_marks()

# ---------- 顶部：单 query 输入与执行 ----------
st.subheader("单 query 分析")
with st.form("run_form", clear_on_submit=False):
    query = st.text_area("query", height=68,
                         value=st.session_state.get("last_query", ""))
    ca, cb, cc, cd = st.columns([3, 1, 1, 1])
    as_of = ca.text_input("as_of（可选，YYYY-MM-DD）")
    max_steps = cb.number_input("步数上限", min_value=5, max_value=100,
                                value=int(st.session_state.get("cfg_max_steps", 20)))
    cd.write("")
    submitted = st.form_submit_button("开始分析", disabled=st.session_state.pending_proc is not None)
if submitted:
    if not query.strip():
        st.warning("query 为空")
    else:
        run_id = pd.Timestamp.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path("runs") / f"cli-{run_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "agent.cli", "--question", query,
               "--out-root", "runs", "--max-steps", str(int(max_steps))]
        if as_of.strip():
            cmd += ["--as-of", as_of.strip()]
        st.session_state.pending_proc = subprocess.Popen(cmd)
        st.session_state.pending_dir = str(out_dir)
        st.session_state["last_query"] = query
        st.rerun()
if st.session_state.pending_proc is not None:
    proc = st.session_state.pending_proc
    if proc.poll() is None:
        st.info("运行中……（只读刷新，不重复启动）")
        if st.button("刷新运行状态"):
            st.rerun()
    else:
        st.success(f"运行已结束（退出码 {proc.returncode}），目录：{st.session_state.pending_dir}")
        st.session_state.pending_proc = None

st.divider()

# ---------- 实验汇总卡片 ----------
n_total = exp.get("planned_total")
ended_rows = [r for r in rows if r["status"] in ("answered", "refuse", "clarify", "error")]
red_rows = [r for r in rows if r["is_red"]]
yellow_rows = [r for r in rows if "🟡" in r["flag_label"]]
reviewed_rows = [r for r in rows if f"{exp['experiment_id']}|{r['query_id']}|{r.get('trial')}" in reviewed]
in_tok = sum(r.get("prompt_tokens") or 0 for r in rows)
out_tok = sum(r.get("completion_tokens") or 0 for r in rows)
cards = st.columns(7)
cards[0].metric("计划总数", n_total if n_total is not None else "未知")
cards[1].metric("已终止", len(ended_rows))
cards[2].metric("运行中/未启动",
                (n_total - len(ended_rows)) if n_total is not None else "未知")
cards[3].metric("🔴 bad case", len(red_rows))
cards[4].metric("🟡 过程警告", len(yellow_rows))
cards[5].metric("已审阅", len(reviewed_rows))
cards[6].metric("累计 token", f"{in_tok}/{out_tok}" if in_tok or out_tok else "未记录")
if n_total is None:
    st.caption("计划总数未知：该实验没有计划清单，仅显示已知条目。")

# ---------- 筛选与 query 表 ----------
st.subheader("本次实验：全部 query")
f1, f2, f3 = st.columns([1, 1, 2])
with f1:
    only_red = st.checkbox("仅 🔴 bad case")
with f2:
    status_filter = st.selectbox("状态", ["全部"] + sorted({r["status"] for r in rows}))
with f3:
    search = st.text_input("搜索 query")
table = rows
if only_red:
    table = [r for r in table if r["is_red"]]
if status_filter != "全部":
    table = [r for r in table if r["status"] == status_filter]
if search:
    table = [r for r in table if search.lower() in r["question"].lower()]
show = pd.DataFrame([{**{k: r.get(k) for k in ("query_id", "trial", "status", "latency_s",
                                               "prompt_tokens", "completion_tokens")},
                      "query 摘要": r["question"][:36],
                      "标记": r["flag_label"]} for r in table])
st.dataframe(show, use_container_width=True, hide_index=True)

# ---------- 单条详情 ----------
sel_q = st.selectbox("选中 query 详情",
                     [f"{r['query_id']}" + (f" (trial {r['trial']})" if r.get("trial") else "")
                      for r in table]) if table else None
if not sel_q:
    st.stop()
qid = sel_q.split(" ")[0]
row = next(r for r in table if r["query_id"] == qid)
st.caption(f"标记原因：{row['flag_label']}")

if row["source"].startswith("评测"):
    st.info("评测 run：完整 trace 见 package_path 下的 jsonl；结果以 summary.json 记录为准。")
    st.json({"correct": row.get("correct"), "detail": row.get("detail"), "status": row["status"]})
    st.stop()

pkg = Path(row["package_path"])
left, right = st.columns([1, 1])
with left:
    st.markdown("### 答案与依据")
    ans_p = pkg / "answer.md"
    if ans_p.exists():
        st.markdown(ans_p.read_text(encoding="utf-8"))
    else:
        st.warning("answer.md 缺失（运行未完成或记录不完整）")
    results = None
    rp = pkg / "results.json"
    if rp.exists():
        results = json.loads(rp.read_text(encoding="utf-8")).get("results") or []
    if row.get("verified") is not None:
        st.caption("核验：数字/引用规则" + ("通过（不代表经营归因正确）" if row["verified"] else "未通过"))
    if results:
        st.markdown("**claims 与来源**")
        for res in results:
            for c in (res.get("claims") or []):
                pass
    ans = pkg / "answer.md"
    if results and (pkg / "answer.md").exists():
        import re
        text = ans_p.read_text(encoding="utf-8")
        claims = []
        for m in re.finditer(r"- (.+) \[([-\d.,]+) (\w+) → (r\d+)\]", text):
            claims.append({"text": m.group(1), "value": m.group(2),
                           "unit": m.group(3), "rid": m.group(4)})
        if claims:
            st.dataframe(pd.DataFrame(claims), hide_index=True)
        for c in claims:
            target = next((res for res in results if res["id"] == c["rid"]), None)
            if target:
                with st.expander(f"rid={c['rid']}：参数 / SQL / 结果行"):
                    st.caption(f"工具：{target['tool']}  data_version={target['data_version']}")
                    st.json(target["args"])
                    if target.get("sql"):
                        st.code(target["sql"], language="sql")
                    st.dataframe(pd.DataFrame(target.get("rows") or []), hide_index=True)
with right:
    st.markdown("### 执行时间线")
    trace = pkg / "trace.jsonl"
    events = []
    if trace.exists():
        for ln in trace.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(ln))
            except Exception:
                pass
    rows_tl = []
    for i, e in enumerate(events, 1):
        t = e.get("type", "?")
        brief = {"meta": "运行元信息", "assistant": f"模型响应（step {e.get('step')}）",
                 "tool_result": f"工具 {e.get('tool')} " + ("成功" if e.get("ok") else "失败"),
                 "stop_reject": f"核验打回：{e.get('feedback', '')[:60]}",
                 "final": "最终提交", "result": "结果记录", "llm_retry": f"API 重试（{e.get('attempt')}）",
                 "error": f"错误：{e.get('error', '')[:60]}"}.get(t, t)
        rows_tl.append({"#": i, "事件": t, "说明": brief, "step": e.get("step")})
    st.dataframe(pd.DataFrame(rows_tl), hide_index=True, use_container_width=True)
    sel_ev = st.selectbox("选中事件详情", [f"#{i} {r['事件']}" for i, r in enumerate(rows_tl, 1)] or [None])
    if sel_ev:
        ev = events[int(sel_ev.split(" ")[0][1:]) - 1]
        st.json(ev)

st.divider()
st.subheader("人工审阅（独立于自动标红）")
rm_key = f"{exp['experiment_id']}|{qid}|{row.get('trial')}"
prev = reviewed.get(rm_key, {})
with st.form("review_form"):
    p1 = st.text_input("端到端质量", prev.get("端到端质量", "未评审"))
    p2 = st.text_input("取数准确性", prev.get("取数准确性", "未评审"))
    p3 = st.text_input("运行可靠性", prev.get("运行可靠性", "未评审"))
    if st.form_submit_button("保存人工审阅结论"):
        mark_reviewed(exp["experiment_id"], qid, row.get("trial"),
                      {"端到端质量": p1, "取数准确性": p2, "运行可靠性": p3})
        st.success("已保存（docs/evidence/review_marks.json，刷新不覆盖）")
