"""
Knowledge Retrieval Node Executor
Retrieves documents from vector database (pgvector)
"""

from uuid import UUID

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from app.services.rag_service import RAGService
from sqlalchemy.ext.asyncio import AsyncSession
import time


class KnowledgeExecutor(BaseNodeExecutor):
    """Executor for Knowledge Retrieval node - vector DB search"""
    
    node_type = "knowledge"
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        
        try:
            node_data = context.node_data
            
            # Get configuration
            query_path = node_data.get("query", "")
            knowledge_bases = node_data.get("knowledgeBases", [])
            top_k = node_data.get("topK", 5)
            score_threshold = node_data.get("scoreThreshold", 0.5)
            search_mode = node_data.get("searchMode", "hybrid") # Default to hybrid
            
            # Get query with variable substitution
            query = self.substitute_variables(query_path, context.variables, context.inputs)
            
            if not query:
                query = context.variables.get("sys.query", "")
            
            if not query:
                elapsed_ms = int((time.time() - start_time) * 1000)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={"result": [], "count": 0, "query": query},
                    elapsed_ms=elapsed_ms
                )
            
            # Use RAGService for actual search
            rag_service = RAGService(db)
            
            # Tenant isolation: require tenant_id for all searches
            tenant_id = UUID(context.tenant_id) if context.tenant_id else None
            if not tenant_id:
                raise ValueError("tenant_id is required for knowledge search")

            if search_mode == "hybrid":
                # Convert string KB IDs to UUIDs for hybrid_search_v2
                kb_uuids = [UUID(kb) for kb in knowledge_bases] if knowledge_bases else None
                results = await rag_service.hybrid_search_v2(
                    query=query,
                    tenant_id=tenant_id,
                    limit=top_k,
                    knowledge_base_ids=kb_uuids,
                )
            else:
                results = await rag_service.vector_search(
                    query=query,
                    tenant_id=tenant_id,
                    limit=top_k,
                )
            
            # Filter by score threshold
            filtered_results = [r for r in results if r["score"] >= score_threshold]
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "result": filtered_results,
                    "count": len(filtered_results),
                    "query": query,
                    "knowledge_bases": knowledge_bases,
                    "settings": {
                        "top_k": top_k,
                        "threshold": score_threshold,
                        "search_mode": search_mode
                    }
                },
                elapsed_ms=elapsed_ms
            )
            
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )
