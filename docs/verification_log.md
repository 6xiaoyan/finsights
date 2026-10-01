# 验证日志

> 记录格式见 verify.md 0.2。每个检查项一行，证据必须是真实执行的命令和输出摘要。

## P0 环境与骨架

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V0.1 | PASS | `git check-ignore .env data/finsights.duckdb data/snapshots/x runs/x data/raw/sec/x.json` → 4 个计划内路径全部输出（另将 `data/raw/` 整体纳入忽略，因 SEC JSON 体积大且可由 etl 重下） | e23ddd1 |
