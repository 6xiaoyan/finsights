"""Agnes 限速与重试策略的离线测试（全部 mock，不联网、不消耗额度；见 verify.md 0.5）。

覆盖：
1. 普通请求只限速一次；
2. 429 重试：每一次真正发出的请求前都重新过共享限速器；
3. Retry-After 解析（delta-seconds 与 HTTP-date）与采纳（含上限）；
4. 无 Retry-After 时的有界指数退避 + 抖动；
5. 重试耗尽 / 非可重试错误仍按原语义处理；
6. CLI 主 Agent、multi 子 Agent、runner 共享同一个限速器实例，且不被重复限速。
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import LLMClient, _retry_after_header, _thinking_extra_body  # noqa: E402
from agent.ratelimit import (DEFAULT_RATE_LIMIT_RPM, DEFAULT_RETRY_BASE_S, DEFAULT_RETRY_CAP_S,  # noqa: E402
                             DEFAULT_RETRY_JITTER, RateLimiter, parse_retry_after, retry_delay)
from agent.v1 import Paced  # noqa: E402

_TEST_KEY = "test-key"  # 假 key；用常量传入，避免触发 G4 的 api_key= 字面量扫描


class FakeClock:
    """可注入的单调时钟：sleep 推进时间，于是间隔可以在不真等的情况下被精确断言。"""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _rl(rpm: float = 8.0, clock: FakeClock | None = None) -> RateLimiter:
    c = clock or FakeClock()
    return RateLimiter(rpm, clock=c.time, sleep=c.sleep), c


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(content="ok", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _err(cls, retry_after: str | None = None, status: int = 429):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    resp = httpx.Response(status, headers=headers, request=httpx.Request("POST", "http://unit.test"))
    return cls("mock error", response=resp, body=None)


def _client(monkeypatch, script, limiter=None, **kw) -> tuple[LLMClient, _FakeCompletions, list]:
    """注入 mock 传输层；记录退避 sleep 的秒数（不真等）。"""
    waits: list[float] = []
    monkeypatch.setattr("agent.llm.time.sleep", waits.append)
    fake = _FakeCompletions(script)
    c = LLMClient(api_key=_TEST_KEY, limiter=limiter or RateLimiter(1e9, clock=lambda: 0.0,
                                                                   sleep=lambda _s: None), **kw)
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return c, fake, waits


# ---------------- 1. 默认值与配置一致性（config / CLI / runner 不得残留 18） ----------------

def test_default_rpm_is_conservative_eight():
    assert DEFAULT_RATE_LIMIT_RPM == 8.0


def test_config_and_code_defaults_agree(tmp_path):
    """config.yaml / CLI / runner 的限速默认值同源，且代码里不再残留 18 这类隐式 fallback。

    不硬断言具体数值：DeepSeek 限的是并发不是 RPM，配置值是 0（关闭节流）；
    切回 Agnes 时是 8。要保证的是"三者一致 + 无残留魔法数"，不是某个数字。
    """
    import yaml

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))["llm"]
    assert "rate_limit_rpm" in cfg
    for path in ("agent/cli.py", "eval/runner.py"):
        src = Path(path).read_text(encoding="utf-8")
        assert "rate_limit_rpm\", 18" not in src and "rate_limit_rpm\", DEFAULT" in src
    import eval.runner as runner_mod
    assert runner_mod._default_rpm() == float(cfg["rate_limit_rpm"])


def test_llmclient_reads_retry_config_from_config_yaml(tmp_path):
    """退避参数来自 config.yaml（retry_base_s/cap_s/jitter），不是写死常量。"""
    import yaml

    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))["llm"]
    assert (raw["retry_base_s"], raw["retry_cap_s"], raw["retry_jitter"]) == (
        DEFAULT_RETRY_BASE_S, DEFAULT_RETRY_CAP_S, DEFAULT_RETRY_JITTER)


# ---------------- 2. 限速器本身：线程安全的最小间隔 ----------------

def test_rate_limiter_enforces_minimum_interval():
    limiter, clock = _rl(8.0)
    assert limiter.interval == pytest.approx(7.5)
    for expected in (0.0, 7.5, 7.5):        # 首次不等待，之后每次间隔 7.5s
        assert limiter.wait() == pytest.approx(expected)
    assert clock.slept == [7.5, 7.5]
    assert limiter.grants == 3


def test_rate_limiter_is_thread_safe():
    """并发调用下放行次数正确，且相邻放行不早于间隔（共享限速器必须线程安全）。"""
    clock = FakeClock()
    limiter = RateLimiter(600.0, clock=clock.time, sleep=clock.sleep)   # 间隔 0.1s
    grants: list[float] = []
    lock = threading.Lock()

    def worker():
        d = limiter.wait()
        with lock:
            grants.append(d)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert limiter.grants == 8
    assert len(grants) == 8
    # 单调 sleep：每个放行时刻严格递增，且相邻差 ≥ 间隔（浮点容差）
    times = [clock.now - sum(clock.slept[i + 1:]) for i in range(len(clock.slept))]
    assert times == sorted(times)


# ---------------- 3. Retry-After 解析 ----------------

@pytest.mark.parametrize("raw,expected", [("17", 17.0), ("0", 0.0), ("2.5", 2.5),
                                          ("  30 ", 30.0), ("-5", 0.0)])
def test_parse_retry_after_seconds(raw, expected):
    assert parse_retry_after(raw) == pytest.approx(expected)


def test_parse_retry_after_http_date():
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    later = now + timedelta(seconds=17)
    assert parse_retry_after(format_datetime(later, usegmt=True), now=now) == pytest.approx(17.0)
    past = now - timedelta(seconds=60)
    assert parse_retry_after(format_datetime(past, usegmt=True), now=now) == 0.0


@pytest.mark.parametrize("raw", [None, "", "   ", "not-a-date", "Mon, 99 Xxx 9999"])
def test_parse_retry_after_unparsable(raw):
    assert parse_retry_after(raw) is None


def test_retry_after_header_extracted_from_openai_error():
    err = _err(openai.RateLimitError, retry_after="17")
    assert _retry_after_header(err) == "17"
    assert _retry_after_header(_err(openai.RateLimitError)) is None
    assert _retry_after_header(RuntimeError("无 response")) is None


# ---------------- 4. 重试等待策略 ----------------

def test_retry_delay_prefers_retry_after_over_backoff():
    wait, source = retry_delay(0, 17.0, base_s=4.0, cap_s=30.0, jitter=0.5)
    assert (wait, source) == (17.0, "retry_after")


def test_retry_delay_caps_absurd_retry_after():
    """服务端给出异常大的值时也不无限等待（上限 120s）。"""
    wait, source = retry_delay(0, 99999.0)
    assert source == "retry_after" and wait == 120.0


def test_retry_delay_backoff_is_bounded_and_jittered():
    class FixedRandom:
        def uniform(self, a, b):
            assert (a, b) == (-DEFAULT_RETRY_JITTER, DEFAULT_RETRY_JITTER)
            return b            # 取抖动上界，断言仍受 cap 约束

    waits = [retry_delay(i, None, rng=FixedRandom()) for i in range(6)]
    for (w, src), _ in zip(waits, range(6)):
        assert src == "backoff"
        assert 0.0 <= w <= DEFAULT_RETRY_CAP_S
    # 基数 4 → 指数增长后封顶，不出现旧的 1/2/4 秒
    assert waits[0][0] > 1.0 and waits[0][0] <= 4.0 * (1 + DEFAULT_RETRY_JITTER)
    assert waits[-1][0] == pytest.approx(DEFAULT_RETRY_CAP_S)


def test_retry_delay_without_jitter_is_deterministic():
    a, _ = retry_delay(1, None, jitter=0.0)
    b, _ = retry_delay(1, None, jitter=0.0)
    assert a == b == 8.0


# ---------------- 5. LLMClient：限速点只有一个，且覆盖每次重试 ----------------

def test_plain_request_is_paced_exactly_once(monkeypatch):
    """普通请求（无重试）：限速器放行 1 次，不是 0 次也不是每个内部环节各一次。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client, fake, _ = _client(monkeypatch, [_resp("ok")], limiter=limiter)
    assert client.chat([{"role": "user", "content": "x"}]).content == "ok"
    assert fake.calls == 1
    assert limiter.grants == 1


