from agency_swarm import Agent

from educator_agency.agency_def.tools.backend_tools import (
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)


def create_lesson_planner() -> Agent:
    return Agent(
        name="LessonPlanner",
        description=(
            "Synthesizes research notes and PEDAGOGY.md into a structured PLAN.md "
            "for a given lesson. Consumed by SlidesAgent."
        ),
        instructions="./instructions.md",
        tools=[ReadFileTool, WriteFileTool, ListFilesTool],
    )
