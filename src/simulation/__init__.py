"""Clinical simulation module — resident training environment."""

from .simulator import SimulationEngine, get_simulation_engine
from .cases import list_cases, get_case
from .scorer import score_session, ScoreResult
from .debrief import generate_debrief, DebriefResult
from .vllm_client import VLLMClient

__all__ = [
    "SimulationEngine",
    "get_simulation_engine",
    "list_cases",
    "get_case",
    "score_session",
    "ScoreResult",
    "generate_debrief",
    "DebriefResult",
    "VLLMClient",
]
