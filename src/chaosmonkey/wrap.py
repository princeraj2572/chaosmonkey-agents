"""Tool wrappers that apply one planned fault and retain an audit trail."""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .faults import FAULTS, ToolFault, apply_fault


@dataclass(frozen=True)
class ChaosPlan:
    """One fault applied to the zero-based ``call_index`` tool call."""

    fault: str | None
    call_index: int = 0
    seed: int | str = 0


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    original: str | None
    delivered: str | None
    fault: str | None = None
    raised: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


@dataclass
class ChaosSession:
    plan: ChaosPlan
    records: list[ToolCallRecord] = field(default_factory=list)
    injected: bool = False
    _next_call: int = field(default=0, init=False, repr=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(str(self.plan.seed))
        if self.plan.fault is not None and self.plan.fault not in FAULTS:
            raise ValueError(f"unknown fault {self.plan.fault!r}")

    def wrap(self, fn: Callable[..., Any], name: str | None = None) -> Callable[..., str]:
        """Return a callable that logs and optionally corrupts ``fn``."""

        tool_name = name or getattr(fn, "__name__", "tool")

        def wrapped(*args: Any, **kwargs: Any) -> str:
            index = self._next_call
            self._next_call += 1
            call_args = dict(kwargs)
            if args:
                call_args["_args"] = list(args)
            original: str | None = None
            delivered: str | None = None
            fault_name: str | None = None
            raised: str | None = None
            try:
                result = fn(*args, **kwargs)
                original = _text(result)
                delivered = original
                if (
                    self.plan.fault is not None
                    and index == self.plan.call_index
                ):
                    fault_name = self.plan.fault
                    self.injected = True
                    delivered = apply_fault(fault_name, original, self._rng)
                return delivered
            except Exception as exc:
                raised = f"{type(exc).__name__}: {exc}"
                if self.plan.fault is not None and index == self.plan.call_index:
                    fault_name = fault_name or self.plan.fault
                    self.injected = True
                raise
            finally:
                self.records.append(
                    ToolCallRecord(
                        tool=tool_name,
                        args=call_args,
                        original=original,
                        delivered=delivered,
                        fault=fault_name,
                        raised=raised,
                    )
                )

        wrapped.__name__ = tool_name
        wrapped.__doc__ = getattr(fn, "__doc__", None)
        return wrapped

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": asdict(self.plan),
            "injected": self.injected,
            "records": [record.to_dict() for record in self.records],
        }


__all__ = ["ChaosPlan", "ChaosSession", "ToolCallRecord", "ToolFault"]
