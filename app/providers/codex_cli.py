"""OpenAI Codex CLI adapter (subscription mode).

Spawns `codex exec --json --sandbox read-only --skip-git-repo-check
--ephemeral --color never -` per request and reads prompt from stdin.
Scrubs every env var that would route the CLI onto API billing.
"""
from __future__ import annotations
import asyncio
import json as _json
import os
import shutil
import tempfile
from typing import AsyncIterator, Optional

from pathlib import Path

from .base import HealthResult, Message, ProviderAdapter, StreamChunk
from .cli_detect import find_binary
from ..settings import CLI_REQUEST_TIMEOUT_SEC
from .claude_cli import scrub_env  # same scrubbing rules


class CodexCLIAdapter(ProviderAdapter):
    kind = "codex_cli"

    def _binary(self) -> str:
        return self.config.get("cli_path") or find_binary("codex") or "codex"

    def _build_prompt(self, messages: list[Message], system: Optional[str]) -> str:
        sys_text, rest = self._split_system(messages, system)
        parts = []
        if sys_text:
            parts.append(f"[System]\n{sys_text}")
        parts.append(
            "[Instructions]\nYou are a chat assistant. Answer the user's last message in plain prose."
            " Do NOT run shell commands, do NOT modify files, do NOT use tools. Reply with text only."
        )
        for m in rest:
            label = "User" if m.role == "user" else "Assistant"
            parts.append(f"[{label}]\n{m.content}")
        parts.append("[Assistant]\n")
        return "\n\n".join(parts)

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        binary = self._binary()
        if not binary or not Path(binary).is_file():
            yield StreamChunk(error="codex CLI 未安装。请在终端运行 `npm i -g @openai/codex`,然后重启 Ask。", done=True)
            return

        prompt_text = self._build_prompt(messages, system)
        last_msg_file = tempfile.NamedTemporaryFile(prefix="codex-last-", delete=False)
        last_msg_file.close()

        args = [
            binary,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "--output-last-message",
            last_msg_file.name,
            "-",
        ]
        if model_id:
            args[2:2] = ["-m", model_id]

        env = scrub_env()
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd="/tmp",
        )
        try:
            assert proc.stdin
            proc.stdin.write(prompt_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        emitted_so_far = ""

        async def _read_stderr() -> bytes:
            assert proc.stderr
            return await proc.stderr.read()

        stderr_task = asyncio.create_task(_read_stderr())

        try:
            assert proc.stdout
            while True:
                if cancel_event and cancel_event.is_set():
                    proc.terminate()
                    yield StreamChunk(done=True)
                    return
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=CLI_REQUEST_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    proc.terminate()
                    yield StreamChunk(error="codex CLI 超时", done=True)
                    return
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    payload = _json.loads(line_str)
                except _json.JSONDecodeError:
                    continue

                # codex 0.x emitted {"msg": {"type": "agent_message_delta", "delta": "..."}}
                # codex 0.124+ emits JSON-RPC: {"method": "item/agentMessage/delta",
                #   "params": {"itemId": "...", "delta": "..."}}
                # Handle both shapes by extracting "method/type" and "msg/params".
                method = payload.get("method") or ""
                inner = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                if not method:
                    msg = payload.get("msg") or {}
                    method = msg.get("type") or payload.get("type") or ""
                    inner = msg or inner

                method_lc = str(method).lower().replace("/", ".").replace("_", ".")
                is_delta = method_lc.endswith(".delta") or method_lc.endswith("agent.message.delta")
                is_final = method_lc in (
                    "agent.message", "item.agent.message.completed",
                    "task.complete", "turn.completed",
                ) or method_lc.endswith(".agent.message.completed")
                is_error = (
                    "error" in method_lc or method_lc.endswith(".failed")
                    or method_lc == "turn.failed"
                )

                if is_delta:
                    delta = inner.get("delta") or inner.get("text") or ""
                    if isinstance(delta, str) and delta:
                        emitted_so_far += delta
                        yield StreamChunk(delta=delta)
                elif is_final:
                    full = (
                        inner.get("text")
                        or inner.get("message")
                        or inner.get("last_agent_message")
                        or inner.get("output")
                        or ""
                    )
                    if isinstance(full, str) and full:
                        if len(full) > len(emitted_so_far) and full.startswith(emitted_so_far):
                            yield StreamChunk(delta=full[len(emitted_so_far):])
                            emitted_so_far = full
                        elif not emitted_so_far:
                            yield StreamChunk(delta=full)
                            emitted_so_far = full
                    if method_lc in ("task.complete", "turn.completed"):
                        break
                elif is_error:
                    err = (
                        inner.get("message")
                        or inner.get("error")
                        or inner.get("reason")
                        or _json.dumps(inner, ensure_ascii=False) if inner else ""
                    )
                    if not err:
                        err = _json.dumps(payload, ensure_ascii=False)[:300]
                    yield StreamChunk(error=f"codex: {err}"[:500], done=True)
                    return

            await proc.wait()
            stderr_bytes = await stderr_task

            # Fallback: read --output-last-message file if streaming missed deltas
            if not emitted_so_far:
                try:
                    with open(last_msg_file.name, "r", encoding="utf-8") as f:
                        final = f.read().strip()
                    if final:
                        yield StreamChunk(delta=final)
                except OSError:
                    pass

            if proc.returncode and proc.returncode != 0 and not emitted_so_far:
                err = stderr_bytes.decode("utf-8", errors="replace").strip()[:500] or f"codex exit {proc.returncode}"
                yield StreamChunk(error=f"codex: {err}", done=True)
                return
            if not emitted_so_far:
                err = stderr_bytes.decode("utf-8", errors="replace").strip()[:500]
                if err:
                    yield StreamChunk(error=f"codex 没有输出: {err}", done=True)
                    return
            yield StreamChunk(done=True)
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            try:
                os.unlink(last_msg_file.name)
            except OSError:
                pass

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        binary = self._binary()
        if not binary or not Path(binary).is_file():
            return HealthResult(False, "codex CLI 未安装(npm i -g @openai/codex)")
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=scrub_env(),
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                ver = out.decode("utf-8", errors="replace").strip()
                return HealthResult(True, f"CLI 可用: {ver[:80]}")
            return HealthResult(False, "CLI 返回非零", err.decode("utf-8", errors="replace")[:200])
        except (asyncio.TimeoutError, FileNotFoundError) as e:
            return HealthResult(False, "CLI 调用失败", str(e))
