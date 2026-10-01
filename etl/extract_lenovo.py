"""P1：从联想业绩公告 PDF 抽取综合资产负债表与损益表关键行 → data/manual/lenovo_*.csv。

结构事实（2026-10-01 实测）：
- 资产负债表：两页（資產頁 + 續頁），两列数字（期末、上财年末）；小节：非流動資產/流動資產/權益/非流動負債/流動負債；
  權益小节在續頁无标题行，以"股本"行开始，至"非流動負債"标题为止。
- 损益表：Q1/Q3 两列（本期止三個月、上年同期）；Q2 中期四列（本期三個月、本期六個月、上年三個月、上年六個月）→ 取第 1 列即单季；
  Q4 年报两列（本财年、上财年）→ 全年数，Q4 由 load 阶段倒算（plan 3.2.2）。
- 数值列取"行内最右 N 个数值 token"，附註号（如 7(a)、12）在数值前，用倒序扫描自然跳过；"-"为空值。
- 单位：千美元（入库时由 load_db.py 换算百万美元）。

校验（脚本内自动执行，全部通过才视为抽取成功）：
1) 每期 資產 = 負債 + 權益（合计行）；
2) 各小节"已映射科目之和 = 小计行"（容差 0，四舍五入误差 < 1 千美元）；
3) 毛利 = 收入 − 銷售成本；
4) 相邻财年交叉验证：下一财年 Q1 公告的"上财年末"列 == 本财年年报的期末列（两份不同 PDF 的同一事实）。
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path

import pdfplumber
import yaml

MAP_PATH = Path("etl/account_map.yaml")
MANIFEST = Path("data/manual/lenovo_manifest.json")
RAW_DIR = Path("data/raw/lenovo")
OUT_BS = Path("data/manual/lenovo_balance_sheet.csv")
OUT_IS = Path("data/manual/lenovo_income_statement.csv")

# 值 token：括号负数（容忍内部空格）、独立破折号（空值位）、数字组（负向断言防切断 13,119）
VAL_RE = re.compile(r"\(\s*-?[\d,]+\s*\)|[‐–—-]|(?<![\d,])(?<![\d,.])[\d,]+(?![\d,])")
DASHES = {"-", "–", "—", "‐"}


def _is_note(tok: str) -> bool:
    """附註号：以数字开头且含字母，如 7(a)、9(a)(ii)、11(a)(ii),12(b)。"""
    return bool(re.fullmatch(r"\d[\da-z(),，]*", tok)) and bool(re.search(r"[a-z]", tok))


def _load_map() -> dict:
    """NFKC 归一化映射键（PDF 里存在兼容码位，如 U+FA68 的"金"）。"""
    raw = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    return _nfkc_all(raw)


def _nfkc_all(obj):
    if isinstance(obj, str):
        return unicodedata.normalize("NFKC", obj)
    if isinstance(obj, list):
        return [_nfkc_all(x) for x in obj]
    if isinstance(obj, dict):
        return {_nfkc_all(k): _nfkc_all(v) for k, v in obj.items()}
    return obj


def _to_num(tok: str) -> float | None:
    """'(212,900)' → -212900.0；'-'/'–' → None；容忍数字内空格（PDF 排版噪声）。"""
    tok = tok.strip()
    if tok in DASHES:
        return None
    neg = tok.startswith("(") and tok.endswith(")")
    if neg:
        tok = tok[1:-1]
    v = float(tok.replace(" ", "").replace(",", ""))
    return -v if neg else v


def _split_line(line: str, n_cols: int) -> tuple[str, list[float | None]]:
    """正则扫描取最右 n_cols 个值 token（'-' 记 None），其余为标签并剥离行尾附註号。"""
    # 修复 PDF 断字："4,810,75 1" → "4,810,751"（仅当逗号后为不完整组 1-2 位且空格后是 1-2 位尾巴）
    line = re.sub(r"(\d,\d{1,2}) (?=\d{1,2}(?![\d,]))", r"\1", line)
    ms = list(VAL_RE.finditer(line))
    if not ms:
        return "".join(line.split()), []
    taken = ms[-n_cols:]
    vals = [_to_num(m.group(0)) for m in taken]
    label_toks = line[: taken[0].start()].split()
    while label_toks and (_is_note(label_toks[-1]) or re.fullmatch(r"\d{1,2}", label_toks[-1])):
        label_toks.pop()  # 行尾附註号（含纯数字小号；值列均在右侧已被取走）
    return "".join(label_toks), vals


def _period_end(fy_tag: str, quarter: str) -> str:
    fy = int(fy_tag[2:])  # FY18 → 18，财年止 2018-03-31
    return {"Q1": f"{fy - 1}-06-30", "Q2": f"{fy - 1}-09-30", "Q3": f"{fy - 1}-12-31", "Q4": f"{fy}-03-31"}[quarter]


def _find_pages(pdf) -> tuple[int, int, int] | None:
    """返回 (资产页idx, 负债页idx, 损益页idx)，从前往后找第一处综合报表。"""
    bs_main = bs_cont = is_page = None
    for idx, page in enumerate(pdf.pages):
        t = page.extract_text() or ""
        if bs_main is None and "綜合資產負債表" in t:
            bs_main = idx
            continue
        if bs_main is not None and bs_cont is None and "綜合資產負債表" in t and "續" in t:
            bs_cont = idx
            continue
        if is_page is None and "綜合損益表" in t:
            is_page = idx
        if bs_main is not None and bs_cont is not None and is_page is not None:
            break
    if bs_main is None or bs_cont is None or is_page is None:
        return None
    return bs_main, bs_cont, is_page


def _is_n_cols(header_text: str) -> int:
    """损益表列数：数表头里所有"止X個月"（三/六/九/十二個月）。"""
    return max(len(re.findall(r"止[一二三四五六七八九十百]+個月", header_text)), 2)


def _parse_bs_pages(pdf, amap: dict, bs_main: int, bs_cont: int) -> tuple[list[dict], list[str]]:
    """换行感知解析：长标签/總計标签单独成行时，用 pending 缓冲与下一行拼接。"""
    rows: list[dict] = []
    warnings: list[str] = []
    sec_map = amap["lenovo_bs_sections"]
    totals_map = amap["lenovo_bs_totals"]
    ignored = set(amap.get("lenovo_bs_ignored", []))
    pending = ""
    for page_idx, state0 in ((bs_main, "非流動資產"), (bs_cont, "權益")):
        text = unicodedata.normalize("NFKC", pdf.pages[page_idx].extract_text() or "")
        state = state0
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("綜合資產負債表") or "千美元" in line or "未經審核" in line:
                continue
            if re.fullmatch(r"[二零〇一二三四五六七八九十 /（）()同止期月日年]+", line):
                continue  # 日期表头
            if re.fullmatch(r"\d{1,3}", line):
                continue  # 页脚页码
            if line in ("非流動資產", "流動資產", "非流動負債", "流動負債"):
                state = line
                pending = ""
                continue
            label, vals = _split_line(line, 2)
            if not vals:
                pending += label  # 纯标签行：换行标签或单独成行的總計标签
                continue
            full = pending + label
            pending = ""
            if full in totals_map:
                rows.append({"section": "合计", "line": full, "account": totals_map[full], "value_k": vals[0]})
            elif full in sec_map.get(state, {}):
                rows.append({"section": state, "line": full, "account": sec_map[state][full], "value_k": vals[0]})
            elif full in ignored:
                continue
            elif full == "":
                if len(vals) == 2 and all(v is not None for v in vals):
                    rows.append({"section": state, "line": f"[{state}小计]", "account": f"_subtotal_{state}", "value_k": vals[0]})
                else:
                    warnings.append(f"BS 孤立数值行[{state}]: {vals}")
            else:
                warnings.append(f"BS 未映射行[{state}]: {full[:44]} ({vals[0]})")
    return rows, warnings


def _parse_is_page(pdf, amap: dict, is_page: int, tag: str) -> tuple[list[dict], list[str], str]:
    text = unicodedata.normalize("NFKC", pdf.pages[is_page].extract_text() or "")
    lines = text.splitlines()
    header = "\n".join(lines[:8])
    n_cols = _is_n_cols(header)
    period_type = "12m" if ("止十二個月" in header or "止年度" in header) else "3m"
    rows: list[dict] = []
    warnings: list[str] = []
    is_map = {k.replace(" ", ""): v for k, v in amap["lenovo_is"].items()}
    for raw in lines:
        line = raw.strip()
        if not line or "千美元" in line or "附註" in line or line.startswith("綜合損益表"):
            continue
        if "美仙" in line or "每股" in line or "股息" in line:
            continue  # EPS/股息行不抽取
        if re.fullmatch(r"[二零〇一二三四五六七八九十 /（）()同止期月日年]+", line):
            continue
        label, vals = _split_line(line, n_cols)
        code = is_map.get(label)
        if code and vals and vals[0] is not None:
            rows.append({"line": label, "account": code, "value_k": abs(vals[0])})
        elif code and vals and vals[0] is None:
            warnings.append(f"IS {tag} {label}: 本期值为空")
    return rows, warnings, period_type


def main() -> int:
    amap = _load_map()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bs_rows: list[list] = []
    is_rows: list[list] = []
    problems: list[str] = []
    # fy_end_values[fy] = {account: value}，供相邻财年交叉验证
    fy_end_from_annual: dict[str, dict[str, float]] = {}
    fy_end_from_next_q1: dict[str, dict[str, float]] = {}

    for m in manifest:
        fy, q, fname = m["fy"], m["quarter"], m["file"]
        pdf_path = RAW_DIR / fname
        with pdfplumber.open(pdf_path) as pdf:
            pages = _find_pages(pdf)
            if pages is None:
                problems.append(f"{fname}: 找不到三张报表页")
                continue
            bs_main, bs_cont, is_page = pages
            bs, warn = _parse_bs_pages(pdf, amap, bs_main, bs_cont)
            problems += [f"{fname}: {w}" for w in warn]
            # 校验 1：A = L + E
            tot = {r["account"]: r["value_k"] for r in bs if r["section"] == "合计"}
            if {"total_assets", "total_liabilities", "total_equity"} <= tot.keys():
                if abs(tot["total_assets"] - tot["total_liabilities"] - tot["total_equity"]) > 1:
                    problems.append(f"{fname}: A≠L+E ({tot})")
                if "total_liabilities_and_equity" in tot and abs(tot["total_liabilities_and_equity"] - tot["total_assets"]) > 1:
                    problems.append(f"{fname}: L+E合计 ≠ 总资产")
            else:
                problems.append(f"{fname}: 合计行不全 {list(tot)}")
            # 校验 2：小节组件和 = 小计行
            comp: dict[str, float] = {}
            for r in bs:
                if r["account"].startswith("_subtotal_"):
                    continue
                comp[r["section"]] = comp.get(r["section"], 0) + (r["value_k"] or 0)
            for r in bs:
                if r["account"].startswith("_subtotal_"):
                    sec = r["account"].replace("_subtotal_", "")
                    if sec in comp and abs(comp[sec] - r["value_k"]) > 1:
                        problems.append(f"{fname}: {sec} 组件和 {comp[sec]} ≠ 小计 {r['value_k']}")
            for r in bs:
                bs_rows.append([fy, q, _period_end(fy, q), r["section"], r["line"], r["account"], r["value_k"], fname])
            # 若为年报，记录期末值（供与下一财年 Q1 交叉验证）
            if q == "Q4":
                fy_end_from_annual[fy] = {r["account"]: r["value_k"] for r in bs if not r["account"].startswith("_subtotal_")}
            # 损益表
            isr, warn2, ptype = _parse_is_page(pdf, amap, is_page, f"{fy}{q}")
            problems += [f"{fname}: {w}" for w in warn2]
            by_acct = {r["account"]: r["value_k"] for r in isr}
            if {"revenue", "cogs", "gross_profit"} <= by_acct.keys():
                if abs(by_acct["gross_profit"] - (by_acct["revenue"] - by_acct["cogs"])) > 1:
                    problems.append(f"{fname}: 毛利 ≠ 收入−成本 ({by_acct})")
            else:
                problems.append(f"{fname}: IS 关键行不全 {list(by_acct)}")
            for r in isr:
                is_rows.append([fy, q, _period_end(fy, q), ptype, r["line"], r["account"], r["value_k"], fname])

    # 校验 4：下一财年 Q1 资产负债表第 2 列（上财年末） vs 本财年年报期末列
    # （第 2 列在 _parse_bs_pages 中未保留，这里用独立轻量解析补一次）
    for m in manifest:
        if m["quarter"] != "Q1":
            continue
        prev_fy = f"FY{int(m['fy'][2:]) - 1}"
        if prev_fy not in fy_end_from_annual:
            continue
        with pdfplumber.open(RAW_DIR / m["file"]) as pdf:
            pages = _find_pages(pdf)
            if pages is None:
                continue
            vals = {}
            for page_idx in (pages[0], pages[0] + 1):
                t = unicodedata.normalize("NFKC", pdf.pages[page_idx].extract_text() or "")
                for raw in t.splitlines():
                    line = raw.strip()
                    if line.startswith(("總資產", "總負債", "總權益")):
                        label, v = _split_line(line, 2)
                        if len(v) == 2:
                            vals[amap["lenovo_bs_totals"].get(label, label)] = v[1]
            for code, v in vals.items():
                ref = fy_end_from_annual[prev_fy].get(code)
                if ref is not None and abs(ref - v) > 1:
                    problems.append(f"{m['file']}: 上财年末列 {code}={v} ≠ {prev_fy} 年报 {ref}")

    # 写 CSV
    OUT_BS.write_text("", encoding="utf-8")
    with OUT_BS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fy", "quarter", "period_end", "section", "line_item_zh", "account_code", "value_k_usd", "source_pdf"])
        w.writerows(bs_rows)
    with OUT_IS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fy", "quarter", "period_end", "period_type", "line_item_zh", "account_code", "value_k_usd", "source_pdf"])
        w.writerows(is_rows)

    print(f"BS 行数: {len(bs_rows)}  IS 行数: {len(is_rows)}")
    print(f"校验问题: {len(problems)}")
    for p in problems[:30]:
        print("  -", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
