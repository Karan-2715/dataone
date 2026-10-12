import logging
import json
import math
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.services.databricks_llm_provider import get_databricks_llm_provider
from app.core.config import settings

logger = logging.getLogger(__name__)

# ── 1. Pydantic Models for LLM Structured Output ───────────────────────────

class LLMMappingResponse(BaseModel):
    target_column: str = Field(description="The exact name of the target column matched.")
    semantic_score: float = Field(description="Semantic match score between 0.0 and 1.0.")
    reasoning: str = Field(description="Why this column was chosen.")

class ReMatchEngine:
    """
    ReMatch Schema Mapping Engine.
    Uses hybrid scoring (In-memory Vector similarity + Rule-based + LLM semantic reasoning).
    """

    def __init__(self, db: Session, user_llm_model: Optional[str] = None, workspace_url: Optional[str] = None, access_token: Optional[str] = None):
        self.db = db
        # Use user's selected model or fallback to default
        self.llm_model = user_llm_model or settings.DATABRICKS_LLM_ENDPOINT or "databricks-meta-llama-3-3-70b-instruct"
        self.embedding_endpoint = "databricks-bge-large-en"
        self.workspace_url = workspace_url or settings.DATABRICKS_WORKSPACE_URL
        self.access_token = access_token or settings.DATABRICKS_ACCESS_TOKEN

    def _get_embedding(self, text: str) -> List[float]:
        """Call Databricks Model Serving to get vector embedding."""
        import requests
        import os
        
        is_databricks_apps = bool(
            os.environ.get("DATABRICKS_CLIENT_ID")
            or os.environ.get("DATABRICKS_CLIENT_SECRET")
            or os.environ.get("DATABRICKS_HOST")
        )
        
        token = self.access_token
        host_url = self.workspace_url
        
        try:
            if is_databricks_apps:
                # Fetch Databricks Apps M2M OAuth Token manually
                host = os.environ.get("DATABRICKS_HOST")
                client_id = os.environ.get("DATABRICKS_CLIENT_ID")
                client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET")
                
                resp = requests.post(
                    f"https://{host.rstrip('/')}/oidc/v1/token",
                    auth=(client_id, client_secret),
                    data={"grant_type": "client_credentials", "scope": "all-apis"},
                    timeout=10
                )
                if resp.status_code == 200:
                    token = resp.json()["access_token"]
                    host_url = f"https://{host}"
                else:
                    logger.error(f"Failed to fetch Databricks Apps token: {resp.text}")
                    raise ValueError(f"OAuth token fetch failed: {resp.text}")
            else:
                if not token or not host_url:
                    raise ValueError("Workspace URL and access token required outside Databricks Apps")

            url = f"{host_url.rstrip('/')}/serving-endpoints/{self.embedding_endpoint}/invocations"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            
            # Databricks Foundation Model API uses OpenAI-compatible "input" field
            resp = requests.post(url, headers=headers, json={"input": [text]}, timeout=10)
            if resp.status_code != 200:
                raise ValueError(f"Embedding API returned status {resp.status_code}: {resp.text}")
                
            response = resp.json()
            
            # Extract embeddings from response
            # OpenAI-compatible Foundation Model API returns: {"data": [{"embedding": [...]}]}
            if "data" in response and len(response["data"]) > 0:
                return response["data"][0].get("embedding", [])
            elif "predictions" in response and len(response["predictions"]) > 0:
                # Older Model Serving format
                return response["predictions"][0]
            else:
                logger.error(f"Unexpected embedding response format: {response}")
                raise ValueError("Unexpected response format from embedding API")
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))
        if magnitude1 == 0.0 or magnitude2 == 0.0:
            return 0.0
        return dot_product / (magnitude1 * magnitude2)

    def _type_compatibility_score(self, src_type: str, tgt_type: str) -> float:
        """Rule-based Data Type Compatibility Matrix (Returns 0.0 to 1.0)."""
        src = str(src_type).lower()
        tgt = str(tgt_type).lower()
        
        # Exact match
        if src == tgt:
            return 1.0
            
        # Numeric grouping
        numerics = {"int", "integer", "bigint", "smallint", "decimal", "numeric", "float", "double"}
        if src in numerics and tgt in numerics:
            return 0.9
            
        # String/Text grouping
        strings = {"varchar", "text", "string", "char"}
        if any(s in src for s in strings) and any(t in tgt for t in strings):
            return 1.0
            
        # String can hold anything (coercion)
        if any(t in tgt for t in strings):
            return 0.5
            
        return 0.0

    def map_schemas(self, source_schema: Dict[str, List[Dict[str, Any]]], target_schema: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        llm_provider = get_databricks_llm_provider(
            endpoint_name=self.llm_model,
            workspace_url=self.workspace_url,
            access_token=self.access_token
        )
        
        # Build context for LLM
        source_context = ""
        for table, cols in source_schema.items():
            source_context += f"Table {table}:\n"
            for c in cols:
                source_context += f"  - {c['name']} ({c.get('type', 'UNKNOWN')})\n"
                
        target_context = ""
        for table, cols in target_schema.items():
            target_context += f"Table {table}:\n"
            for c in cols:
                target_context += f"  - {c['name']} ({c.get('type', 'UNKNOWN')})\n"

        prompt = f"""
You are an expert database schema mapping assistant.
Your task is to map ALL source columns to the best matching target columns.
Only map a source column if a reasonably good semantic match exists in the target schema.
Do NOT map multiple source columns to the same target column (1-to-1 matching only).

SOURCE SCHEMA:
{source_context}

TARGET SCHEMA:
{target_context}

Respond ONLY with a JSON array of objects. Do not include markdown formatting or explanations outside the JSON.
Each object must have exactly these keys:
- "source_table": string
- "source_column": string
- "target_table": string
- "target_column": string
- "semantic_score": float (0.0 to 1.0)
- "reasoning": string
"""
        mappings = []
        try:
            response = llm_provider.generate(prompt=prompt, stream=False)
            text_resp = response.get("response", "[]")
            if "```json" in text_resp:
                text_resp = text_resp.split("```json")[1].split("```")[0]
            elif "```" in text_resp:
                text_resp = text_resp.split("```")[1].split("```")[0]
            
            llm_result = json.loads(text_resp.strip())
            if not isinstance(llm_result, list):
                logger.error("LLM did not return a list.")
                llm_result = []
                
            for match in llm_result:
                src_tbl = match.get("source_table")
                src_col = match.get("source_column")
                tgt_tbl = match.get("target_table")
                tgt_col = match.get("target_column")
                if not all([src_tbl, src_col, tgt_tbl, tgt_col]):
                    continue
                    
                # Find types
                src_type = "UNKNOWN"
                for c in source_schema.get(src_tbl, []):
                    if c["name"] == src_col: src_type = c.get("type", "UNKNOWN")
                
                tgt_type = "UNKNOWN"
                for c in target_schema.get(tgt_tbl, []):
                    if c["name"] == tgt_col: tgt_type = c.get("type", "UNKNOWN")
                    
                type_score = self._type_compatibility_score(src_type, tgt_type)
                llm_score = float(match.get("semantic_score", 0.0))
                final_confidence = (0.7 * llm_score) + (0.3 * type_score)
                
                mappings.append({
                    "source_table": src_tbl,
                    "source_column": src_col,
                    "target_table": tgt_tbl,
                    "target_column": tgt_col,
                    "confidence_score": round(final_confidence, 2),
                    "data_type_compatible": type_score >= 0.5,
                    "reasoning": match.get("reasoning", "")
                })
        except Exception as e:
            logger.error(f"Batch LLM mapping failed: {e}")
            
        return mappings
