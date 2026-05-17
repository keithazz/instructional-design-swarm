from agency_swarm import Agent

from educator_agency.agency_def.course_designer.extract_style import (
    ExtractStyleFromPptx,
)
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
            "before drafting or updating the course outline. Also extracts a "
            "candidate style.css from an educator-supplied PPTX when asked."
        ),
        instructions="./instructions.md",
        tools=[ReadFileTool, WriteFileTool, ListFilesTool, ExtractStyleFromPptx],
    )
