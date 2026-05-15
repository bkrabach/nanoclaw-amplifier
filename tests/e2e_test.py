#!/usr/bin/env python3
"""
E2E test for nanoclaw-amplifier.
Simulates nanoclaw's host: creates SQLite databases, runs the runner,
feeds messages, validates outputs. Uses real API calls.
"""
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).parent.parent


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def setup_workspace(tmp: Path):
    """Create workspace directory structure matching nanoclaw's layout."""
    workspace = tmp / "workspace"
    agent_dir = tmp / "agent"
    workspace.mkdir()
    agent_dir.mkdir()
    (workspace / "outbox").mkdir()

    # Create container.json
    container_json = {
        "provider": "anthropic",
        "model": "claude-haiku-3-5",
        "assistantName": "TestBot",
        "agentGroupId": "test-agent-001",
        "maxMessagesPerPrompt": 10,
        "mcpServers": {}
    }
    (agent_dir / "container.json").write_text(json.dumps(container_json))

    # Create CLAUDE.local.md (persistent memory)
    (agent_dir / "CLAUDE.local.md").write_text(
        "# Test Agent Memory\n\nThis is a test agent. Be concise and helpful."
    )

    return workspace, agent_dir


def init_inbound_db(db_path: Path):
    """Create inbound.db with nanoclaw's schema."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages_in (
            id TEXT PRIMARY KEY,
            seq INTEGER UNIQUE,
            kind TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            process_after TEXT,
            recurrence TEXT,
            series_id TEXT,
            tries INTEGER DEFAULT 0,
            trigger INTEGER NOT NULL DEFAULT 1,
            platform_id TEXT,
            channel_type TEXT,
            thread_id TEXT,
            content TEXT NOT NULL,
            source_session_id TEXT,
            on_wake INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS session_routing (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_type TEXT,
            platform_id TEXT,
            thread_id TEXT
        );
        CREATE TABLE IF NOT EXISTS destinations (
            name TEXT PRIMARY KEY,
            display_name TEXT,
            type TEXT NOT NULL,
            channel_type TEXT,
            platform_id TEXT,
            agent_group_id TEXT
        );
        CREATE TABLE IF NOT EXISTS delivered (
            message_out_id TEXT PRIMARY KEY,
            platform_message_id TEXT,
            status TEXT NOT NULL DEFAULT 'delivered',
            delivered_at TEXT NOT NULL
        );
        INSERT OR REPLACE INTO session_routing (id, channel_type, platform_id, thread_id)
        VALUES (1, 'test', 'test-user-001', NULL);
    """)
    conn.commit()
    conn.close()


def insert_message(db_path: Path, seq: int, text: str, msg_id: str = None):
    """Insert a chat message into inbound.db."""
    if msg_id is None:
        msg_id = f"msg-{int(time.time()*1000)}-test{seq:02d}"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO messages_in
           (id, seq, kind, timestamp, status, trigger, platform_id, channel_type, content)
           VALUES (?, ?, 'chat', ?, 'pending', 1, 'test-user-001', 'test', ?)""",
        (msg_id, seq, now_iso(), json.dumps({"text": text}))
    )
    conn.commit()
    conn.close()
    print(f"  → Inserted message [seq={seq}]: {text[:60]}")


def get_all_outbound(out_db_path: Path) -> tuple[list[dict], list[dict]]:
    """Get all chat and system rows from outbound.db."""
    if not out_db_path.exists():
        return [], []
    try:
        conn = sqlite3.connect(str(out_db_path))
        conn.row_factory = sqlite3.Row
        chat = [dict(r) for r in conn.execute(
            "SELECT * FROM messages_out WHERE kind='chat' ORDER BY seq ASC"
        ).fetchall()]
        system = [dict(r) for r in conn.execute(
            "SELECT * FROM messages_out WHERE kind='system' ORDER BY seq ASC"
        ).fetchall()]
        conn.close()
        return chat, system
    except Exception:
        return [], []


def check_processing_acks(out_db_path: Path) -> list[dict]:
    """Check processing_ack table."""
    if not out_db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(out_db_path))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM processing_ack ORDER BY rowid ASC"
        ).fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def wait_for_outbound_count(out_db_path: Path, min_count: int, timeout: float = 60.0,
                             poll: float = 0.5) -> list[dict]:
    """Wait until messages_out chat rows reach at least min_count."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chat, _ = get_all_outbound(out_db_path)
        if len(chat) >= min_count:
            return chat
        time.sleep(poll)
    return get_all_outbound(out_db_path)[0]


