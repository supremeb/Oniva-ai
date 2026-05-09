"""
Entity Extraction Service for Oniva.

Part of Phase 2: Workflow Entity Tracking.
Extracts entities (companies, people, regulations, etc.) from workflow outputs
using LLM-based NER with structured output.

Features:
- LLM-powered named entity recognition
- Financial domain-specific entity types
- Confidence scoring based on extraction source
- Batch processing for efficiency
- Graceful degradation on LLM failures
"""

import json
import logging
import threading
from typing import Optional, List, Dict, Any
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.workflow_entity import WorkflowEntity, EntityType
from app.services.llm_gateway import LLMGateway, LLMRequest, AllProvidersFailedError


logger = logging.getLogger(__name__)

# Singleton instance with thread safety
_entity_extraction_service: Optional['EntityExtractionService'] = None
_entity_extraction_lock = threading.Lock()


def get_entity_extraction_service() -> 'EntityExtractionService':
    """Get or create the singleton EntityExtractionService instance."""
    global _entity_extraction_service
    if _entity_extraction_service is None:
        with _entity_extraction_lock:
            if _entity_extraction_service is None:  # Double-check locking
                _entity_extraction_service = EntityExtractionService()
    return _entity_extraction_service


def reset_entity_extraction_service() -> None:
    """Reset the singleton instance (for testing)."""
    global _entity_extraction_service
    _entity_extraction_service = None


# Entity extraction prompt template
ENTITY_EXTRACTION_PROMPT = """Extract named entities from the following text. Focus on entities relevant to financial analysis and business workflows.

Entity types to extract:
- COMPANY: Company names, organizations, subsidiaries
- PERSON: Names of executives, analysts, individuals
- REGULATION: Regulatory frameworks, laws, compliance standards (e.g., SOX, GDPR, SEC Rule)
- FINANCIAL_INSTRUMENT: Stocks, bonds, derivatives, tickers (e.g., AAPL, BTC)
- RISK_FACTOR: Risk categories, market risks, operational risks
- DATE: Specific dates, time periods, deadlines
- AMOUNT: Financial amounts, percentages, metrics
- LOCATION: Countries, cities, jurisdictions

TEXT TO ANALYZE:
{text}

Return a JSON array of extracted entities. Each entity should have:
- "name": The entity text as it appears
- "type": One of the entity types above (use exact type names)
- "confidence": A score from 0.5 to 1.0 based on extraction certainty
- "properties": Optional object with additional context (ticker, jurisdiction, etc.)

Example output:
[
  {{"name": "Apple Inc.", "type": "COMPANY", "confidence": 0.95, "properties": {{"ticker": "AAPL"}}}},
  {{"name": "Tim Cook", "type": "PERSON", "confidence": 0.9, "properties": {{"role": "CEO"}}}},
  {{"name": "SEC Rule 10b-5", "type": "REGULATION", "confidence": 0.85, "properties": {{"jurisdiction": "US"}}}}
]

Return ONLY the JSON array, no other text."""


