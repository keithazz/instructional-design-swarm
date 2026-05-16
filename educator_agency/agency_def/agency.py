"""Educator-agency definition.

Creates the five-agent Agency instance used by the educator-agency server.
The original `swarm.py` and its eight-agent OpenSwarm agency are untouched;
this module coexists alongside it.

Agent topology (§5 of POC_DESIGN.md):
  - OrchestratorAgent     — adapted, educator-domain instructions
  - CourseDesignerAgent   — new
  - DeepResearchAgent     — existing, citation-format rewrite pending (§6.9)
  - LessonPlannerAgent    — new
  - SlidesAgent           — adapted, full rewrite in progress (§6.7, §6.8)

Communication: SendMessage only (per user decision, plan §5.1).
No Handoff flows — Orchestrator stays in the loop for all approval gates.

Per `.claude/plans/i-want-to-start-stateless-oasis.md` §6.5.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def create_educator_agency(load_threads_callback=None):
    from agency_swarm import Agency
    from agency_swarm.tools import SendMessage

    from educator_agency.agency_def.orchestrator.orchestrator import (
        create_educator_orchestrator,
    )
    from educator_agency.agency_def.course_designer.course_designer import (
        create_course_designer,
    )
    from educator_agency.agency_def.lesson_planner.lesson_planner import (
        create_lesson_planner,
    )

    from educator_agency.agency_def.slides_agent.slides_agent import (
        create_educator_slides_agent,
    )
    from educator_agency.agency_def.deep_research.deep_research import (
        create_educator_deep_research,
    )

    orchestrator = create_educator_orchestrator()
    course_designer = create_course_designer()
    lesson_planner = create_lesson_planner()
    deep_research = create_educator_deep_research()
    slides_agent = create_educator_slides_agent()

    specialists = [course_designer, deep_research, lesson_planner, slides_agent]

    # SendMessage only: orchestrator remains in the loop for every turn.
    # Specialists do not message each other directly.
    communication_flows = [
        (orchestrator, specialist, SendMessage)
        for specialist in specialists
    ]

    return Agency(
        orchestrator,
        *specialists,
        communication_flows=communication_flows,
        name="educator-agency",
        shared_instructions=str(
            Path(__file__).parent / "shared_instructions.md"
        ),
        load_threads_callback=load_threads_callback,
    )
