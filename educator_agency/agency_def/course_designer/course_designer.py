from agency_swarm import Agent

from educator_agency.agency_def.tools.backend_tools import (
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)


def create_course_designer() -> Agent:
    return Agent(
        name="CourseDesigner",
        description=(
            "Interactively co-creates COURSE.md with the educator. "
            "Asks clarifying questions about scope, audience, and learning objectives "
            "before drafting or updating the course outline."
        ),
        instructions="./instructions.md",
        tools=[ReadFileTool, WriteFileTool, ListFilesTool],
    )
