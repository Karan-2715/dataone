from fastapi import APIRouter
from typing import List, Dict, Any

router = APIRouter()

@router.get("/models")
def get_supported_models() -> List[Dict[str, Any]]:
    # A curated whitelist of Databricks models best suited for Schema Mapping
    return [
        {
            "id": "databricks-meta-llama-3-3-70b-instruct",
            "name": "Llama 3.3 70B Instruct (Recommended)",
            "description": "Best reasoning and structured output for complex schemas."
        },
        {
            "id": "databricks-meta-llama-3-1-70b-instruct",
            "name": "Llama 3.1 70B Instruct",
            "description": "Excellent reasoning, slightly older version."
        },
        {
            "id": "databricks-dbrx-instruct",
            "name": "DBRX Instruct",
            "description": "Databricks native model, good structured output."
        },
        {
            "id": "databricks-mixtral-8x7b-instruct",
            "name": "Mixtral 8x7B Instruct",
            "description": "Faster, acceptable for simpler schema mappings."
        }
    ]
