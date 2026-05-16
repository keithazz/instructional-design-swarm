from agency_swarm import Agent

from educator_agency.agency_def.tools.backend_tools import ListFilesTool, ReadFileTool


def create_educator_orchestrator() -> Agent:
    return Agent(
        name="Orchestrator",
        description=(
            "Primary coordinator for the Educator Agency. Routes user requests "
            "to specialists and mediates file-write approvals. Never produces "
            "course content directly."
        ),
        instructions="./instructions.md",
        tools=[ReadFileTool, ListFilesTool],
    )
