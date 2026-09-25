from __future__ import annotations

import json

import anyio
from mcp import types


def add_skill_prompts(server, library, location):
    def render(identifier, repository="", task=""):
        if len(task) > 20000 or len(repository) > 1000 or len(identifier) > 255:
            raise ValueError("Skill command arguments exceed the size limits")
        workspace, repository = location(
            {"repository": repository} if repository else {}
        )
        skill = library.one(workspace, identifier, repository)
        if skill is None:
            raise ValueError("Skill not found in this scope")
        content = json.dumps({**skill.content, "instructions": skill.body})
        if len(content.encode()) > 100000:
            raise ValueError("Skill content exceeds the command size limit")
        text = f"Apply the {skill.name} skill to the requested task.\n\n{skill.body}"
        extra = {
            key: value for key, value in skill.content.items() if key != "instructions"
        }
        if extra:
            text += "\n\nSupporting content:\n" + json.dumps(extra)
        if skill.paths:
            text += "\n\nFile patterns: " + json.dumps(list(skill.paths))
        if repository:
            text += f"\n\nRepository: {repository}"
        if task:
            text += f"\n\nRequested task:\n{task}"
        else:
            text += "\n\nUse the current task. Ask what to work on if no task is clear."
        return types.GetPromptResult(
            description=skill.description[:2000],
            messages=[
                types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text=text)
                )
            ],
        )

    @server.prompt(
        name="use_skill",
        title="Use a skill",
        description="Apply a named skill to a task, optionally from a repository.",
    )
    async def use_skill(id: str, repository: str = "", task: str = "") -> str:
        result = await anyio.to_thread.run_sync(render, id, repository, task)
        return result.messages[0].content.text

    @server.prompt(
        name="choose_skill",
        title="Choose a skill for my task",
        description="Find a relevant skill and choose which instructions to apply.",
    )
    def choose_skill(task: str, repository: str = "") -> str:
        if len(task) > 20000 or len(repository) > 1000:
            raise ValueError("Skill command arguments exceed the size limits")
        scope = {"repository": repository} if repository else {}
        return (
            f"Find skills for this task:\n{task}\n\n"
            f"Use search_skills with scope {json.dumps(scope)}. "
            "Show the relevant skills and let me choose one. Then use get_skill "
            "with the same scope and apply its full instructions to my task. "
            "If the result is truncated, request the full content before using it."
        )

    @server._mcp_server.list_prompts()
    async def list_prompts(
        request: types.ListPromptsRequest,
    ) -> types.ListPromptsResult:
        cursor = request.params.cursor if request.params else None
        if cursor is not None and (not cursor.isdecimal() or len(cursor) > 10):
            raise ValueError("Invalid prompt cursor")
        offset = int(cursor or 0)

        def available():
            workspace, repository = location({})
            return library.all(workspace, repository)

        skills = await anyio.to_thread.run_sync(available)
        prompts = await server.list_prompts()
        prompts.extend(
            types.Prompt(
                name=f"skill:{skill.id}",
                title=skill.name,
                description=skill.description[:2000],
                arguments=[
                    types.PromptArgument(name="task", description="What to work on"),
                    types.PromptArgument(
                        name="repository",
                        description="Repository whose skill override to use",
                    ),
                ],
            )
            for skill in sorted(skills, key=lambda skill: skill.id)
        )
        return types.ListPromptsResult(
            prompts=prompts[offset : offset + 50],
            nextCursor=str(offset + 50) if offset + 50 < len(prompts) else None,
        )

    @server._mcp_server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None):
        if not name.startswith("skill:"):
            return await server.get_prompt(name, arguments)
        arguments = arguments or {}
        if set(arguments) - {"repository", "task"}:
            raise ValueError("Unknown skill command arguments")
        return await anyio.to_thread.run_sync(
            render,
            name.removeprefix("skill:"),
            arguments.get("repository", ""),
            arguments.get("task", ""),
        )
