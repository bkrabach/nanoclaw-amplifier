"""Nanoclaw scheduling tools for Amplifier."""
__amplifier_module_type__ = "tool"

from .tools import (
    ScheduleTaskTool, ListTasksTool, CancelTaskTool,
    PauseTaskTool, ResumeTaskTool, UpdateTaskTool,
)
from amplifier_module_tool_nanoclaw_messaging.tools import NanoclawContext

__all__ = ["mount", "NanoclawContext",
           "ScheduleTaskTool", "ListTasksTool", "CancelTaskTool",
           "PauseTaskTool", "ResumeTaskTool", "UpdateTaskTool"]

async def mount(coordinator, config=None):
    ctx = NanoclawContext()
    tools = [
        ScheduleTaskTool(ctx),
        ListTasksTool(ctx),
        CancelTaskTool(ctx),
        PauseTaskTool(ctx),
        ResumeTaskTool(ctx),
        UpdateTaskTool(ctx),
    ]
    for tool in tools:
        await coordinator.mount("tools", tool, name=tool.name)
    coordinator._nanoclaw_scheduling_ctx = ctx
