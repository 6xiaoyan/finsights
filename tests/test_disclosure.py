"""Q11 回答一步骤 2 离线验收：披露索引/检索 + as_of 隔离 + A2 运行时接线。全部离线（不落 PDF）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import disclosure  # noqa: E402
from agent.result_store import ResultStore  # noqa: E402
from agent.tools import data as td  # noqa: E402
from agent.tools.data import ToolContext, execute  # noqa: E402

DB = "data/finsights.duckdb"


def _doc(doc_id, fy, q, published, end):
    return {"document_id": doc_id, "company": "Lenovo", "fiscal_year": fy, "fiscal_quarter": q,
            "period_end": end, "period_start": None, "calendar_quarter": None, "days": None,
            "doc_type": "quarterly_results_announcement", "published_at": published,
            "published_at_basis": "test", "source_file": None, "sha256": None,
            "extraction": "test", "n_chunks": 2}


@pytest.fixture()
def corpus_dir(tmp_path):
    docs = [_doc("lenovo_FY2022Q4", 2022, 4, "2022-11-04", "2022-06-30"),
            _doc("lenovo_FY2023Q3", 2023, 3, "2023-02-17", "2022-12-31")]
    chunks = {
        "lenovo_FY2022Q4": [
            {"chunk_id": "lenovo_FY2022Q4#p2", "page": 2, "text": "Revenue growth slowed; "
             "inventory levels remained elevated across the industry."},
            {"chunk_id": "lenovo_FY2022Q4#p9", "page": 9, "text": "分部信息：基础设施方案业务集团收入环比下降。"}],
        "lenovo_FY2023Q3": [
            {"chunk_id": "lenovo_FY2023Q3#p2", "page": 2, "text": "管理层讨论：终端市场疲弱，"
             "渠道库存过剩，公司积极推动渠道去库存，存货较上季显著下降。"},
            {"chunk_id": "lenovo_FY2023Q3#p11", "page": 11, "text": "Three-month segment revenue "
             "and eliminations table. Inventory reduction driven by soft demand."}],
    }
    (tmp_path / "chunks").mkdir()
    (tmp_path / "index.json").write_text(
        json.dumps({"documents": docs}, ensure_ascii=False), encoding="utf-8")
    for k, v in chunks.items():
        (tmp_path / "chunks" / f"{k}.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in v) + "\n", encoding="utf-8")
    return tmp_path


def _corpus(corpus_dir):
    disclosure._CACHE.clear()
    return disclosure.load(corpus_dir)


def test_asof_hides_future_disclosures(corpus_dir):
    c = _corpus(corpus_dir)
    hits = disclosure.search(c, "inventory", as_of=date(2022, 12, 1))
    assert hits and all(h["document_id"] == "lenovo_FY2022Q4" for h in hits)
    hits2 = disclosure.search(c, "inventory", as_of=date(2023, 3, 1))
    assert {h["document_id"] for h in hits2} == {"lenovo_FY2022Q4", "lenovo_FY2023Q3"}


def test_cjk_and_english_queries_with_filters(corpus_dir):
    c = _corpus(corpus_dir)
    hits = disclosure.search(c, "渠道去库存", as_of=date(2023, 3, 1))
    assert hits[0]["citation"] == "lenovo_FY2023Q3#p2"
    assert hits[0]["published_at"] == "2023-02-17" and hits[0]["page"] == 2
    only_q3 = disclosure.search(c, "inventory", period="FY23Q3", as_of=date(2023, 3, 1))
    assert only_q3 and all(h["fiscal_period"] == "FY2023Q3" for h in only_q3)
    Dell = disclosure.search(c, "inventory", company="Dell", as_of=date(2023, 3, 1))
    assert Dell == []                                   # 库里只有联想，不能凭空出别家
    assert disclosure.search(c, "inventory", top_k=1, as_of=date(2023, 3, 1))
    with pytest.raises(ValueError):
        disclosure._fy_q("2023Q3")                     # 财季必须 FY 前缀，防止口径混用


def test_execute_tool_routes_and_stores(corpus_dir, monkeypatch):
    monkeypatch.setattr(disclosure, "_CACHE", {})
    ctx = ToolContext(db_path=DB, store=ResultStore("test"),
                      cfg={"disclosure_dir": str(corpus_dir)}, as_of=date(2023, 3, 1))
    o = execute("search_disclosure", {"query": "去库存", "top_k": 3}, ctx)
    assert o.ok and o.rid and o.stored is not None
    assert {"citation", "published_at", "text"} <= set(o.stored.df.columns)
    # as_of 之前的未来文档被过滤：只可能来自 FY2023Q3（FY2022Q4 也在 as_of 前，两家都可出现）
    ctx2 = ToolContext(db_path=DB, store=ResultStore("test"),
                       cfg={"disclosure_dir": str(corpus_dir)}, as_of=date(2022, 12, 1))
    o2 = execute("search_disclosure", {"query": "inventory"}, ctx2)
    assert set(o2.stored.df["document_id"]) == {"lenovo_FY2022Q4"}
    o3 = execute("search_disclosure", {"query": "库存", "period": "BAD"}, ctx)
    assert not o3.ok and "FY" in o3.text               # 参数错误以工具错误文本回给模型


def test_make_run_ctx_shares_comp_and_recall_unstubs():
    import pandas as pd
    from agent.loop import make_run_ctx
    ctx = make_run_ctx(DB, llm=None, as_of=date(2026, 10, 1))
    assert ctx.tool_ctx.as_of == date(2026, 10, 1)
    assert ctx.tool_ctx.comp is ctx.comp               # A2 修复前 tool_ctx.comp 从未挂接（死代码）
    res = ctx.store.put("query_metric", {"metric": "revenue"},
                        pd.DataFrame({"a": [1, 2]}), step=0)
    ctx.comp.stubbed = frozenset({res.id})
    o = execute("recall", {"result_id": res.id, "offset": 0}, ctx.tool_ctx)
    assert o.ok
    assert res.id not in ctx.comp.stubbed              # 召回后恢复完整内容，不再立即重新存根
    assert res.id in ctx.comp.active_rids              # 进入活动保护集
