"""Nanoclaw messaging tools for Amplifier."""
__amplifier_module_type__ = "tool"

from .tools import (
    SendMessageTool, SendFileTool, EditMessageTool,
    AddReactionTool, SendCardTool, AskUserQuestionTool,
    NanoclawContext,
)

__all__ = [
    "mount", "NanoclawContext",
    "SendMessageTool", "SendFileTool", "EditMessageTool",
    "AddReactionTool", "SendCardTool", "AskUserQuestionTool",
]

async def mount(coordinator, config=None):
    # Context is injected after mount by runner.py
    # We expose tool instances so runner can call set_context()
    ctx = NanoclawContext()
    tools = [
        SendMessageTool(ctx),
        SendFileTool(ctx),
        EditMessageTool(ctx),
        AddReactionTool(ctx),
        SendCardTool(ctx),
        AskUserQuestionTool(ctx),
    ]
    for tool in tools:
        await coordinator.mount("tools", tool, name=tool.name)
    # Stash context ref on coordinator for runner to retrieve
    coordinator._nanoclaw_messaging_ctx = ctx
