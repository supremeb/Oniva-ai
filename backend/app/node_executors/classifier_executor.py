"""
Question Classifier Node Executor
Uses LLM to classify input into categories
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
import asyncio
from uuid import UUID

import litellm
from pydantic import BaseModel

from app.services.llm_gateway import (
    resolve_key_with_fallback, normalize_model_id, get_provider_from_model,
)


class ClassificationResult(BaseModel):
    """Output from classifier"""
    class_id: str
    class_name: str
    confidence: float


class ClassifierExecutor(BaseNodeExecutor):
    """Executor for Question Classifier node - LLM-based classification"""

    node_type = "classifier"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get configuration
            model_id = normalize_model_id(node_data.get("model", "openai/gpt-4o-mini"))

            classes = node_data.get("classes", [])
            instructions = node_data.get("instructions", "")
            input_text_path = node_data.get("inputText", "")

            # Get input text
            input_text = self.substitute_variables(
                input_text_path, context.variables, context.inputs
            )

            if not input_text:
                input_text = context.variables.get("sys.query", "")

            if not classes:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="No classes defined for classification",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Normalize classes — handle both dict and string formats
            normalized_classes = []
            for i, c in enumerate(classes):
                if isinstance(c, dict):
                    normalized_classes.append(c)
                elif isinstance(c, str):
                    normalized_classes.append({"id": c, "name": c})
                else:
                    normalized_classes.append({"id": f"class_{i}", "name": str(c)})
            classes = normalized_classes

            # Build classification prompt
            class_list = "\n".join([
                f"- {c.get('id', f'class_{i}')}: {c.get('name', 'Unnamed')}"
                for i, c in enumerate(classes)
            ])

            system_prompt = f"""You are a text classifier. Classify the input text into one of these categories:

{class_list}

{instructions if instructions else ''}

Respond with ONLY a JSON object in this format:
{{"class_id": "<id>", "class_name": "<name>", "confidence": <0.0-1.0>}}

Be precise and choose the most appropriate category."""

            # Resolve API key via unified gateway (with OpenRouter fallback)
            tenant_uuid = UUID(context.tenant_id) if context.tenant_id else None
            api_key, model_id = await resolve_key_with_fallback(db, tenant_uuid, model_id)

            if not api_key:
                provider_type = get_provider_from_model(model_id)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"API key not configured for provider '{provider_type}'. Add your key in Settings > LLM Providers.",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Run classification via LiteLLM
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Classify this text:\n\n{input_text}"},
                    ],
                    temperature=0.1,
                    max_tokens=200,
                    api_key=api_key,
                ),
                timeout=30.0,
            )

            elapsed_ms = int((time.time() - start_time) * 1000)
            result_text = response.choices[0].message.content or ""

            # Parse result
            try:
                json_match = result_text
                if "```json" in result_text:
                    json_match = result_text.split("```json")[1].split("```")[0]
                elif "```" in result_text:
                    json_match = result_text.split("```")[1].split("```")[0]

                classification = json.loads(json_match.strip())

                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={
                        "class_id": classification.get("class_id", ""),
                        "class_name": classification.get("class_name", ""),
                        "confidence": classification.get("confidence", 0.0),
                        "input_text": input_text,
                    },
                    elapsed_ms=elapsed_ms,
                    next_node_id=classification.get("class_id")
                )
            except json.JSONDecodeError:
                # Fallback: try to match class name in response
                result_lower = result_text.lower()
                for c in classes:
                    if c.get("name", "").lower() in result_lower:
                        return NodeExecutionResult(
                            node_id=context.node_id,
                            status="success",
                            output={
                                "class_id": c.get("id", ""),
                                "class_name": c.get("name", ""),
                                "confidence": 0.7,
                                "input_text": input_text,
                                "raw_response": result_text,
                            },
                            elapsed_ms=elapsed_ms,
                            next_node_id=c.get("id")
                        )

                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={
                        "class_id": classes[0].get("id", "") if classes else "",
                        "class_name": classes[0].get("name", "") if classes else "",
                        "confidence": 0.5,
                        "input_text": input_text,
                        "raw_response": result_text,
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
