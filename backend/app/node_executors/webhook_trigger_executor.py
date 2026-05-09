"""
Webhook Trigger Node Executor
Receives and validates incoming webhook requests to trigger workflow execution
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
import hmac
import hashlib
from typing import Any, Dict, Optional


class WebhookTriggerExecutor(BaseNodeExecutor):
    """Executor for Webhook Trigger node - receives webhook requests"""

    node_type = "webhook_trigger"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """
        This executor is typically called by the webhook API endpoint
        after validating the incoming request.
        """
        start_time = time.time()

        try:
            node_data = context.node_data
            # The webhook payload is passed via context.inputs or context.variables
            webhook_payload = context.inputs.get("webhook_payload", {})

            if isinstance(webhook_payload, str):
                try:
                    webhook_payload = json.loads(webhook_payload)
                except json.JSONDecodeError:
                    webhook_payload = {"raw": webhook_payload}

            # Extract common webhook metadata
            headers = context.inputs.get("webhook_headers", {})
            method = context.inputs.get("webhook_method", "POST")
            query_params = context.inputs.get("webhook_query", {})

            # Validate payload schema if defined
            payload_schema = node_data.get("payloadSchema")
            validation_result = None
            if payload_schema:
                validation_result = self._validate_payload(webhook_payload, payload_schema)
                if not validation_result["valid"]:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error=f"Payload validation failed: {validation_result['errors']}",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": webhook_payload,
                    "payload": webhook_payload,
                    "headers": headers,
                    "method": method,
                    "query": query_params,
                    "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "validation": validation_result,
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

    def _validate_payload(self, payload: Dict, schema: str) -> Dict[str, Any]:
        """Validate payload against JSON schema"""
        try:
            schema_dict = json.loads(schema) if isinstance(schema, str) else schema

            # Simple required field validation
            errors = []
            if "required" in schema_dict:
                for field in schema_dict["required"]:
                    if field not in payload:
                        errors.append(f"Missing required field: {field}")

            # Type validation
            if "properties" in schema_dict:
                for field, spec in schema_dict["properties"].items():
                    if field in payload:
                        value = payload[field]
                        expected_type = spec.get("type")
                        if not self._check_type(value, expected_type):
                            errors.append(f"Field '{field}' expected type {expected_type}")

            return {
                "valid": len(errors) == 0,
                "errors": errors
            }
        except Exception as e:
            return {"valid": False, "errors": [str(e)]}

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Check if value matches expected JSON schema type"""
        if expected_type is None:
            return True

        type_mapping = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        expected_py_type = type_mapping.get(expected_type)
        if expected_py_type:
            return isinstance(value, expected_py_type)
        return True

    @staticmethod
    def validate_hmac_signature(
        payload: bytes,
        signature: str,
        secret: str,
        algorithm: str = "sha256"
    ) -> bool:
        """Validate HMAC signature for webhook authentication"""
        if not signature or not secret:
            return False

        try:
            # Handle different signature formats
            if signature.startswith("sha256="):
                signature = signature[7:]
            elif signature.startswith("sha1="):
                algorithm = "sha1"
                signature = signature[5:]

            hash_algo = getattr(hashlib, algorithm)
            expected = hmac.new(
                secret.encode(),
                payload,
                hash_algo
            ).hexdigest()

            return hmac.compare_digest(expected, signature)
        except Exception:
            return False

    @staticmethod
    def generate_idempotency_key(payload: Dict, key_path: Optional[str] = None) -> str:
        """Generate idempotency key from payload"""
        if key_path:
            # Extract specific field for idempotency
            parts = key_path.split(".")
            value = payload
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    break
            if value:
                return str(value)

        # Default: hash the entire payload
        payload_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(payload_str.encode()).hexdigest()[:32]
