import asyncio, json, time
from typing import Any

try:
    from amplifier_core import ToolResult
except ImportError:
    try:
        from amplifier_core.models import ToolResult
    except ImportError:
        from dataclasses import dataclass
        @dataclass
        class ToolResult:
            success: bool = True
            output: Any = None
            error: dict | None = None

from amplifier_module_tool_nanoclaw_messaging.tools import (
    NanoclawContext, _next_odd_seq, _resolve_dest, _write_out, _rand6,
)


class ScheduleTaskTool:
    name = "schedule_task"
    description = "Schedule a task to run at a future time, optionally recurring."
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Task instructions to execute when triggered"},
            "process_after": {"type": "string", "description": "ISO 8601 datetime (e.g. '2026-01-15T09:00:00Z')"},
            "recurrence": {"type": "string", "description": "Cron expression for recurring tasks (optional)"},
            "script": {"type": "string", "description": "Optional pre-execution script (optional)"},
        },
        "required": ["prompt", "process_after"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        prompt = input.get("prompt", "")
        process_after = input.get("process_after", "")
        if not prompt or not process_after:
            return ToolResult(success=False, error={"message": "prompt and process_after required"})
        task_id = f"task-{int(time.time()*1000)}-{_rand6()}"
        dest = _resolve_dest(self._ctx, None)
        content: dict = {
            "action": "schedule_task", "taskId": task_id, "prompt": prompt,
            "processAfter": process_after, "script": input.get("script"),
            "platformId": dest["platform_id"],
            "channelType": dest["channel_type"],
            "threadId": dest["thread_id"],
        }
        if input.get("recurrence"):
            content["recurrence"] = input["recurrence"]
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        await asyncio.to_thread(_write_out, self._ctx, task_id, seq, "system", content)
        return ToolResult(success=True, output=f"Task scheduled: {task_id}")


class ListTasksTool:
    name = "list_tasks"
    description = "List all pending and active scheduled tasks."
    input_schema = {"type": "object", "properties": {}, "required": []}
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        def _fetch():
            try:
                rows = self._ctx.in_conn.execute(
                    "SELECT seq, content FROM messages_in "
                    "WHERE kind='task' AND (status IS NULL OR status NOT IN ('completed','failed','cancelled')) "
                    "ORDER BY seq ASC"
                ).fetchall()
                tasks = []
                for row in rows:
                    try:
                        c = json.loads(row[1])
                    except Exception:
                        c = {"raw": row[1]}
                    tasks.append({"seq": row[0], **c})
                return tasks
            except Exception:
                return []
        tasks = await asyncio.to_thread(_fetch)
        return ToolResult(success=True, output=tasks)


class CancelTaskTool:
    name = "cancel_task"
    description = "Cancel a scheduled task by its task ID."
    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string", "description": "Task ID to cancel"}},
        "required": ["task_id"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        task_id = input.get("task_id", "")
        if not task_id:
            return ToolResult(success=False, error={"message": "task_id required"})
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"task-{int(time.time()*1000)}-{_rand6()}"
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "system",
                                {"action": "cancel_task", "taskId": task_id})
        return ToolResult(success=True, output=f"Cancel queued for {task_id}")


class PauseTaskTool:
    name = "pause_task"
    description = "Pause a recurring scheduled task."
    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        task_id = input.get("task_id", "")
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"task-{int(time.time()*1000)}-{_rand6()}"
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "system",
                                {"action": "pause_task", "taskId": task_id})
        return ToolResult(success=True, output=f"Pause queued for {task_id}")


class ResumeTaskTool:
    name = "resume_task"
    description = "Resume a paused scheduled task."
    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        task_id = input.get("task_id", "")
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"task-{int(time.time()*1000)}-{_rand6()}"
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "system",
                                {"action": "resume_task", "taskId": task_id})
        return ToolResult(success=True, output=f"Resume queued for {task_id}")


class UpdateTaskTool:
    name = "update_task"
    description = "Update a task's prompt, recurrence, timing, or script."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "prompt": {"type": "string"},
            "recurrence": {"type": "string"},
            "process_after": {"type": "string"},
            "script": {"type": "string"},
        },
        "required": ["task_id"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        task_id = input.get("task_id", "")
        content: dict[str, Any] = {"action": "update_task", "taskId": task_id}
        for field_name in ("prompt", "recurrence", "script"):
            if field_name in input and input[field_name] is not None:
                content[field_name] = input[field_name]
        if "process_after" in input:
            content["processAfter"] = input["process_after"]
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"task-{int(time.time()*1000)}-{_rand6()}"
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "system", content)
        return ToolResult(success=True, output=f"Update queued for {task_id}")
