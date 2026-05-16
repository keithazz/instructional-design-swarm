from agency_swarm import Agent, ModelSettings
from agency_swarm.tools import WebSearchTool, IPythonInterpreter
from openai.types.shared import Reasoning
from virtual_assistant.tools.ScholarSearch import ScholarSearch

from educator_agency.agency_def.tools.backend_tools import (
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from config import get_default_model, is_openai_provider


def create_educator_deep_research() -> Agent:
    return Agent(
        name="DeepResearchAgent",
        description=(
            "Conducts academic research for a single lesson and writes research.md "
            "to the course directory via the approval gate. Prioritises primary academic "
            "sources; uses numbered footnote citations."
        ),
        instructions="./instructions.md",
        tools=[
            WebSearchTool(),
            ScholarSearch,
            IPythonInterpreter,
            ReadFileTool,
            WriteFileTool,
            ListFilesTool,
        ],
        model=get_default_model(),
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="high", summary="auto") if is_openai_provider() else None,
            response_include=["web_search_call.action.sources"] if is_openai_provider() else None,
        ),
    )
