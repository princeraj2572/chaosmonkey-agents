"""Deterministic baseline plus one-fault-per-run chaos experiments."""

from __future__ import annotations

import copy
import inspect
import random
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from .faults import FAULTS, ToolFault
from .judge import Judgement, Outcome, classify, judge
from .wrap import ChaosPlan, ChaosSession


@dataclass
class TraceEvent:
    step: int
    kind: str
    name: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "kind": self.kind, "name": self.name, "detail": self.detail, "data": _jsonable(self.data)}


@dataclass
class RunRecord:
    mode: str
    seed: int | None
    output: Any = None
    status: str = ""
    error: str | None = None
    duration_ms: float = 0.0
    trace: list[TraceEvent] = field(default_factory=list)
    judgement: Judgement | None = None
    fault: str | None = None
    call_index: int | None = None
    injected: bool = False
    tool_records: list[Any] = field(default_factory=list)

    @property
    def outcome(self) -> Outcome:
        return self.judgement.outcome if self.judgement else Outcome.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        output = _jsonable(self.output)
        transcript = _jsonable(getattr(self.output, "messages", None))
        if isinstance(output, Mapping) and transcript is not None:
            output = dict(output)
            output.setdefault("transcript", transcript)
        return {
            "mode": self.mode, "seed": self.seed, "fault": self.fault,
            "call_index": self.call_index, "injected": self.injected,
            "output": output, "status": self.status, "error": self.error,
            "duration_ms": round(self.duration_ms, 3),
            "outcome": self.outcome.value,
            "judgement": self.judgement.to_dict() if self.judgement else None,
            "tool_records": [_jsonable(record) for record in self.tool_records],
            "trace": [event.to_dict() for event in self.trace],
        }


