"""Deterministic offline examples used by the CLI and tests."""

from .inventory import InventoryBrain, InventoryTool, make_inventory
from .research import ResearchBrain, ResearchTool, make_research

__all__ = ["InventoryBrain", "InventoryTool", "ResearchBrain", "ResearchTool", "make_inventory", "make_research"]
