"""
nanoclaw-amplifier runner.
Replaces nanoclaw's Claude Code container. Reads from inbound.db, runs
AmplifierSession, writes to outbound.db. Speaks nanoclaw's exact SQLite protocol.

Entry point: python -m nanoclaw_amplifier.runner
"""
from __future__ import annotations

import asyncio, logging, os, signal, sys, tempfile
from pathlib import Path
from typing import Any

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("nanoclaw-amplifier")

# ── Amplifier imports ─────────────────────────────────────────────────────────
from amplifier_foundation import load_bundle

try:
    from amplifier_foundation.bundle._dataclass import Bundle
except ImportError:
    from amplifier_foundation import Bundle  # type: ignore

try:
    from amplifier_core.models import HookResult
except ImportError:
    class HookResult:  # type: ignore
        def __init__(self, **kwargs): pass

# ── nanoclaw imports ──────────────────────────────────────────────────────────
from nanoclaw_amplifier.config import load_config, NanoclawConfig
from nanoclaw_amplifier.db import (
    open_db, init_outbound, clear_stale_processing,
    fetch_pending, fetch_routing, fetch_destinations,
    ack_batch, mark_inbound_status, update_container_state,
    save_context, load_context,
    next_odd_seq,
)
from amplifier_module_tool_nanoclaw_messaging.tools import (
    NanoclawContext,
    SendMessageTool, SendFileTool, EditMessageTool,
    AddReactionTool, SendCardTool, AskUserQuestionTool,
)
from amplifier_module_tool_nanoclaw_scheduling.tools import (
    ScheduleTaskTool, ListTasksTool, CancelTaskTool,
    PauseTaskTool, ResumeTaskTool, UpdateTaskTool,
)

POLL_INTERVAL = 1.0   # seconds when idle
MAX_IDLE_POLLS = 3600 # ~1 hour idle → exit

BUNDLE_MD = Path(__file__).parent / "bundle.md"


# ── Memory helpers ────────────────────────────────────────────────────────────

def load_memory_text() -> str:
    """Concatenate CLAUDE.md memory files into system context."""
    parts = []
    for p, label in [
        (Path("/workspace/global/CLAUDE.md"),     "Global Instructions"),
        (Path("/workspace/agent/CLAUDE.md"),      "Agent Instructions"),
        (Path("/workspace/agent/CLAUDE.local.md"),"Persistent Memory"),
    ]:
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(f"## {label}\n\n{text}")
            except Exception as e:
                log.warning(f"Could not read {p}: {e}")
    return "\n\n---\n\n".join(parts)


# ── Bundle building ────────────────────────────────────────────────────────────

async def build_prepared(cfg: NanoclawConfig) -> Any:
    """Load bundle.md, compose provider overlay, prepare."""
    # Render {ASSISTANT_NAME} placeholder
    template = BUNDLE_MD.read_text(encoding="utf-8")
    rendered  = template.replace("{ASSISTANT_NAME}", cfg.assistant_name)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(rendered)
        tmp = f.name

    try:
        base = await load_bundle(tmp)
    finally:
        try: os.unlink(tmp)
        except: pass

    # Provider overlay
    provider_entry: dict = {
        "module": cfg.provider.module_id,
        "source": cfg.provider.source,
        "config": {"default_model": cfg.provider.model},
    }
    if cfg.provider.extra:
        provider_entry["config"].update(cfg.provider.extra)

    overlay = Bundle(
        name="nanoclaw-provider-overlay",
        providers=[provider_entry],
    )
    composed = base.compose(overlay)

    # Optional per-agent custom bundle from additionalMounts
    custom_bundle_path = Path("/workspace/extra/bundle.md")
    if custom_bundle_path.exists():
        log.info("Loading per-agent bundle overlay from /workspace/extra/bundle.md")
        custom = await load_bundle(str(custom_bundle_path))
        composed = composed.compose(custom)

    log.info(f"Preparing bundle (provider={cfg.provider.module_id}, model={cfg.provider.model})...")
    return await composed.prepare()


