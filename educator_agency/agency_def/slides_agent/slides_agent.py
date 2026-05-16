from agency_swarm import Agent

from educator_agency.agency_def.tools.backend_tools import ListFilesTool, ReadFileTool
from educator_agency.agency_def.slides_agent.generate_slides import GenerateEducatorSlides


def create_educator_slides_agent() -> Agent:
    return Agent(
        name="SlidesAgent",
        description=(
            "Generates lecture slide decks from a structured PLAN.md, PEDAGOGY.md, "
            "and the course style.css. Produces slides.pptx with speaker notes."
        ),
        instructions="./instructions.md",
        tools=[ReadFileTool, ListFilesTool, GenerateEducatorSlides],
    )
