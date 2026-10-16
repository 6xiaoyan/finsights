"""PDF 页码索引：把数值锚定到公告 PDF 的具体页面（PRD §5.2 来源可追溯）。

用与 ETL 相同的 pypdfium2 文本提取，扫描公告 PDF，返回包含指定千美元/百万美元
数值的页码。用于构建来源锚点与 `verify-sources` 复核，避免"数字属于该页该期间"没有核对。
"""
from __future__ import annotations

import functools
from pathlib import Path

RAW_DIR = Path("data/raw/lenovo")


def thousands(value_mn: float) -> str:
    """DB 单位为百万美元，公告为千美元：转成千美元字符串（不带货币号）。"""
    return f"{round(value_mn * 1000):,}"


@functools.lru_cache(maxsize=64)
def page_texts(pdf_path: str) -> tuple[str, ...]:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return tuple(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()


def find_pages(pdf_name: str, needle: str, raw_dir: Path = RAW_DIR) -> list[int]:
    """返回包含 needle 的 1-based 页码列表；PDF 不存在返回 []。"""
    path = raw_dir / pdf_name
    if not path.exists():
        return []
    return [i + 1 for i, t in enumerate(page_texts(str(path))) if needle in t]


def find_value_page(pdf_name: str, value_mn: float, raw_dir: Path = RAW_DIR) -> int | None:
    """返回包含该数值（千美元）的首个页码；找不到返回 None。"""
    pages = find_pages(pdf_name, thousands(value_mn), raw_dir)
    return pages[0] if pages else None
