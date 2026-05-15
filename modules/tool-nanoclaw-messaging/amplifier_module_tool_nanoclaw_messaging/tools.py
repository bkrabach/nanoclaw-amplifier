import asyncio, json, os, shutil, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Try amplifier_core ToolResult import - handle both possible locations
try:
    from amplifier_core import ToolResult
except ImportError:
    try:
        from amplifier_core.models import ToolResult
    except ImportError:
        # Fallback dataclass
        @dataclass
        class ToolResult:
            success: bool = True
            output: Any = None
            error: dict | None = None

@dataclass
class NanoclawContext:
    in_conn: Any = None
    out_conn: Any = None
    routing_channel_type: str = ""
    routing_platform_id: str = ""
    routing_thread_id: str | None = None
    destinations: dict = field(default_factory=dict)
    current_inbound_ids: list = field(default_factory=list)

OUTBOX_DIR = Path("/workspace/outbox")

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _rand6() -> str:
    import random, string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def _next_odd_seq(ctx: NanoclawContext) -> int:
    """Next odd seq number: read max from BOTH DBs."""
    row = ctx.in_conn.execute("SELECT COALESCE(MAX(seq), 0) as m FROM messages_in").fetchone()
    max_in = row[0] if row else 0
    row = ctx.out_conn.execute("SELECT COALESCE(MAX(seq), 0) as m FROM messages_out").fetchone()
    max_out = row[0] if row else 0
    base = max(max_in, max_out)
    candidate = base + 1
    return candidate if candidate % 2 == 1 else candidate + 1

def _resolve_dest(ctx: NanoclawContext, destination_name: str | None) -> dict:
    """Resolve destination to {channel_type, platform_id, thread_id}."""
    if destination_name and destination_name in ctx.destinations:
        d = ctx.destinations[destination_name]
        return {"channel_type": d.get("channel_type", ""),
                "platform_id": d.get("platform_id", ""),
                "thread_id": d.get("thread_id")}
    return {
        "channel_type": ctx.routing_channel_type,
        "platform_id": ctx.routing_platform_id,
        "thread_id": ctx.routing_thread_id,
    }

def _write_out(ctx: NanoclawContext, msg_id: str, seq: int, kind: str,
               content: dict, in_reply_to: str | None = None,
               dest: dict | None = None) -> None:
    d = dest or {}
    ctx.out_conn.execute(
        """INSERT INTO messages_out
           (id, seq, in_reply_to, timestamp, deliver_after, recurrence,
            kind, platform_id, channel_type, thread_id, content)
           VALUES (?, ?, ?, datetime('now'), NULL, NULL, ?, ?, ?, ?, ?)""",
        (msg_id, seq, in_reply_to,
         kind,
         d.get("platform_id", ""),
         d.get("channel_type", ""),
         d.get("thread_id"),
         json.dumps(content))
    )
    ctx.out_conn.commit()

# --- Tools ---

class SendMessageTool:
    name = "send_message"
    description = (
        "Send a text message to the user. "
        "REQUIRED for ALL user-visible replies — return text is NOT delivered. "
        "Use this for every response the user should see."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Message text to send"},
            "destination": {"type": "string", "description": "Named destination (optional, defaults to current session routing)"},
        },
        "required": ["text"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        text = input.get("text", "")
        if not text:
            return ToolResult(success=False, error={"message": "text is required"})
        dest = _resolve_dest(self._ctx, input.get("destination"))
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"msg-{int(time.time()*1000)}-{_rand6()}"
        in_reply_to = self._ctx.current_inbound_ids[0] if self._ctx.current_inbound_ids else None
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "chat",
                                {"text": text}, in_reply_to, dest)
        return ToolResult(success=True, output=f"Message sent (seq={seq})")


class SendFileTool:
    name = "send_file"
    description = "Send a file attachment with a caption. File is copied to /workspace/outbox/."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Caption text"},
            "file_path": {"type": "string", "description": "Absolute path to file"},
            "destination": {"type": "string", "description": "Named destination (optional)"},
        },
        "required": ["text", "file_path"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        text = input.get("text", "")
        file_path = Path(input.get("file_path", ""))
        if not file_path.exists():
            return ToolResult(success=False, error={"message": f"File not found: {file_path}"})
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        dest_file = OUTBOX_DIR / file_path.name
        await asyncio.to_thread(shutil.copy2, str(file_path), str(dest_file))
        dest = _resolve_dest(self._ctx, input.get("destination"))
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"msg-{int(time.time()*1000)}-{_rand6()}"
        in_reply_to = self._ctx.current_inbound_ids[0] if self._ctx.current_inbound_ids else None
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "chat",
                                {"text": text, "files": [file_path.name]}, in_reply_to, dest)
        return ToolResult(success=True, output=f"File sent (seq={seq})")