class EntityExtractionService:
    """
    Extract and store entities from workflow outputs.

    Uses LLM-based NER to identify companies, people, regulations,
    and other entities from workflow execution results.
    """

    # Minimum text length for extraction (skip very short outputs)
    MIN_TEXT_LENGTH = 50

    # Maximum text length to process (truncate longer texts)
    MAX_TEXT_LENGTH = 8000

    # Default confidence for manual/fallback extraction
    DEFAULT_CONFIDENCE = 0.7

    def __init__(self):
        """Initialize EntityExtractionService."""
        self._settings = get_settings()

    async def extract_entities(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        text: str,
        source_model: str = "gpt-4o-mini",
    ) -> List[Dict[str, Any]]:
        """
        Extract entities from text using LLM.

        Args:
            db: Database session
            tenant_id: Tenant ID for LLM provider selection
            text: Text to extract entities from
            source_model: LLM model to use for extraction

        Returns:
            List of extracted entities with name, type, confidence, properties
        """
        if not text or len(text.strip()) < self.MIN_TEXT_LENGTH:
            logger.debug(f"Text too short for extraction: {len(text)} chars")
            return []

        # Truncate very long texts
        if len(text) > self.MAX_TEXT_LENGTH:
            text = text[:self.MAX_TEXT_LENGTH] + "..."
            logger.debug(f"Truncated text to {self.MAX_TEXT_LENGTH} chars")

        try:
            llm_gateway = LLMGateway(db)
            request = LLMRequest(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a financial entity extraction specialist. Extract named entities accurately and return valid JSON."
                    },
                    {
                        "role": "user",
                        "content": ENTITY_EXTRACTION_PROMPT.format(text=text)
                    }
                ],
                model=source_model,
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=2000,
            )

            response = await llm_gateway.complete(request, tenant_id)

            # Parse LLM response
            entities = self._parse_extraction_response(response.content)
            logger.info(f"Extracted {len(entities)} entities from text")
            return entities

        except AllProvidersFailedError as e:
            logger.warning(f"Entity extraction failed - all LLM providers unavailable: {e}")
            return []
        except Exception as e:
            logger.error(f"Entity extraction failed: {type(e).__name__}: {e}")
            return []

    def _parse_extraction_response(self, response: str) -> List[Dict[str, Any]]:
        """Parse LLM response into entity list."""
        try:
            # Try to parse JSON directly
            entities = json.loads(response.strip())
            if not isinstance(entities, list):
                logger.warning("LLM response is not a list")
                return []

            # Validate and normalize entities
            validated = []
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                if "name" not in entity or "type" not in entity:
                    continue

                # Normalize entity type
                entity_type = entity["type"].upper().replace(" ", "_")
                valid_types = {
                    EntityType.COMPANY, EntityType.PERSON, EntityType.REGULATION,
                    EntityType.FINANCIAL_INSTRUMENT, EntityType.RISK_FACTOR,
                    EntityType.DATE, EntityType.AMOUNT, EntityType.LOCATION
                }
                if entity_type not in valid_types:
                    entity_type = "other"  # Fallback for unknown types

                validated.append({
                    "name": str(entity["name"])[:255],  # Truncate to DB limit
                    "type": entity_type.lower(),
                    "confidence": min(max(float(entity.get("confidence", 0.7)), 0.5), 1.0),
                    "properties": entity.get("properties", {})
                })

            return validated

        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```json" in response:
                try:
                    json_str = response.split("```json")[1].split("```")[0]
                    return self._parse_extraction_response(json_str)
                except (IndexError, json.JSONDecodeError):
                    pass
            elif "```" in response:
                try:
                    json_str = response.split("```")[1].split("```")[0]
                    return self._parse_extraction_response(json_str)
                except (IndexError, json.JSONDecodeError):
                    pass

            logger.warning(f"Failed to parse entity extraction response as JSON")
            return []

    async def store_entities(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        workflow_id: UUID,
        execution_id: Optional[UUID],
        entities: List[Dict[str, Any]],
        source_node_id: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> List[UUID]:
        """
        Store extracted entities to database.

        Args:
            db: Database session
            tenant_id: Tenant ID
            workflow_id: Workflow that produced these entities
            execution_id: Specific execution (optional)
            entities: List of entity dicts from extract_entities()
            source_node_id: Workflow node that produced the output
            created_by: User who triggered the extraction

        Returns:
            List of created entity UUIDs
        """
        if not entities:
            return []

        entity_ids = []
        for entity in entities:
            workflow_entity = WorkflowEntity(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                name=entity["name"],
                entity_type=entity["type"],
                properties=entity.get("properties", {}),
                confidence=entity.get("confidence", self.DEFAULT_CONFIDENCE),
                source_node_id=source_node_id,
                created_by=created_by,
            )
            db.add(workflow_entity)
            entity_ids.append(workflow_entity.id)

        await db.commit()
        logger.info(f"Stored {len(entity_ids)} entities for workflow {workflow_id}")
        return entity_ids

    async def extract_and_store(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        workflow_id: UUID,
        execution_id: Optional[UUID],
        text: str,
        source_node_id: Optional[str] = None,
        created_by: Optional[UUID] = None,
        source_model: str = "gpt-4o-mini",
    ) -> List[UUID]:
        """
        Convenience method to extract entities from text and store them.

        Combines extract_entities() and store_entities() in one call.
        """
        entities = await self.extract_entities(db, tenant_id, text, source_model)
        if not entities:
            return []

        return await self.store_entities(
            db=db,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            entities=entities,
            source_node_id=source_node_id,
            created_by=created_by,
        )

    async def get_workflow_entities(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        workflow_id: UUID,
        execution_id: Optional[UUID] = None,
        entity_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[WorkflowEntity]:
        """
        Retrieve entities for a workflow.

        Args:
            db: Database session
            tenant_id: Tenant ID
            workflow_id: Workflow ID
            execution_id: Filter by specific execution (optional)
            entity_type: Filter by entity type (optional)
            limit: Maximum entities to return

        Returns:
            List of WorkflowEntity objects
        """
        query = select(WorkflowEntity).where(
            WorkflowEntity.tenant_id == tenant_id,
            WorkflowEntity.workflow_id == workflow_id,
        ).order_by(WorkflowEntity.created_at.desc()).limit(limit)

        if execution_id:
            query = query.where(WorkflowEntity.execution_id == execution_id)
        if entity_type:
            query = query.where(WorkflowEntity.entity_type == entity_type.lower())

        result = await db.execute(query)
        return list(result.scalars().all())

    async def search_by_entity(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        entity_name: str,
        entity_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Search for workflows that mention a specific entity.

        Enables queries like "What workflows analyzed Acme Corp?"

        Args:
            db: Database session
            tenant_id: Tenant ID
            entity_name: Entity name to search for (case-insensitive)
            entity_type: Optional type filter
            limit: Maximum results

        Returns:
            List of dicts with workflow_id, entity details, and execution info
        """
        from sqlalchemy import func

        query = (
            select(WorkflowEntity)
            .where(
                WorkflowEntity.tenant_id == tenant_id,
                func.lower(WorkflowEntity.name).contains(entity_name.lower()),
            )
            .order_by(WorkflowEntity.created_at.desc())
            .limit(limit)
        )

        if entity_type:
            query = query.where(WorkflowEntity.entity_type == entity_type.lower())

        result = await db.execute(query)
        entities = result.scalars().all()

        return [entity.to_dict() for entity in entities]

    async def get_entity_summary(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        workflow_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Get summary statistics for entities.

        Args:
            db: Database session
            tenant_id: Tenant ID
            workflow_id: Optional workflow filter

        Returns:
            Dict with total count and breakdown by type
        """
        from sqlalchemy import func

        base_query = select(
            WorkflowEntity.entity_type,
            func.count(WorkflowEntity.id).label("count")
        ).where(
            WorkflowEntity.tenant_id == tenant_id
        ).group_by(WorkflowEntity.entity_type)

        if workflow_id:
            base_query = base_query.where(WorkflowEntity.workflow_id == workflow_id)

        result = await db.execute(base_query)
        type_counts = {row.entity_type: row.count for row in result.all()}

        total = sum(type_counts.values())

        return {
            "total_entities": total,
            "by_type": type_counts,
            "workflow_id": str(workflow_id) if workflow_id else None,
        }

    async def delete_workflow_entities(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        workflow_id: UUID,
        execution_id: Optional[UUID] = None,
    ) -> int:
        """
        Delete entities for a workflow or execution.

        Args:
            db: Database session
            tenant_id: Tenant ID
            workflow_id: Workflow ID
            execution_id: Optional execution filter

        Returns:
            Number of deleted entities
        """
        query = delete(WorkflowEntity).where(
            WorkflowEntity.tenant_id == tenant_id,
            WorkflowEntity.workflow_id == workflow_id,
        )

        if execution_id:
            query = query.where(WorkflowEntity.execution_id == execution_id)

        result = await db.execute(query)
        await db.commit()

        deleted = result.rowcount
        logger.info(f"Deleted {deleted} entities for workflow {workflow_id}")
        return deleted