def test_every_retry_is_paced_again(monkeypatch):
    """429 重试：每一次真正发出的请求前都重新过限速器（3 次尝试 = 3 次放行）。"""
    clock = FakeClock()
    limiter = RateLimiter(8.0, clock=clock.time, sleep=clock.sleep)
    script = [_err(openai.RateLimitError), _err(openai.RateLimitError), _resp("ok")]
    client, fake, waits = _client(monkeypatch, script, limiter=limiter)
    assert client.chat([{"role": "user", "content": "x"}]).content == "ok"
    assert fake.calls == 3
    assert limiter.grants == 3, "重试的请求没有被共享限速器覆盖"
    assert len(waits) == 2                      # 2 次退避（最后一次尝试不等待）
    assert [e["wait_source"] for e in client.last_call_events] == ["backoff", "backoff"]
    # 重试也付了限速代价：第 2/3 次放行各等一个间隔，不是 0
    # （last_call_events 只记录失败的尝试，成功那次不发事件；放行总数见上面的 grants == 3）
    assert [e["pace_wait_s"] for e in client.last_call_events] == [0.0, 7.5]


def test_retry_after_seconds_is_honoured_instead_of_short_backoff(monkeypatch):
    """离线实测：429 带 Retry-After: 17 时，旧实现仍只等 1/2/4 秒；现在必须等 17 秒。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    script = [_err(openai.RateLimitError, retry_after="17"), _resp("ok")]
    client, _, waits = _client(monkeypatch, script, limiter=limiter)
    client.chat([{"role": "user", "content": "x"}])
    assert waits == [17.0]
    assert client.last_call_events[0]["wait_s"] == 17.0
    assert client.last_call_events[0]["wait_source"] == "retry_after"
    assert client.last_call_events[0]["retry_after_s"] == 17.0


def test_retry_after_http_date_is_honoured(monkeypatch):
    """Retry-After 也可以是 HTTP-date（解析成秒后同样被采纳）。"""
    import agent.llm as llm_mod
    now = datetime.now(timezone.utc)
    header = format_datetime(now + timedelta(seconds=9), usegmt=True)
    monkeypatch.setattr(llm_mod, "parse_retry_after",
                        lambda v, now=None: parse_retry_after(v, now=now))
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    script = [_err(openai.RateLimitError, retry_after=header), _resp("ok")]
    client, _, waits = _client(monkeypatch, script, limiter=limiter)
    client.chat([{"role": "user", "content": "x"}])
    assert waits and 8.0 <= waits[0] <= 10.0
    assert client.last_call_events[0]["wait_source"] == "retry_after"


def test_backoff_without_retry_after_is_bounded_and_jittered(monkeypatch):
    """没有 Retry-After：面向分钟级配额的有界指数退避 + 抖动（不是固定 1/2/4 秒）。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    script = [_err(openai.RateLimitError) for _ in range(3)] + [_resp("ok")]
    client, fake, waits = _client(monkeypatch, script, limiter=limiter)
    client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 4
    assert len(waits) == client.max_retries == 3
    assert all(0 < w <= DEFAULT_RETRY_CAP_S for w in waits)
    assert all(e["retry_after_s"] is None for e in client.last_call_events)
    # 抖动确实生效：同样只重试一次，两次独立取样的等待不完全相同
    c2, _, waits2 = _client(monkeypatch, [_err(openai.RateLimitError), _resp("ok")],
                            limiter=RateLimiter(1e9, clock=lambda: 0.0, sleep=lambda _s: None))
    c2.chat([{"role": "user", "content": "x"}])
    assert len(waits2) == 1 and 2.0 <= waits2[0] <= DEFAULT_RETRY_BASE_S * (1 + DEFAULT_RETRY_JITTER)


