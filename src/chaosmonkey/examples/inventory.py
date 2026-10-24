"""Offline inventory example with redundant tool data for validation."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping


TASK = "What is the total number of units in stock across all warehouses? Answer with one integer."
WAREHOUSES = ("north", "south", "west")
STOCK = {
    "north": [{"sku": "ACME-ROCKET", "qty": 8}, {"sku": "ACME-MOON", "qty": 5}],
    "south": [{"sku": "ACME-CLOUD", "qty": 11}],
    "west": [{"sku": "ACME-STAR", "qty": 7}, {"sku": "ACME-COMET", "qty": 11}],
}
TRUE_TOTAL = sum(item["qty"] for rows in STOCK.values() for item in rows)


class InventoryTool:
    """Fictional, deterministic inventory service."""

    def list_warehouses(self) -> list[str]:
        return list(WAREHOUSES)

    def get_stock(self, warehouse: str) -> dict[str, Any]:
        rows = [dict(item) for item in STOCK[warehouse]]
        return {
            "warehouse": warehouse, "items": rows,
            "item_count": len(rows),
            "total_units": sum(item["qty"] for item in rows),
        }

    # Compatibility helpers retained for callers of the earlier demo.
    def lookup(self, sku: str = "ACME-ROCKET") -> dict[str, Any]:
        for warehouse, rows in STOCK.items():
            for item in rows:
                if item["sku"] == sku:
                    return {"sku": sku, "found": True, "quantity": item["qty"], "warehouse": warehouse}
        return {"sku": sku, "found": False, "quantity": 0}

    def reserve(self, sku: str = "ACME-ROCKET", quantity: int = 1) -> dict[str, Any]:
        stock = self.lookup(sku)
        available = int(stock.get("quantity", 0))
        requested = int(quantity)
        reserved = max(0, min(requested, available))
        return {"sku": sku, "requested": requested, "reserved": reserved, "confirmed": reserved == requested}


def build_tools() -> dict[str, Any]:
    service = InventoryTool()
    return {"list_warehouses": service.list_warehouses, "get_stock": service.get_stock}


def _content(message: Mapping[str, Any]) -> str:
    text = str(message.get("content", ""))
    prefix = f"TOOL RESULT ({message.get('name', '')}): "
    if text.startswith(prefix):
        return text[len(prefix):]
    error_prefix = f"TOOL ERROR ({message.get('name', '')}): "
    if text.startswith(error_prefix):
        return text[len(error_prefix):]
    return text


def _tool_messages(messages: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [message for message in messages if message.get("role") == "tool"]


def _call(name: str, arguments: dict[str, Any]) -> str:
    return f"CALL {name} {json.dumps(arguments, sort_keys=True)}"


def mock_brain(style: str, messages: list[Mapping[str, Any]]) -> str:
    """Return a deterministic naive or careful response for the Agent loop."""

    careful = style.lower() in {"careful", "mock-careful"}
    tools = _tool_messages(messages)
    if not tools:
        return _call("list_warehouses", {})

    last = tools[-1]
    name = str(last.get("name", ""))
    raw = _content(last)
    if not careful:
        if "PINEAPPLE" in raw:
            return "FINAL: PINEAPPLE"
        if name == "list_warehouses":
            return _call("get_stock", {"warehouse": "north"})
        stock_calls = [message for message in tools if message.get("name") == "get_stock"]
        if len(stock_calls) < len(WAREHOUSES):
            return _call("get_stock", {"warehouse": WAREHOUSES[len(stock_calls)]})
        total = 0
        for message in stock_calls:
            try:
                payload = json.loads(_content(message))
                total += int(payload.get("total_units", 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return f"FINAL: The total number of units is {total}."

    # A careful brain retries a malformed, empty, injected, or internally
    # inconsistent response once. The injected fault fires only on the first
    # attempt, so the retry receives clean data.
    same_calls = [message for message in tools if message.get("name") == name]
    if name == "list_warehouses":
        try:
            warehouses = json.loads(raw)
            if not isinstance(warehouses, list) or warehouses != list(WAREHOUSES):
                raise ValueError("warehouse list was incomplete or inconsistent")
        except (TypeError, ValueError, json.JSONDecodeError):
            if len(same_calls) < 2:
                return _call("list_warehouses", {})
            return "FINAL: UNABLE: the warehouse list is incomplete or unreliable."
        return _call("get_stock", {"warehouse": WAREHOUSES[0]})

    if name == "get_stock":
        def parsed_stock(message: Mapping[str, Any]) -> tuple[str, int] | None:
            try:
                payload = json.loads(_content(message))
                items = payload["items"]
                if payload["item_count"] != len(items) or payload["total_units"] != sum(int(item["qty"]) for item in items):
                    return None
                return str(payload["warehouse"]), int(payload["total_units"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return None

        latest: dict[str, int] = {}
        for message in tools:
            parsed = parsed_stock(message)
            if parsed is not None:
                latest[parsed[0]] = parsed[1]
        if parsed_stock(last) is None:
            trailing_invalid = 0
            for message in reversed(tools):
                if message.get("name") != "get_stock" or parsed_stock(message) is not None:
                    break
                trailing_invalid += 1
            if trailing_invalid < 2:
                next_warehouse = next((warehouse for warehouse in WAREHOUSES if warehouse not in latest), WAREHOUSES[0])
                return _call("get_stock", {"warehouse": next_warehouse})
            return "FINAL: UNABLE: the stock data is incomplete or inconsistent."
        if len(latest) < len(WAREHOUSES):
            next_warehouse = next(warehouse for warehouse in WAREHOUSES if warehouse not in latest)
            return _call("get_stock", {"warehouse": next_warehouse})
        return f"FINAL: The total number of units in stock is {sum(latest.values())}."

    return "FINAL: UNABLE: an unexpected tool response was missing."


class InventoryBrain:
    """Factory-compatible wrapper for the scripted inventory model."""

    def __init__(self, style: str = "careful") -> None:
        self.style = style

    def model(self):
        from ..llm import MockModel
        return MockModel(messages=lambda messages: mock_brain(self.style, messages))


def check(final: str) -> bool:
    return not final.upper().startswith("UNABLE") and bool(re.search(rf"\b{TRUE_TOTAL}\b", final))


def make_inventory(style: str = "careful") -> tuple[Any, dict[str, Any]]:
    from ..agent import Agent
    brain = InventoryBrain(style)
    agent = Agent(brain.model(), system="Use the inventory tools and verify redundant totals.")
    return agent, build_tools()


name = "inventory"
task = TASK

__all__ = ["TASK", "TRUE_TOTAL", "InventoryTool", "InventoryBrain", "build_tools", "check", "mock_brain", "make_inventory", "name", "task"]