# ── Session lifecycle ─────────────────────────────────────────────────────────

async def create_session(prepared: Any, cfg: NanoclawConfig,
                         in_conn: Any, out_conn: Any) -> tuple[Any, NanoclawContext]:
    """Create session, mount tools, register hooks."""
    saved_ctx = load_context(out_conn)
    is_resumed = bool(saved_ctx)

    log.info(f"Creating session (resumed={is_resumed}, id={cfg.agent_group_id})")
    session = await prepared.create_session(
        session_id=cfg.agent_group_id,
        is_resumed=is_resumed,
    )

    # Create shared context for all tools
    ctx = NanoclawContext(in_conn=in_conn, out_conn=out_conn)

    # Mount nanoclaw tools post-creation (documented in APPLICATION_INTEGRATION_GUIDE step 5)
    nanoclaw_tools = [
        SendMessageTool(ctx), SendFileTool(ctx), EditMessageTool(ctx),
        AddReactionTool(ctx), SendCardTool(ctx), AskUserQuestionTool(ctx),
        ScheduleTaskTool(ctx), ListTasksTool(ctx), CancelTaskTool(ctx),
        PauseTaskTool(ctx), ResumeTaskTool(ctx), UpdateTaskTool(ctx),
    ]
    for tool in nanoclaw_tools:
        await session.coordinator.mount("tools", tool, name=tool.name)

    # Register hooks
    heartbeat = cfg.heartbeat

    async def on_llm_response(event: str, data: dict) -> HookResult:
        try: heartbeat.touch(exist_ok=True)
        except: pass
        return HookResult()

    async def on_tool_pre(event: str, data: dict) -> HookResult:
        tname = data.get("tool_name", "unknown")
        await asyncio.to_thread(update_container_state, out_conn, tname)
        return HookResult()

    async def on_tool_post(event: str, data: dict) -> HookResult:
        await asyncio.to_thread(update_container_state, out_conn, None)
        return HookResult()

    session.coordinator.hooks.register("llm:response",      on_llm_response, name="nc-heartbeat")
    session.coordinator.hooks.register("content_block:end", on_llm_response, name="nc-heartbeat-block")
    session.coordinator.hooks.register("tool:pre",          on_tool_pre,     name="nc-tool-state-pre")
    session.coordinator.hooks.register("tool:post",         on_tool_post,    name="nc-tool-state-post")

    # Restore context if resuming
    if saved_ctx:
        context_mgr = session.coordinator.get("context")
        if context_mgr:
            try:
                await context_mgr.set_messages(saved_ctx)
                log.info(f"Restored {len(saved_ctx)} context messages")
            except Exception as e:
                log.warning(f"Could not restore context: {e}")

    # Inject CLAUDE.md memory files as initial system message
    memory = load_memory_text()
    if memory:
        context_mgr = session.coordinator.get("context")
        if context_mgr:
            try:
                await context_mgr.add_message({"role": "system", "content": memory})
            except Exception as e:
                log.warning(f"Could not inject memory: {e}")

    return session, ctx


# ── Prompt building ───────────────────────────────────────────────────────────

def build_prompt(rows: list, routing: dict) -> str:
    """Build a text prompt from a batch of inbound messages."""
    parts = []
    if routing.get("channel_type"):
        parts.append(
            f"[Channel: {routing['channel_type']} | "
            f"platform_id: {routing.get('platform_id','')} | "
            f"thread: {routing.get('thread_id') or 'none'}]"
        )
    import json as _json
    for row in rows:
        kind    = row["kind"]
        content = row["content"]
        seq     = row["seq"]
        try:
            content_parsed = _json.loads(content) if isinstance(content, str) else content
        except Exception:
            content_parsed = {"raw": content}

        if kind in ("chat", "chat-sdk"):
            text = (content_parsed.get("text") or
                    content_parsed.get("message") or
                    _json.dumps(content_parsed))
            parts.append(f"[seq={seq}]\n{text}")
        elif kind == "task":
            prompt_text = content_parsed.get("prompt", "")
            parts.append(f"[Scheduled task seq={seq}]\n{prompt_text}")
        elif kind == "system":
            parts.append(f"[System event seq={seq}]\n{_json.dumps(content_parsed)}")
        else:
            parts.append(f"[{kind} seq={seq}]\n{_json.dumps(content_parsed)}")

    return "\n\n".join(parts)