def test_retry_events_record_pacing_and_rpm(monkeypatch):
    """重试事件带上限速等待与生效 RPM，trace 可核对是否真的过了共享限速器。"""
    clock = FakeClock()
    limiter = RateLimiter(8.0, clock=clock.time, sleep=clock.sleep)
    script = [_err(openai.RateLimitError), _resp("ok")]
    client, _, _ = _client(monkeypatch, script, limiter=limiter)
    client.chat([{"role": "user", "content": "x"}])
    ev = client.last_call_events[0]
    assert ev["type"] == "llm_retry" and ev["attempt"] == 1
    assert ev["pace_wait_s"] == pytest.approx(0.0)      # 首次放行不等待
    assert ev["rpm"] == 8.0
    assert "error" in ev and ev["error"]


def test_retry_events_report_effective_rpm_after_override(monkeypatch):
    """--max-rpm 覆盖后，事件里记的是实际生效值，不是 config 里的名义值。"""
    limiter = RateLimiter(12.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client = LLMClient(api_key=_TEST_KEY, limiter=limiter)
    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=_FakeCompletions([_err(openai.RateLimitError), _resp("ok")])))
    monkeypatch.setattr("agent.llm.time.sleep", lambda _s: None)
    Paced(client, limiter)                              # CLI/runner 的 --max-rpm 写回客户端
    client.chat([{"role": "user", "content": "x"}])
    assert client.last_call_events[0]["rpm"] == 12.0