class EditMessageTool:
    name = "edit_message"
    description = "Edit a previously sent message. Provide the seq of the original inbound message to identify context."
    input_schema = {
        "type": "object",
        "properties": {
            "seq": {"type": "integer", "description": "Seq of the inbound message whose reply to edit"},
            "text": {"type": "string", "description": "New message text"},
        },
        "required": ["seq", "text"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        seq_target = input.get("seq")
        text = input.get("text", "")
        # Look up platform_id from delivered table or messages_out
        def _lookup():
            row = self._ctx.out_conn.execute(
                "SELECT platform_message_id FROM delivered d "
                "JOIN messages_out mo ON d.message_out_id = mo.id "
                "WHERE mo.in_reply_to IN (SELECT id FROM messages_in WHERE seq=?) LIMIT 1",
                (seq_target,)
            ).fetchone()
            return row[0] if row else None
        platform_id = await asyncio.to_thread(_lookup)
        if not platform_id:
            platform_id = f"seq-{seq_target}"  # fallback
        dest = _resolve_dest(self._ctx, None)
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"msg-{int(time.time()*1000)}-{_rand6()}"
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "chat",
                                {"operation": "edit", "messageId": platform_id, "text": text},
                                None, dest)
        return ToolResult(success=True, output=f"Edit queued (seq={seq})")


class AddReactionTool:
    name = "add_reaction"
    description = "Add an emoji reaction to a previously sent message."
    input_schema = {
        "type": "object",
        "properties": {
            "seq": {"type": "integer", "description": "Seq of the message to react to"},
            "emoji": {"type": "string", "description": "Emoji character or name"},
        },
        "required": ["seq", "emoji"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        seq_target = input.get("seq")
        emoji = input.get("emoji", "")
        def _lookup():
            row = self._ctx.out_conn.execute(
                "SELECT platform_message_id FROM delivered d "
                "JOIN messages_out mo ON d.message_out_id = mo.id "
                "WHERE mo.in_reply_to IN (SELECT id FROM messages_in WHERE seq=?) LIMIT 1",
                (seq_target,)
            ).fetchone()
            return row[0] if row else f"seq-{seq_target}"
        platform_id = await asyncio.to_thread(_lookup)
        dest = _resolve_dest(self._ctx, None)
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"msg-{int(time.time()*1000)}-{_rand6()}"
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "chat",
                                {"operation": "reaction", "messageId": platform_id, "emoji": emoji},
                                None, dest)
        return ToolResult(success=True, output=f"Reaction queued (seq={seq})")


class SendCardTool:
    name = "send_card"
    description = "Send a structured card/embed to the channel."
    input_schema = {
        "type": "object",
        "properties": {
            "card_data": {"type": "object", "description": "Platform card data"},
            "destination": {"type": "string"},
        },
        "required": ["card_data"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        card_data = input.get("card_data")
        dest = _resolve_dest(self._ctx, input.get("destination"))
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"msg-{int(time.time()*1000)}-{_rand6()}"
        in_reply_to = self._ctx.current_inbound_ids[0] if self._ctx.current_inbound_ids else None
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "chat",
                                {"card": card_data}, in_reply_to, dest)
        return ToolResult(success=True, output=f"Card sent (seq={seq})")


class AskUserQuestionTool:
    name = "ask_user_question"
    description = (
        "Present a multiple-choice question to the user and wait for their answer. "
        "BLOCKS for up to timeout seconds (default 300). Returns the chosen option."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The question text"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Answer options"},
            "timeout": {"type": "number", "description": "Timeout in seconds (default 300)"},
        },
        "required": ["title", "options"],
    }
    def __init__(self, ctx: NanoclawContext): self._ctx = ctx
    async def execute(self, input: dict) -> ToolResult:
        title = input.get("title", "")
        options = input.get("options", [])
        timeout = float(input.get("timeout", 300))
        question_id = str(uuid.uuid4())
        dest = _resolve_dest(self._ctx, None)
        seq = await asyncio.to_thread(_next_odd_seq, self._ctx)
        msg_id = f"msg-{int(time.time()*1000)}-{_rand6()}"
        in_reply_to = self._ctx.current_inbound_ids[0] if self._ctx.current_inbound_ids else None
        await asyncio.to_thread(_write_out, self._ctx, msg_id, seq, "chat",
                                {"type": "ask_question", "questionId": question_id,
                                 "title": title, "options": options},
                                in_reply_to, dest)
        # Poll for response
        def _poll() -> dict | None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                row = self._ctx.in_conn.execute(
                    "SELECT id, content FROM messages_in "
                    "WHERE kind='system' "
                    "AND json_extract(content, '$.questionId')=? "
                    "AND (status IS NULL OR status NOT IN ('completed','failed')) "
                    "LIMIT 1", (question_id,)
                ).fetchone()
                if row:
                    try:
                        c = json.loads(row[1])
                        return c.get("answer") or c.get("option") or c.get("result") or str(c)
                    except Exception:
                        return str(row[1])
                time.sleep(1.0)
            return None
        result = await asyncio.to_thread(_poll)
        if result is None:
            return ToolResult(success=False, error={"message": f"Timed out after {timeout}s"})
        return ToolResult(success=True, output=result)
