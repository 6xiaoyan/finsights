"""共享 RPM 限速器与 429 重试等待策略。

实测背景（2026-10-09 小样本探测，记录 runs/rate_limit_probe_20261009.json）：
  8 RPM  8/8 成功；10 RPM 10/10 成功；12 RPM 前 10 次成功、第 11 次 429；
  429 响应**没有** Retry-After 头。
该样本与"约 10 RPM 有效上限"相符，但一次短探测不足以断言精确硬上限，因此默认取保守值
8 RPM（低于两次零失败的档位、留出余量）。实际取值一律从 config.yaml 的 llm.rate_limit_rpm 读，
本模块只提供缺省常量，代码里不再残留 18 之类的隐式 fallback。

**限速点只有一个**：LLMClient 在每次真正发出 API 请求之前调用 limiter.wait()，
包括它自己内部的 429 重试。调用方（agent.v1.Paced / agent.v0 / CLI / eval.runner）
共享同一个 limiter 实例，不允许再叠加第二层限速，否则实际速率会被腰斩。
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

DEFAULT_RATE_LIMIT_RPM = 8.0
"""config.yaml 缺 llm.rate_limit_rpm 时的保守默认值（agent/llm.py、agent/cli.py、eval/runner.py 共用）。"""

RETRY_AFTER_CAP_S = 120.0
"""采纳服务端 Retry-After 的上限：即使服务端给出异常大的值也不无限等待。"""

DEFAULT_RETRY_BASE_S = 4.0
"""无 Retry-After 时的退避基数。面向分钟级 RPM 配额窗口，比旧的 1s 起步更贴合。"""

DEFAULT_RETRY_CAP_S = 30.0
"""单次退避上限，保证等待有界（配合有限的 max_retries，总等待时间仍然有限）。"""

DEFAULT_RETRY_JITTER = 0.5
"""退避随机抖动幅度（±50%），避免多个进程/角色在同一个限额窗口上齐步重试。"""


class RateLimiter:
    """线程安全的最小间隔限速：相邻两次请求之间至少间隔 60/max_rpm 秒。

    时钟与 sleep 可注入，离线测试因此完全不需要真等（verify.md 0.5：pytest 全离线）。
    grants / total_wait_s 记录真实放行次数与累计等待，供运行包与测试核对
    "每一次实际发出的请求都过了限速器"。
    """

    def __init__(self, max_rpm: float = DEFAULT_RATE_LIMIT_RPM, *,
                 clock=time.monotonic, sleep=time.sleep):
        self.max_rpm = float(max_rpm)
        self.interval = 60.0 / self.max_rpm if self.max_rpm > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self.grants = 0          # 放行次数（含未产生等待的那些）
        self.total_wait_s = 0.0  # 累计实际等待秒数

    def wait(self) -> float:
        """阻塞到下一个可发送时刻，返回本次等待秒数（供 trace/测试记账）。"""
        with self._lock:
            now = self._clock()
            self._next = max(self._next, now)
            wake = self._next
            self._next += self.interval
            delay = max(0.0, wake - now)
            self.grants += 1
            self.total_wait_s += delay
        if delay > 0:
            self._sleep(delay)
        return delay


def parse_retry_after(value, now: datetime | None = None) -> float | None:
    """把 Retry-After 头解析成等待秒数。

    支持 delta-seconds（"17"）与 HTTP-date（"Wed, 21 Oct 2026 07:28:00 GMT"）；
    空值或无法解析返回 None，交给退避策略。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:                                    # delta-seconds
        return max(0.0, float(text))
    except (TypeError, ValueError):
        pass
    try:                                    # HTTP-date
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (when - ref).total_seconds())


def retry_delay(attempt: int, retry_after_s: float | None, *,
                base_s: float = DEFAULT_RETRY_BASE_S,
                cap_s: float = DEFAULT_RETRY_CAP_S,
                jitter: float = DEFAULT_RETRY_JITTER,
                rng=None) -> tuple[float, str]:
    """返回 (等待秒数, 来源)。`attempt` 从 0 开始。

    - 有 Retry-After：直接采用（上限 RETRY_AFTER_CAP_S），不叠加本地退避——服务端比本地
      更清楚配额窗口，本地猜 1/2/4 秒只会连续撞同一个窗口。
    - 无 Retry-After：面向分钟级 RPM 限额的有界指数退避 + 抖动（base 4s → 8s → 16s，
      上限 cap），避免固定短间隔反复打在同一窗口上。
    """
    if retry_after_s is not None:
        return min(max(0.0, retry_after_s), RETRY_AFTER_CAP_S), "retry_after"
    r = rng or random
    exp = min(base_s * (2 ** max(0, attempt)), cap_s)
    if jitter:
        exp *= 1.0 + r.uniform(-jitter, jitter)
    return min(max(0.0, exp), cap_s), "backoff"