"""Case sources — dynamic case fetching from online medical literature."""

from .pubmed import search_pubmed_cases, fetch_pubmed_abstract, abstract_to_case
from .endless_medical import build_case_from_disease, get_available_diseases
from .wiley import search_wiley_cases
from .ai_generator import generate_case, CASE_STRUCTURING_PROMPT
from .agentclinic import import_agentclinic, download_dataset, DATASET_URLS

__all__ = [
    "search_pubmed_cases",
    "fetch_pubmed_abstract",
    "abstract_to_case",
    "build_case_from_disease",
    "get_available_diseases",
    "search_wiley_cases",
    "generate_case",
    "CASE_STRUCTURING_PROMPT",
    "import_agentclinic",
    "download_dataset",
    "DATASET_URLS",
]