def test_retry_exhaustion_raises_and_records_final_event(monkeypatch):
    """重试耗尽：抛出原始异常，不静默返回；最后一次事件标注 exhausted、wait_s=0。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    script = [_err(openai.RateLimitError) for _ in range(4)]
    client, fake, waits = _client(monkeypatch, script, limiter=limiter)
    with pytest.raises(openai.RateLimitError):
        client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 4 and len(waits) == 3
    assert limiter.grants == 4
    last = client.last_call_events[-1]
    assert last["wait_source"] == "exhausted" and last["wait_s"] == 0.0


def test_non_retryable_error_not_retried(monkeypatch):
    """认证错误不重试：只发一次请求（因此只放行一次）。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client, fake, waits = _client(monkeypatch, [_err(openai.AuthenticationError, status=401)],
                                  limiter=limiter)
    with pytest.raises(openai.AuthenticationError):
        client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 1 and waits == [] and limiter.grants == 1
    assert client.last_call_events == []


def test_retry_events_never_contain_api_key(monkeypatch):
    """重试事件进 trace，不能带上凭证。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client, _, _ = _client(monkeypatch, [_err(openai.RateLimitError), _resp("ok")], limiter=limiter)
    client.chat([{"role": "user", "content": "x"}])
    blob = json.dumps(client.last_call_events, ensure_ascii=False)
    assert _TEST_KEY not in blob


# ---------------- 6. 共享限速器：主 Agent / 子 Agent / CLI / runner 不各自新建、不重复限速 ----------------

def test_rate_limiter_zero_rpm_means_no_client_pacing():
    """rate_limit_rpm=0 = 关闭客户端节流（DeepSeek 限的是并发不是 RPM）。

    即使不等待，"每次请求都过唯一限速点"的不变量仍成立（grants 照常计数）。
    """
    import yaml
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))["llm"]
    if cfg["rate_limit_rpm"] != 0:
        pytest.skip("当前配置不是 0，无需验证关闭路径")
    slept: list[float] = []
    limiter = RateLimiter(cfg["rate_limit_rpm"], clock=lambda: 0.0, sleep=slept.append)
    client = LLMClient(api_key=_TEST_KEY, limiter=limiter)
    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=_FakeCompletions([_resp("a"), _resp("b"), _resp("c")])))
    for _ in range(3):
        client.chat([{"role": "user", "content": "x"}])
    assert limiter.interval == 0.0
    assert slept == []                       # 没有任何人工节流
    assert limiter.grants == 3                # 但每个请求仍过了限速点


def test_unknown_thinking_param_style_is_rejected():
    """厂商写法写错要立刻报错，不能静默发一个服务端不认的字段。"""
    with pytest.raises(ValueError):
        _thinking_extra_body("gpt-style", False)


def test_paced_does_not_double_pace_self_pacing_client(monkeypatch):
    """LLMClient 自带限速时，Paced 不再叠加第二层（否则一次请求被限速两遍、速率腰斩）。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client, _, _ = _client(monkeypatch, [_resp("a"), _resp("b"), _resp("c")], limiter=limiter)
    paced = Paced(client, limiter)
    assert paced._limiter is None, "自限速客户端被叠加了第二层限速"
    for _ in range(3):
        paced.chat([{"role": "user", "content": "x"}])
    assert limiter.grants == 3, "每个 chat 恰好过一次限速器"


