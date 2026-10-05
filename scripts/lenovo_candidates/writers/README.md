# 抽取工作区归档（.cache/candidates 的被忽略副本）

- `write_fy*.py`：各公告的候选 writer（FY24Q2–FY25Q3 批次；更早批次的 writer 未保留，
  其输出以本目录 `FY*.jsonl` 缓存为准）。
- `FY*.jsonl`：按公告缓存的候选行（build_candidates.py 的输入）。恢复工作区：
  `cp scripts/lenovo_candidates/writers/FY*.jsonl scripts/lenovo_candidates/writers/meta.json .cache/candidates/`
- `apply_selfcheck_fixups.py` / `...2.py`：交付前自查修正（幂等，直接在 jsonl 缓存上重放）。
- `meta.json`：case_id → tags/difficulty_rationale，合并时注入。
