"""披露正文检索（运行时，只读）：读 etl/disclosures 生成的 data/disclosures/。

- 永远不写库；agent 只能通过 search_disclosure 工具按 query/公司/期间/top_k 检索。
- as_of 隔离（回答一步骤 2 硬要求）：as_of 给定时只返回 published_at ≤ as_of 的披露，
  防止历史评测泄漏未来文本；as_of 由运行上下文注入，不是工具参数（V4.5）。
- 引用可回溯：每条命中带 document_id + PDF 页码 + published_at；正文分块存
  Result Store 后可 recall 续读。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

_LAT_RE = re.compile(r"[a-z0-9]+")
_FY_RE = re.compile(r"^FY(\d{2}|\d{4})Q(\d)$", re.I)


def _tokens(text: str) -> list[str]:
    """英文按词（小写），CJK 按相邻双字组合（bigram）——无需分词器。"""
    t = text.lower()
    toks = _LAT_RE.findall(t)
    cjk = [ch for ch in t if "\u4e00" <= ch <= "\u9fff"]
    toks += [a + b for a, b in zip(cjk, cjk[1:])]
    toks += cjk
    return toks


def _fy_q(label: str | None) -> tuple[int, int] | None:
    if not label:
        return None
    m = _FY_RE.match(label.strip())
    if not m:
        raise ValueError(f"期间写法应为 FY24Q3/FY2024Q3，收到: {label}")
    y = int(m.group(1))
    return (2000 + y if y < 100 else y, int(m.group(2)))


@dataclass
class Corpus:
    root: Path
    docs: list[dict]
    _chunks: dict[str, list[dict]] = field(default_factory=dict)

    def chunks(self, document_id: str) -> list[dict]:
        if document_id not in self._chunks:
            p = self.root / "chunks" / f"{document_id}.jsonl"
            self._chunks[document_id] = [json.loads(ln) for ln in
                                         p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return self._chunks[document_id]


_CACHE: dict[tuple[str, float], Corpus] = {}


def load(disclosure_dir: str | Path) -> Corpus:
    root = Path(disclosure_dir)
    idx = root / "index.json"
    if not idx.exists():
        raise FileNotFoundError(
            f"披露库不存在: {idx}（先运行 .venv/Scripts/python -m etl.disclosures）")
    key = (str(root), idx.stat().st_mtime)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = Corpus(root=root, docs=json.loads(idx.read_text(encoding="utf-8"))["documents"])
    return _CACHE[key]


def search(corpus: Corpus, query: str, *, company: str | None = None,
           period: str | None = None, as_of: date | None = None,
           top_k: int = 5) -> list[dict[str, Any]]:
    qtoks = _tokens(query)
    if not qtoks:
        raise ValueError("query 为空或无有效词")
    fq = _fy_q(period)
    hits: list[tuple[float, dict]] = []
    for doc in corpus.docs:
        if company and doc["company"] != company:
            continue
        if fq and (doc["fiscal_year"], doc["fiscal_quarter"]) != fq:
            continue
        if as_of is not None and date.fromisoformat(doc["published_at"]) > as_of:
            continue                                    # as_of 之后的披露一律不可见
        for ch in corpus.chunks(doc["document_id"]):
            ct = _tokens(ch["text"])
            if not ct:
                continue
            score = sum(ct.count(tok) for tok in set(qtoks))
            if score:
                hits.append((score / (1 + len(ct) / 2000), {   # 长度阻尼，偏好浓缩页
                    "citation": ch["chunk_id"], "document_id": doc["document_id"],
                    "company": doc["company"], "doc_type": doc["doc_type"],
                    "fiscal_period": f"FY{doc['fiscal_year']}Q{doc['fiscal_quarter']}",
                    "period_end": doc["period_end"], "published_at": doc["published_at"],
                    "page": ch["page"], "score": round(score / (1 + len(ct) / 2000), 2),
                    "text": ch["text"]}))
    hits.sort(key=lambda x: (-x[0], x[1]["citation"]))
    return [h[1] for h in hits[:max(1, int(top_k))]]
