"""External ingestion adapters; every result enters the pending review queue."""

import logging
from src.simulation.database import save_case

logger = logging.getLogger(__name__)


async def import_cases(payload, source, client):
    if source == "agentclinic":
        from src.simulation.case_sources.agentclinic import import_agentclinic

        cases = await import_agentclinic(dataset=payload.dataset, max_cases=payload.max_cases)
        reference = payload.dataset
    elif source == "pubmed":
        from src.simulation.case_sources.pubmed import (
            search_pubmed_cases,
            fetch_pubmed_abstracts,
            abstract_to_case,
        )

        ids = await search_pubmed_cases(payload.specialty, max_results=payload.max_results)
        abstracts = await fetch_pubmed_abstracts(ids)
        cases = []
        for abstract in abstracts:
            data = await abstract_to_case(abstract, client)
            data["_source_ref"] = str(abstract["pmid"])
            cases.append(data)
        reference = payload.specialty
    elif source == "wiley":
        from src.simulation.case_sources.wiley import search_wiley_cases, wiley_article_to_case

        articles = await search_wiley_cases(query=payload.query, max_results=payload.max_results)
        cases = []
        for article in articles:
            if article.get("abstract"):
                data = await wiley_article_to_case(article, client)
                data["_source_ref"] = article["doi"]
                cases.append(data)
        reference = payload.query
    else:
        from src.simulation.case_sources.endless_medical import build_case_from_disease

        cases = [await build_case_from_disease(payload.disease, client)]
        reference = payload.disease
    results = []
    for case in cases:
        save_case(case, source=source, source_ref=case.get("_source_ref", reference))
        results.append({"case_id": case["case_id"], "title": case["title"], "status": "pending"})
    return {"imported": len(results), "cases": results}


async def generate(payload, client):
    from .models import ImportRequest

    if payload.source != "auto":
        imported = await import_cases(
            ImportRequest(
                specialty=payload.topic, query=payload.topic, disease=payload.topic, max_results=1
            ),
            payload.source,
            client,
        )
        if imported["cases"]:
            return imported["cases"][0]
        raise ValueError("No matching source case found")
    from src.simulation.case_sources.ai_generator import generate_case

    case = await generate_case(
        client,
        f"Create a fictional educational practice case about: {payload.topic}. Do not include real personal identifiers.",
        source_type="ai_generated",
    )
    save_case(case, source="ai_generated")
    return {"case_id": case["case_id"], "title": case["title"], "status": "pending"}
