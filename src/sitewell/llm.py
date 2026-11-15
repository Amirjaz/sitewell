"""Calling Claude for the steps that need judgment.

Each call is a headless Claude Code run with sitewell's own short instructions in place of
the default ones, a single tool (Read, inside one workspace folder), and a JSON schema the
answer must match. The model sees files; it cannot browse, run commands or reach a site.
Every call's tokens and its cost at API prices are recorded.

The same command runs on a Claude subscription token (CLAUDE_CODE_OAUTH_TOKEN) or an API
key (ANTHROPIC_API_KEY); Claude Code picks whichever is set.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

MODELS = {
    "decide": "claude-opus-5-5",
    "produce": "claude-opus-5-5",
    "check": "claude-haiku-5-5",  # mechanical checks run on the smallest model
}


def model_for(step: str) -> str:
    return os.environ.get(f"SITEWELL_MODEL_{step.upper()}") or MODELS[step]


@dataclass
class Call:
    step: str
    model: str
    ok: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    turns: int | None = None
    seconds: float | None = None
    cost_usd: float | None = None
    tokens: dict[str, int] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "output"}


class Runner(Protocol):
    def __call__(self, step: str, prompt: str, workdir: Path, system: str, schema: dict[str, Any], model: str, max_turns: int) -> Call: ...


def claude_code(step: str, prompt: str, workdir: Path, system: str, schema: dict[str, Any], model: str, max_turns: int, timeout: int = 900) -> Call:
    """One `claude -p` run. Never raises: a failure is a Call with ok=False."""
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--tools", "Read",
        "--system-prompt", system,
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--no-session-persistence",
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SITEWELL_GSC", "GOOGLE_"))}  # no Google secrets near the model
    try:
        done = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except FileNotFoundError:
        return Call(step, model, False, error="the claude command is not installed")
    except subprocess.TimeoutExpired:
        return Call(step, model, False, error=f"timed out after {timeout} s")
    try:
        doc = json.loads(done.stdout)
    except ValueError:
        return Call(step, model, False, error=f"no JSON from claude (exit {done.returncode}): {(done.stderr or done.stdout)[-300:]}")
    if isinstance(doc, list):
        doc = next((d for d in reversed(doc) if isinstance(d, dict) and d.get("type") == "result"), doc[-1])
    usage = doc.get("usage") or {}
    call = Call(
        step,
        model,
        ok=not doc.get("is_error") and isinstance(doc.get("structured_output"), dict),
        output=doc.get("structured_output"),
        turns=doc.get("num_turns"),
        seconds=round((doc.get("duration_ms") or 0) / 1000, 1),
        cost_usd=doc.get("total_cost_usd"),
        tokens={k: int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")},
    )
    if not call.ok:
        call.error = str(doc.get("result") or doc.get("subtype") or "no structured output")[:400]
    return call


def run(step: str, prompt: str, workdir: Path, system: str, schema: dict[str, Any], max_turns: int = 12, runner: Runner | None = None, validate: Callable[[dict[str, Any]], list[str]] | None = None) -> Call:
    """Run a step; if the answer breaks the schema or a check, ask once more with the problems listed."""
    runner = runner or claude_code
    model = model_for(step)
    call = runner(step, prompt, workdir, system, schema, model, max_turns)
    problems = (validate(call.output) if (call.ok and validate) else [])
    if call.ok and not problems:
        return call
    retry_prompt = prompt + "\n\nYour previous answer could not be used:\n" + "\n".join(f"- {p}" for p in (problems or [call.error or "no answer"]))
    second = runner(step, retry_prompt, workdir, system, schema, model, max_turns)
    second.cost_usd = (second.cost_usd or 0) + (call.cost_usd or 0)
    for k, v in call.tokens.items():
        second.tokens[k] = second.tokens.get(k, 0) + v
    if second.ok and validate:
        left = validate(second.output)
        if left:
            second.ok, second.error = False, "; ".join(left)[:400]
    return second