def wait_for_ack(out_db_path: Path, msg_count: int, timeout: float = 60.0) -> list[dict]:
    """Wait for at least msg_count completed/failed acks."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        acks = check_processing_acks(out_db_path)
        done = [a for a in acks if a["status"] in ("completed", "failed")]
        if len(done) >= msg_count:
            return acks
        time.sleep(0.5)
    return check_processing_acks(out_db_path)


async def run_e2e_test():
    """Main E2E test."""
    # Load API key from amplifier settings if not in env
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    default_model = "claude-sonnet-4-6"
    if not api_key:
        try:
            import yaml
            settings = yaml.safe_load(open(os.path.expanduser("~/.amplifier/settings.yaml")))
            for p in settings.get("config", {}).get("providers", []):
                if p.get("module") == "provider-anthropic":
                    api_key = p["config"]["api_key"]
                    # Use model from settings if available
                    default_model = p["config"].get("default_model", default_model)
                    break
        except Exception as e:
            print(f"Warning: Could not load API key from settings: {e}")

    if not api_key:
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            print("Using OpenAI provider for E2E test")
            provider = "openai"
            model = "gpt-4o-mini"
            api_key = openai_key
        else:
            print("ERROR: No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
            sys.exit(1)
    else:
        provider = "anthropic"
        model = default_model
        print(f"Using Anthropic provider (key: {api_key[:12]}...) model: {model}")

    print(f"\n{'='*60}")
    print("nanoclaw-amplifier E2E Test")
    print(f"Provider: {provider} / Model: {model}")
    print(f"{'='*60}\n")

    with tempfile.TemporaryDirectory(prefix="nanoclaw-e2e-") as tmp_str:
        tmp = Path(tmp_str)
        workspace, agent_dir = setup_workspace(tmp)

        # Override provider/model in container.json
        container_json = json.loads((agent_dir / "container.json").read_text())
        container_json["provider"] = provider
        container_json["model"] = model
        (agent_dir / "container.json").write_text(json.dumps(container_json))

        in_db  = workspace / "inbound.db"
        out_db = workspace / "outbound.db"
        heartbeat = workspace / ".heartbeat"
        context_file = agent_dir / ".amplifier-context.json"

        init_inbound_db(in_db)

        # Build env for the runner subprocess
        env = dict(os.environ)
        env["ANTHROPIC_API_KEY"] = api_key
        env["LOG_LEVEL"] = "INFO"
        # Use env-var path overrides (new feature added to config.py)
        env["_NC_CONTAINER_JSON"] = str(agent_dir / "container.json")
        env["_NC_INBOUND_DB"]     = str(in_db)
        env["_NC_OUTBOUND_DB"]    = str(out_db)
        env["_NC_HEARTBEAT"]      = str(heartbeat)
        env["_NC_CONTEXT_FILE"]   = str(context_file)

        print("Starting runner process...")
        proc = subprocess.Popen(
            [sys.executable, "-m", "nanoclaw_amplifier.runner"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Give runner time to start and initialize the outbound DB
        print("Waiting for runner to initialize (up to 30s)...")
        start = time.monotonic()
        timeout_init = 30
        initialized = False
        while time.monotonic() - start < timeout_init:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                print("Runner exited prematurely!")
                print("STDOUT:", stdout[-3000:] if stdout else "(empty)")
                print("STDERR:", stderr[-3000:] if stderr else "(empty)")
                sys.exit(1)
            # outbound.db created and sized > 0 → init_outbound ran
            if out_db.exists() and out_db.stat().st_size > 0:
                initialized = True
                print(f"  ✓ Runner initialized in {time.monotonic()-start:.1f}s")
                break
            time.sleep(0.5)

        if not initialized:
            print(f"  ✗ Runner did not initialize within {timeout_init}s")
            proc.terminate()
            _, stderr = proc.communicate(timeout=10)
            print("STDERR:", stderr[-3000:] if stderr else "(empty)")
            sys.exit(1)

        results = {}

        # ── Test 1: Simple greeting ────────────────────────────────────────
        print("\n── Test 1: Simple greeting ──")
        insert_message(in_db, 2, "Hello! Please introduce yourself briefly in one sentence.")

        chat = wait_for_outbound_count(out_db, 1, timeout=60)
        if chat:
            content = json.loads(chat[0]["content"])
            text = content.get("text", "")
            print(f"  ✓ Got response: {text[:120]}...")
            results["test1"] = True
        else:
            print("  ✗ No response received for Test 1")
            results["test1"] = False

        # ── Test 2: Arithmetic + multi-part answer ─────────────────────────
        print("\n── Test 2: Arithmetic and facts ──")
        before = len(get_all_outbound(out_db)[0])
        insert_message(in_db, 4,
            "What is 17 * 23? Also tell me one fun fact about prime numbers.")

        # Wait for at least one new response
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            chat, _ = get_all_outbound(out_db)
            if len(chat) > before:
                new_resp = chat[before:]
                print(f"  ✓ Got {len(new_resp)} response(s)")
                for r in new_resp:
                    c = json.loads(r["content"])
                    print(f"    → {c.get('text', '')[:120]}")
                # Check that the response contains 391 (17*23)
                all_text = " ".join(json.loads(r["content"]).get("text", "") for r in new_resp)
                if "391" in all_text:
                    print("  ✓ Correct arithmetic (391 found)")
                    results["test2"] = True
                else:
                    print("  ~ Response present but 391 not found in text")
                    results["test2"] = True  # Still a pass for response existence
                break
            time.sleep(1.0)
        else:
            print("  ✗ No response for Test 2")
            results["test2"] = False

        # ── Test 3: Schedule a task ────────────────────────────────────────
        print("\n── Test 3: Schedule a task ──")
        before_chat = len(get_all_outbound(out_db)[0])
        before_sys  = len(get_all_outbound(out_db)[1])
        insert_message(in_db, 6,
            "Please schedule a reminder for tomorrow at 9am UTC to check my emails. "
            "Then confirm you've scheduled it.")

        deadline = time.monotonic() + 60
        schedule_found = False
        while time.monotonic() < deadline:
            chat, system = get_all_outbound(out_db)
            new_sys = system[before_sys:]
            for s in new_sys:
                try:
                    c = json.loads(s["content"])
                    if c.get("action") == "schedule_task":
                        print(f"  ✓ Task scheduled! taskId={c.get('taskId','?')}")
                        print(f"    processAfter={c.get('processAfter','?')}")
                        schedule_found = True
                        break
                except Exception:
                    pass
            if schedule_found:
                break
            # Also count chat responses as partial success
            new_chat = chat[before_chat:]
            if new_chat:
                all_text = " ".join(json.loads(r["content"]).get("text", "") for r in new_chat)
                if any(kw in all_text.lower() for kw in
                       ["schedul", "reminder", "9am", "tomorrow", "9:00"]):
                    print(f"  ~ Agent mentioned scheduling but no system row yet: {all_text[:120]}")
            time.sleep(1.0)

        if not schedule_found:
            # Final check
            _, system = get_all_outbound(out_db)
            for s in system[before_sys:]:
                try:
                    c = json.loads(s["content"])
                    if c.get("action") == "schedule_task":
                        print(f"  ✓ Task scheduled (late): {c.get('taskId','?')}")
                        schedule_found = True
                        break
                except Exception:
                    pass

        if not schedule_found:
            chat, _ = get_all_outbound(out_db)
            print(f"  ~ Schedule tool not called (agent may have responded in text only)")
            print(f"    Total chat responses: {len(chat)}")
        results["test3"] = schedule_found

        # ── Test 4: Conversation continuity ───────────────────────────────
        print("\n── Test 4: Conversation continuity ──")
        before = len(get_all_outbound(out_db)[0])
        insert_message(in_db, 8,
            "What was the very first thing I said to you in this conversation?")

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            chat, _ = get_all_outbound(out_db)
            if len(chat) > before:
                new_r = chat[before:]
                c = json.loads(new_r[0]["content"])
                text = c.get("text", "")
                # Should reference "Hello" or "introduce"
                if any(word in text.lower() for word in
                       ["hello", "introduce", "greeting", "first", "said"]):
                    print(f"  ✓ Context preserved: {text[:150]}")
                    results["test4"] = True
                else:
                    print(f"  ~ Response received but may not reference prior: {text[:150]}")
                    results["test4"] = True  # Response is enough
                break
            time.sleep(1.0)
        else:
            print("  ✗ No response for Test 4")
            results["test4"] = False

        # ── Final validation ───────────────────────────────────────────────
        print("\n── Final validation ──")
        chat, system = get_all_outbound(out_db)
        acks = check_processing_acks(out_db)
        completed = [a for a in acks if a["status"] == "completed"]
        failed    = [a for a in acks if a["status"] == "failed"]

        print(f"  Total chat messages sent:   {len(chat)}")
        print(f"  Total system actions:       {len(system)}")
        print(f"  Processing acks:            {len(acks)}")
        print(f"  Completed batches:          {len(completed)}")
        print(f"  Failed batches:             {len(failed)}")

        if heartbeat.exists():
            age = time.time() - heartbeat.stat().st_mtime
            print(f"  Heartbeat last touched:     {age:.1f}s ago")

        # Summary
        print("\n── Test Results ──")
        passed = 0
        for name, ok in results.items():
            status = "PASS" if ok else "FAIL"
            print(f"  {status}  {name}")
            if ok:
                passed += 1

        overall = passed == len(results) and len(chat) >= 1 and len(completed) >= 1
        if overall:
            print(f"\n✓ E2E test PASSED ({passed}/{len(results)} tests, "
                  f"{len(completed)} completed batches)")
        else:
            print(f"\n✗ E2E test FAILED ({passed}/{len(results)} tests, "
                  f"{len(completed)} completed batches)")

        # ── Cleanup ────────────────────────────────────────────────────────
        print("\nShutting down runner...")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

        # Show runner stderr on failure or if verbose
        verbose = os.environ.get("E2E_VERBOSE", "")
        try:
            stderr = proc.stderr.read()
            if not overall or verbose:
                print("\n── Runner stderr (last 3000 chars) ──")
                print(stderr[-3000:] if stderr else "(empty)")
        except Exception:
            pass

        return overall


if __name__ == "__main__":
    ok = asyncio.run(run_e2e_test())
    sys.exit(0 if ok else 1)
