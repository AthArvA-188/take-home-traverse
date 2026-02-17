import logging
import tempfile
from pathlib import Path

import verifiers as vf
from verifiers.envs.experimental.harbor_env import HarborEnv

logger = logging.getLogger("verifiers.envs.SweHarborEnv")


class SweHarborEnv(HarborEnv):
    """Harbor environment for SWE intern take-home tasks.

    This environment reuses all Harbor task loading, test execution, and reward
    computation from the base HarborEnv class.  It runs a tool-use agent inside
    the Docker container that can execute bash commands and edit files to solve
    the task.
    """

    def __init__(
        self,
        dataset_path: str | Path,
        tasks: list[str] | None = None,
        agent_workdir: str = "/app",
        docker_image: str = "python:3.11-slim",
        **kwargs,
    ):
        run_command = "python /app/agent.py 2>&1"

        super().__init__(
            run_command=run_command,
            dataset_path=dataset_path,
            tasks=tasks,
            agent_workdir=agent_workdir,
            docker_image=docker_image,
            **kwargs,
        )

    async def build_env_vars(self, state: vf.State) -> dict[str, str]:
        """Configure the OpenAI SDK inside the container to use OpenRouter."""
        import os

        env_vars = await super().build_env_vars(state)
        env_vars.setdefault(
            "OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
        )
        env_vars.setdefault(
            "OPENAI_API_KEY",
            os.environ.get("OPENROUTER_API_KEY", ""),
        )
        return env_vars

    async def post_sandbox_setup(self, state: vf.State) -> None:
        """Upload Harbor task assets then install openai and upload the agent script."""
        await super().post_sandbox_setup(state)

        sandbox_id = state["sandbox_id"]

        # Install openai SDK inside the container
        await self.sandbox_client.execute_command(
            sandbox_id,
            "pip install openai >/dev/null 2>&1",
            working_dir=None,
            timeout=120,
        )

        # Write agent script to a temp file and upload it
        agent_script = self._get_agent_script()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp:
            tmp.write(agent_script)
            tmp_path = tmp.name

        try:
            await self.sandbox_client.upload_file(
                sandbox_id, "/app/agent.py", tmp_path
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @staticmethod
    def _get_agent_script() -> str:
        """Return a self-contained Python agent script for execution inside the container."""
        return r'''#!/usr/bin/env python3
"""Tool-use agent that reads the task instruction and solves it."""

import json
import os
import subprocess
import sys

from openai import OpenAI

MAX_TURNS = 50
MODEL = os.environ.get("OPENAI_MODEL", "openai/gpt-4o")

SYSTEM_PROMPT = (
    "You are a skilled software engineer. You have access to tools for running "
    "bash commands and editing files. Read /task/instruction.md to understand "
    "the task, then complete it. Work step by step. When you are done, call no "
    "more tools."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command and return stdout, stderr, and exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read and return the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content. Creates parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "Replace exactly one occurrence of a string in a file. Fails if the string appears zero or more than one time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact string to find (must appear exactly once).",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The replacement string.",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
]


# ── tool implementations ──────────────────────────────────────────────


def tool_bash(command: str) -> str:
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=120
    )
    parts = []
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    parts.append(f"exit_code: {result.returncode}")
    return "\n".join(parts)


def tool_read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception as e:
        return f"Error reading {path}: {e}"


def tool_write_file(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


def tool_str_replace(path: str, old_str: str, new_str: str) -> str:
    try:
        with open(path) as f:
            content = f.read()
    except Exception as e:
        return f"Error reading {path}: {e}"

    count = content.count(old_str)
    if count == 0:
        return f"Error: old_str not found in {path}"
    if count > 1:
        return f"Error: old_str appears {count} times in {path} (must be exactly 1)"

    new_content = content.replace(old_str, new_str, 1)
    with open(path, "w") as f:
        f.write(new_content)
    return f"Replaced 1 occurrence in {path}"


TOOL_DISPATCH = {
    "bash": lambda args: tool_bash(args["command"]),
    "read_file": lambda args: tool_read_file(args["path"]),
    "write_file": lambda args: tool_write_file(args["path"], args["content"]),
    "str_replace": lambda args: tool_str_replace(
        args["path"], args["old_str"], args["new_str"]
    ),
}


# ── main loop ─────────────────────────────────────────────────────────


def main():
    client = OpenAI()  # picks up OPENAI_BASE_URL and OPENAI_API_KEY from env

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Read /task/instruction.md and complete the task.",
        },
    ]

    for turn in range(MAX_TURNS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )

        choice = response.choices[0]
        assistant_msg = choice.message

        # Append assistant message to history
        messages.append(assistant_msg.model_dump(exclude_none=True))

        if not assistant_msg.tool_calls:
            # Agent is done
            if assistant_msg.content:
                print(assistant_msg.content)
            break

        # Execute each tool call
        for tool_call in assistant_msg.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            handler = TOOL_DISPATCH.get(fn_name)
            if handler is None:
                result = f"Unknown tool: {fn_name}"
            else:
                try:
                    result = handler(fn_args)
                except Exception as e:
                    result = f"Error executing {fn_name}: {e}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
    else:
        print("Agent reached maximum number of turns.")


if __name__ == "__main__":
    main()
'''


def load_environment(
    dataset_path: str | Path = Path(__file__).parent / "tasks",
    tasks: list[str] | None = None,
    agent_workdir: str = "/app",
    docker_image: str = "python:3.11-slim",
    timeout_seconds: float = 900.0,
    cpu_cores: int = 2,
    memory_gb: int = 4,
    disk_size_gb: int = 10,
    timeout_minutes: int = 60,
    max_turns: int = 30,
) -> SweHarborEnv:
    return SweHarborEnv(
        dataset_path=dataset_path,
        tasks=tasks,
        agent_workdir=agent_workdir,
        docker_image=docker_image,
        timeout_seconds=timeout_seconds,
        cpu_cores=cpu_cores,
        memory_gb=memory_gb,
        disk_size_gb=disk_size_gb,
        timeout_minutes=timeout_minutes,
        max_turns=max_turns,
    )
