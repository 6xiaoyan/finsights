"""ETL 纯函数单元测试（plan 0 规则 7：每个模块都要有 pytest 测试）。"""
from __future__ import annotations

from datetime import date

from etl.load_db import (
    days_since_prev_pe,
    fiscal_quarter,
    fy_anchor_for,
    q4_backout,
    quarter_dates,
    select_versions,
)


def test_select_versions_original_and_restated():
    """V1.15 逻辑：最早 filed = original；更晚且值不同 = restated；同值跳过。"""
    entries = [
        {"val": 100.0, "filed": date(2024, 3, 1)},
        {"val": 105.0, "filed": date(2023, 11, 1)},  # 更早 filed，乱序输入
        {"val": 100.0, "filed": date(2024, 5, 1)},   # 同值重报 → 跳过
        {"val": 108.0, "filed": date(2024, 8, 1)},   # 重述
    ]
    out = select_versions(entries)
    assert [(o["version"], o["val"]) for o in out] == [("original", 105.0), ("restated", 100.0), ("restated", 108.0)]


def test_fiscal_quarter_52_53_week_drift():
    """Dell 52/53 周财年：期末日在月中漂移，天数法不错位。"""
    # Dell FY2026 锚 = 2026-01-30；FY2026Q2 期末 2025-08-01（距上年锚 183 天）
    assert fiscal_quarter(date(2025, 8, 1), date(2026, 1, 30)) == 2
    # Dell FY2020 锚 = 2020-01-31；FY2020Q3 期末 2019-11-01
    assert fiscal_quarter(date(2019, 11, 1), date(2020, 1, 31)) == 3
    # HP 锚 = 2024-10-31
    assert fiscal_quarter(date(2024, 1, 31), date(2024, 10, 31)) == 1
    assert fiscal_quarter(date(2024, 4, 30), date(2024, 10, 31)) == 2
    assert fiscal_quarter(date(2024, 10, 31), date(2024, 10, 31)) == 4


def test_fy_anchor_implied_for_latest_fy():
    """最新财年 Q1–Q3 尚无年报锚 → 用最后锚 +1 年的隐含锚。"""
    anchors = [date(2024, 10, 31), date(2025, 10, 31)]
    a = fy_anchor_for(date(2026, 7, 31), anchors)  # HP FY2026Q3，真锚 2026-10-31 未出现
    assert a == date(2026, 10, 31)
    assert fiscal_quarter(date(2026, 7, 31), a) == 3


def test_q4_backout():
    """Q4-2 答复：Q4 = 全年 − 9M YTD（Dell FY2017Q4 实测值 20,074 = 61,642 − 41,568）。"""
    assert q4_backout(500.0, 330.0) == 170.0
    assert q4_backout(61642.0, 41568.0) == 20074.0


def test_days_since_prev_pe():
    """Q7-2：days = 本期期末 − 上期期末（含 52/53 周长季度）。"""
    assert days_since_prev_pe(date(2017, 2, 3), date(2016, 10, 28)) == 98   # Dell FY2017Q4（14 周）
    assert days_since_prev_pe(date(2018, 3, 31), date(2017, 12, 31)) == 90  # 联想 FY18Q4
    assert days_since_prev_pe(date(2024, 10, 31), date(2024, 7, 31)) == 92  # HP Q4


def test_quarter_dates_span():
    periods = {(2024, 4): date(2024, 10, 31), (2025, 1): date(2025, 1, 31), (2025, 2): date(2025, 4, 30)}
    qd = quarter_dates(periods)
    assert qd[(2025, 1)] == (date(2024, 11, 1), date(2025, 1, 31))
    assert qd[(2025, 2)] == (date(2025, 2, 1), date(2025, 4, 30))


def test_quarter_dates_accepts_dict_values():
    periods = {(2024, 4): {"pe": date(2024, 10, 31)}, (2025, 1): {"pe": date(2025, 1, 31)}}
    qd = quarter_dates(periods)
    assert qd[(2025, 1)][1] == date(2025, 1, 31)