@dataclass
class ExperimentResult:
    example: str
    runs: list[RunRecord]
    seeds: list[int]
    fault_rate: float = 0.35
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def baseline(self) -> RunRecord | None:
        return next((run for run in self.runs if run.mode == "baseline"), None)

    @property
    def chaos_runs(self) -> list[RunRecord]:
        return [run for run in self.runs if run.mode != "baseline"]

    @property
    def counts(self) -> dict[str, int]:
        counts = {outcome.value: 0 for outcome in Outcome}
        for run in self.runs:
            counts[run.outcome.value] += 1
        return counts

    @property
    def silent_failure_rate(self) -> float:
        injected = [run for run in self.chaos_runs if run.injected]
        if not injected:
            return 0.0
        return sum(run.outcome is Outcome.SILENT_FAILURE for run in injected) / len(injected)

    def worst_runs(self, limit: int = 3) -> list[RunRecord]:
        return sorted(
            self.chaos_runs,
            key=lambda run: (-(run.judgement.severity if run.judgement else 0), run.seed if run.seed is not None else -1),
        )[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "example": self.example, "seeds": self.seeds, "fault_rate": self.fault_rate,
            "metadata": self.metadata, "counts": self.counts,
            "silent_failure_rate": self.silent_failure_rate,
            "runs": [run.to_dict() for run in self.runs],
        }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if hasattr(value, "value") and not callable(value.value):
        return _jsonable(value.value)
    if hasattr(value, "__dict__"):
        return {key: _jsonable(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


class PlannedTools(Mapping[str, Callable[..., Any]]):
    """Expose a mapping or object through one shared :class:`ChaosSession`."""

    def __init__(self, tools: Any, session: ChaosSession, trace: list[TraceEvent]):
        self._tools = tools
        self._session = session
        self._trace = trace
        self._wrapped: dict[str, Callable[..., Any]] = {}

    def _names(self) -> list[str]:
        if isinstance(self._tools, Mapping):
            return [str(name) for name in self._tools]
        return [
            name for name in dir(self._tools)
            if not name.startswith("_") and callable(getattr(self._tools, name, None))
        ]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names())

    def __len__(self) -> int:
        return len(self._names())

    def _get(self, name: str) -> Any:
        if isinstance(self._tools, Mapping):
            return self._tools[name]
        return getattr(self._tools, name)

    def __getitem__(self, name: str) -> Callable[..., Any]:
        if name not in self._wrapped:
            function = self._get(name)
            session_wrapped = self._session.wrap(function, name=name)

            def traced(*args: Any, **kwargs: Any) -> Any:
                self._trace.append(TraceEvent(len(self._trace) + 1, "tool", name, "called"))
                try:
                    value = session_wrapped(*args, **kwargs)
                except Exception as exc:
                    self._trace.append(TraceEvent(len(self._trace) + 1, "tool", name, f"raised {type(exc).__name__}"))
                    raise
                self._trace.append(TraceEvent(len(self._trace) + 1, "tool", name, "returned", {"value": _jsonable(value)}))
                return value

            traced.__name__ = name
            self._wrapped[name] = traced
        return self._wrapped[name]

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except (KeyError, AttributeError) as exc:
            raise AttributeError(name) from exc

    @property
    def session(self) -> ChaosSession:
        return self._session


# The earlier public name remains available for callers that used the runner's
# probabilistic wrapper. New runs use PlannedTools so exactly one named fault
# is selected per trial.
class FaultyTools(PlannedTools):
    def __init__(self, tools: Any, rng: random.Random, rate: float, trace: list[TraceEvent]):
        call_index = 0 if rate > 0 else 10**9
        plan = ChaosPlan("error" if rate > 0 else None, call_index=call_index, seed=rng.random())
        super().__init__(tools, ChaosSession(plan), trace)


def _trace_callback(trace: list[TraceEvent]) -> Callable[..., None]:
    def record(kind: str = "agent", name: str = "step", detail: str = "", **data: Any) -> None:
        trace.append(TraceEvent(len(trace) + 1, kind, name, detail, data))
    return record


def _call_agent(agent: Any, tools: Any, seed: int, trace: list[TraceEvent], prompt: str | None) -> Any:
    target = getattr(agent, "run", agent)
    if not callable(target):
        raise TypeError("agent must be callable or expose run()")
    callback = _trace_callback(trace)
    values = {
        "task": prompt or "", "tools": tools, "seed": seed, "rng": random.Random(seed),
        "trace": callback, "record": callback, "prompt": prompt, "question": prompt,
    }
    try:
        signature = inspect.signature(target)
    except (ValueError, TypeError):
        return target(tools)
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
    kwargs = values if accepts_kwargs else {key: value for key, value in values.items() if key in signature.parameters}
    return target(**kwargs)


def _fresh(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


class Runner:
    """Run a clean baseline and deterministic one-fault chaos trials."""

    def __init__(self, *, fault_rate: float = 0.35, fault: Any = None, faults: list[str] | tuple[str, ...] | None = None):
        self.fault_rate = max(0.0, min(1.0, float(fault_rate)))
        self.fault = fault
        self.faults = tuple(faults or FAULTS)
        unknown = sorted(set(self.faults) - set(FAULTS))
        if unknown:
            raise ValueError(f"unknown faults: {', '.join(unknown)}")

    def run(
        self, agent: Any, tools: Any, *, seeds: list[int] | tuple[int, ...] = (1, 2, 3),
        example: str = "experiment", prompt: str | None = None,
        expected: Any | None = None, check: Callable[[str], bool] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ExperimentResult:
        seed_list = [int(seed) for seed in seeds]
        baseline = self._run_once(
            _fresh(agent), _fresh(tools), mode="baseline", seed=None, prompt=prompt,
            fault_name=None, call_index=None, check=check, expected=expected,
        )
        runs = [baseline]
        calls = self._tool_call_count(baseline)
        for index, seed in enumerate(seed_list):
            fault_name = self.faults[index % len(self.faults)] if self.fault_rate > 0 and self.faults else None
            chooser = random.Random(f"{seed}-{index}")
            call_index = chooser.randrange(calls) if fault_name and calls else 0
            runs.append(self._run_once(
                _fresh(agent), _fresh(tools), mode="chaos", seed=seed, prompt=prompt,
                fault_name=fault_name, call_index=call_index, check=check, expected=expected,
            ))
        result_metadata = dict(metadata or {})
        result_metadata.setdefault("faults", list(self.faults))
        result_metadata.setdefault("one_fault_per_run", True)
        return ExperimentResult(example, runs, seed_list, self.fault_rate, result_metadata)

    @staticmethod
    def _tool_call_count(record: RunRecord) -> int:
        if record.tool_records:
            return len(record.tool_records)
        output = record.output
        calls = getattr(output, "tool_calls", None)
        if calls is not None:
            return len(calls)
        return sum(event.kind == "tool" and event.detail in {"called", "returned"} for event in record.trace)

    def _run_once(
        self, agent: Any, tools: Any, *, mode: str, seed: int | None, prompt: str | None,
        fault_name: str | None, call_index: int | None, check: Callable[[str], bool] | None, expected: Any | None,
    ) -> RunRecord:
        trace: list[TraceEvent] = []
        record = RunRecord(mode=mode, seed=seed, fault=fault_name, call_index=call_index, trace=trace)
        session: ChaosSession | None = None
        started = time.perf_counter()
        try:
            if mode == "baseline":
                run_tools = tools
                run_seed = 0
            else:
                run_seed = int(seed or 0)
                session = ChaosSession(ChaosPlan(fault_name, int(call_index or 0), f"{seed}-{fault_name or 'clean'}"))
                run_tools = PlannedTools(tools, session, trace)
            trace.append(TraceEvent(len(trace) + 1, "agent", "run", f"{mode} start"))
            value = _call_agent(agent, run_tools, run_seed, trace, prompt)
            record.output = value
            record.status = str(getattr(value, "status", "") or (value.get("status", "") if isinstance(value, Mapping) else ""))
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            record.status = "timeout" if isinstance(exc, TimeoutError) else "error"
            trace.append(TraceEvent(len(trace) + 1, "agent", "run", record.error))
        finally:
            # Timing is deliberately omitted from the persisted result so the
            # same seed and model produce byte-identical JSON.
            record.duration_ms = 0.0
            if session is not None:
                record.injected = session.injected
                record.tool_records = list(session.records)
        if session is None:
            record.judgement = judge(record, expected=expected)
        elif record.error is not None:
            record.judgement = classify({"error": record.error, "status": record.status}, session=session, check=check)
        else:
            record.judgement = classify(record.output, session=session, check=check)
        return record


def run(
    agent: Any, tools: Any, *, seeds: list[int] | tuple[int, ...] = (1, 2, 3),
    fault_rate: float = 0.35, example: str = "experiment", prompt: str | None = None,
    expected: Any | None = None, check: Callable[[str], bool] | None = None,
) -> ExperimentResult:
    return Runner(fault_rate=fault_rate).run(
        agent, tools, seeds=seeds, example=example, prompt=prompt, expected=expected, check=check,
    )


__all__ = [
    "TraceEvent", "RunRecord", "ExperimentResult", "PlannedTools", "FaultyTools", "Runner", "run", "ToolFault",
]