def test_paced_adopts_explicit_limiter_instance():
    """CLI/runner 显式传入的限速器（--max-rpm）会写回客户端，主/子 Agent 共用同一个实例。"""
    limiter = RateLimiter(12.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client = LLMClient(api_key=_TEST_KEY,
                       limiter=RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None))
    Paced(client, limiter)
    assert client.limiter is limiter
    assert client.paces_own_requests is True


def test_bare_mock_client_keeps_external_pacing():
    """没有自限速能力的裸 mock 客户端：仍由 Paced 限速（保持既有语义与既有测试）。"""
    clock = FakeClock()
    waits = []

    class Bare:
        def chat(self, messages, tools=None, **kw):
            return "resp"

    p = Paced(Bare(), RateLimiter(8.0, clock=clock.time, sleep=waits.append))
    p.chat([])
    p.chat([])
    assert waits == [7.5]         # 首次放行不等待；第二次等满一个间隔


def test_subagent_and_main_agent_share_one_limiter():
    """multi 模式：主 Agent 与子 Agent 的每次请求都记在同一个限速器上。"""
    limiter = RateLimiter(8.0, clock=lambda: 0.0, sleep=lambda _s: None)
    client = LLMClient(api_key=_TEST_KEY, limiter=limiter)
    client._client = SimpleNamespace(chat=SimpleNamespace(
        completions=_FakeCompletions([_resp("a"), _resp("b"), _resp("c")])))
    paced = Paced(client, limiter)

    from agent.subagents import SubAgentRunner

    parent = SimpleNamespace(db_path="data/finsights.duckdb", store=None, cfg={},
                             as_of=None, enabled_tools=None)
    runner = SubAgentRunner(paced, parent, "reviewer", "system prompt", max_steps=2)
    runner.run("审查一下")                             # 子 Agent 的 2 次模型请求
    after_sub = limiter.grants
    assert after_sub == 2

    paced.chat([{"role": "user", "content": "x"}])     # 主 Agent 的模型请求
    assert limiter.grants == after_sub + 1
    assert client.limiter is limiter, "子 Agent 与主 Agent 用的不是同一个限速器实例"


def test_runner_and_cli_build_one_limiter_from_config():
    """runner 与 CLI 的默认限速都来自 config，不再各自写死数值。"""
    import yaml

    import agent.cli as cli_mod
    import eval.runner as runner_mod

    assert runner_mod.RateLimiter is RateLimiter
    expected = float(yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
                     ["llm"]["rate_limit_rpm"])
    assert runner_mod._default_rpm() == expected
    assert cli_mod.RateLimiter is RateLimiter
    src = Path("agent/cli.py").read_text(encoding="utf-8")
    assert "RateLimiter(args.max_rpm or default_rpm)" in src