# ── Poll loop ─────────────────────────────────────────────────────────────────

async def run_poll_loop(cfg: NanoclawConfig, ctx: NanoclawContext,
                        in_conn: Any, out_conn: Any, session: Any) -> None:
    idle = 0
    shutdown = asyncio.Event()

    def _sig(*_):
        log.info("Shutdown signal received")
        shutdown.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    log.info("Poll loop started")

    while not shutdown.is_set():
        if idle >= MAX_IDLE_POLLS:
            log.info("Idle limit reached, exiting")
            break

        rows = await asyncio.to_thread(fetch_pending, in_conn, cfg.max_messages_per_prompt)

        if not rows:
            idle += 1
            await asyncio.sleep(POLL_INTERVAL)
            continue

        idle = 0
        ids = [r["id"] for r in rows]
        log.info(f"Processing batch of {len(rows)} message(s): {ids}")

        # Update shared context with current routing
        routing = await asyncio.to_thread(fetch_routing, in_conn)
        destinations = await asyncio.to_thread(fetch_destinations, in_conn)
        ctx.routing_channel_type = routing.get("channel_type", "")
        ctx.routing_platform_id  = routing.get("platform_id", "")
        ctx.routing_thread_id    = routing.get("thread_id")
        ctx.destinations         = destinations
        ctx.current_inbound_ids  = ids

        await asyncio.to_thread(ack_batch, out_conn, ids, "processing")
        await asyncio.to_thread(mark_inbound_status, in_conn, ids, "processing")

        try:
            prompt = build_prompt(list(rows), routing)
            log.info(f"Executing prompt ({len(prompt)} chars)")
            await session.execute(prompt)
            await asyncio.to_thread(ack_batch, out_conn, ids, "completed")
            await asyncio.to_thread(mark_inbound_status, in_conn, ids, "completed")
            log.info("Batch completed")

            # Persist context after each successful turn
            context_mgr = session.coordinator.get("context")
            if context_mgr:
                try:
                    messages = await context_mgr.get_messages()
                    await asyncio.to_thread(save_context, out_conn, messages)
                except Exception as e:
                    log.warning(f"Context save failed: {e}")

        except Exception as e:
            log.error(f"Batch failed: {e}", exc_info=True)
            await asyncio.to_thread(ack_batch, out_conn, ids, "failed")
            await asyncio.to_thread(mark_inbound_status, in_conn, ids, "failed")
            await asyncio.sleep(5.0)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    log.info("nanoclaw-amplifier starting")

    cfg = load_config()
    log.info(f"Config: provider={cfg.provider.module_id} model={cfg.provider.model} "
             f"assistant={cfg.assistant_name}")

    in_conn  = await asyncio.to_thread(open_db, cfg.inbound_db)
    out_conn = await asyncio.to_thread(open_db, cfg.outbound_db)

    await asyncio.to_thread(init_outbound, out_conn)
    await asyncio.to_thread(clear_stale_processing, out_conn)

    prepared = await build_prepared(cfg)

    session, ctx = await create_session(prepared, cfg, in_conn, out_conn)

    try:
        await run_poll_loop(cfg, ctx, in_conn, out_conn, session)
    finally:
        log.info("Saving context and cleaning up...")
        context_mgr = session.coordinator.get("context")
        if context_mgr:
            try:
                messages = await context_mgr.get_messages()
                await asyncio.to_thread(save_context, out_conn, messages)
            except Exception as e:
                log.warning(f"Final context save failed: {e}")
        await session.cleanup()
        in_conn.close()
        out_conn.close()
        log.info("Shutdown complete")


def main_sync():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
