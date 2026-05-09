"""
Error Node Executor
Vellum-inspired explicit error handling for workflows

The Error node provides explicit error handling capabilities:
- Catch errors from upstream nodes
- Transform errors into structured responses
- Route execution based on error type
- Provide fallback values or retry logic

Usage:
{
    "type": "error",
    "data": {
        "errorType": "explicit",  // explicit, catch, transform
        "errorMessage": "Custom error message",
        "errorCode": "ERR001",
        "includeStackTrace": false,
        "transformErrors": true,
        "fallbackValue": null,
        "errorMapping": {
            "timeout": "Operation timed out, please try again",
            "auth": "Authentication failed, check credentials",
            "*": "An unexpected error occurred"
        }
    }
}

Error node types:
- explicit: Explicitly throw an error (workflow termination)
- catch: Catch errors from upstream and continue
- transform: Transform error messages into user-friendly format
"""

import logging
import time
import traceback
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult

logger = logging.getLogger(__name__)


class ErrorDetails(BaseModel):
    """Structured error information"""
    error_code: Optional[str] = None
    error_message: str
    error_type: str
    original_error: Optional[str] = None
    source_node: Optional[str] = None
    stack_trace: Optional[str] = None
    timestamp: str = ""
    recoverable: bool = True


class ErrorOutput(BaseModel):
    """Output schema for Error node"""
    has_error: bool
    error_details: Optional[ErrorDetails] = None
    fallback_value: Optional[Any] = None
    transformed_message: Optional[str] = None
    caught_errors: List[ErrorDetails] = Field(default_factory=list)
    action_taken: str  # thrown, caught, transformed, passed


class ErrorExecutor(BaseNodeExecutor):
    """
    Executor for Error node - explicit error handling

    This node handles errors in three modes:
    1. explicit: Throw an error to terminate workflow
    2. catch: Catch upstream errors and continue execution
    3. transform: Transform error messages for end-user consumption
    """

    node_type = "error"

    # Common error type mappings
    ERROR_TYPE_PATTERNS = {
        "timeout": ["timeout", "timed out", "deadline exceeded"],
        "auth": ["authentication", "unauthorized", "forbidden", "401", "403"],
        "network": ["connection", "network", "dns", "ssl"],
        "validation": ["validation", "invalid", "required field"],
        "rate_limit": ["rate limit", "too many requests", "429"],
        "not_found": ["not found", "404", "does not exist"],
        "server": ["500", "502", "503", "internal server error"],
    }

    def _classify_error(self, error_message: str) -> str:
        """Classify error into a type based on patterns"""
        error_lower = error_message.lower()

        for error_type, patterns in self.ERROR_TYPE_PATTERNS.items():
            for pattern in patterns:
                if pattern in error_lower:
                    return error_type

        return "unknown"

    def _transform_error_message(
        self,
        error_message: str,
        error_mapping: Dict[str, str]
    ) -> str:
        """Transform error message using mapping"""
        error_type = self._classify_error(error_message)

        # Try specific mapping first
        if error_type in error_mapping:
            return error_mapping[error_type]

        # Try wildcard mapping
        if "*" in error_mapping:
            return error_mapping["*"]

        # Return original if no mapping
        return error_message

    def _extract_errors_from_inputs(
        self,
        inputs: Dict[str, Any]
    ) -> List[ErrorDetails]:
        """Extract error information from input nodes"""
        errors = []
        from datetime import datetime

        for source_node_id, node_output in inputs.items():
            if isinstance(node_output, dict):
                # Check for error indicators
                if node_output.get("status") == "failed":
                    error_msg = node_output.get("error", "Unknown error")
                    errors.append(ErrorDetails(
                        error_message=error_msg,
                        error_type=self._classify_error(error_msg),
                        original_error=error_msg,
                        source_node=source_node_id,
                        timestamp=datetime.utcnow().isoformat(),
                        recoverable=True
                    ))
                elif "error" in node_output and node_output["error"]:
                    error_msg = node_output["error"]
                    errors.append(ErrorDetails(
                        error_message=error_msg,
                        error_type=self._classify_error(error_msg),
                        original_error=error_msg,
                        source_node=source_node_id,
                        timestamp=datetime.utcnow().isoformat(),
                        recoverable=True
                    ))

        return errors

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """Execute error handling"""
        start_time = time.time()
        from datetime import datetime

        try:
            node_data = context.node_data

            # Get configuration
            error_type = node_data.get("errorType", "explicit")
            error_message = node_data.get("errorMessage", "An error occurred")
            error_code = node_data.get("errorCode")
            include_stack_trace = node_data.get("includeStackTrace", False)
            fallback_value = node_data.get("fallbackValue")
            error_mapping = node_data.get("errorMapping", {})

            # Variable substitution in error message
            error_message = self.substitute_variables(
                error_message,
                context.variables,
                context.inputs
            )

            if error_type == "explicit":
                # Explicitly throw an error
                error_details = ErrorDetails(
                    error_code=error_code,
                    error_message=error_message,
                    error_type="explicit",
                    timestamp=datetime.utcnow().isoformat(),
                    recoverable=False
                )

                if include_stack_trace:
                    error_details.stack_trace = "".join(
                        traceback.format_stack()[:-1]
                    )

                output = ErrorOutput(
                    has_error=True,
                    error_details=error_details,
                    action_taken="thrown"
                )

                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    output=output.model_dump(),
                    error=f"[{error_code}] {error_message}" if error_code else error_message,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            elif error_type == "catch":
                # Catch errors from upstream nodes
                caught_errors = self._extract_errors_from_inputs(context.inputs)

                if caught_errors:
                    # Transform error messages if mapping provided
                    for err in caught_errors:
                        if error_mapping:
                            err.error_message = self._transform_error_message(
                                err.original_error or err.error_message,
                                error_mapping
                            )

                    output = ErrorOutput(
                        has_error=True,
                        caught_errors=caught_errors,
                        fallback_value=fallback_value,
                        action_taken="caught"
                    )

                    # Return success with fallback - error is caught, not thrown
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="success",
                        output={
                            **output.model_dump(),
                            "output": fallback_value,
                            "caught_error_count": len(caught_errors)
                        },
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )
                else:
                    # No errors to catch - pass through inputs
                    output = ErrorOutput(
                        has_error=False,
                        action_taken="passed"
                    )

                    # Pass through the first input as output
                    pass_through = None
                    for node_output in context.inputs.values():
                        pass_through = node_output
                        break

                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="success",
                        output={
                            **output.model_dump(),
                            "output": pass_through
                        },
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            elif error_type == "transform":
                # Transform errors into user-friendly messages
                caught_errors = self._extract_errors_from_inputs(context.inputs)

                if caught_errors:
                    # Transform all error messages
                    transformed_messages = []
                    for err in caught_errors:
                        transformed = self._transform_error_message(
                            err.original_error or err.error_message,
                            error_mapping
                        )
                        err.error_message = transformed
                        transformed_messages.append(transformed)

                    # Combine transformed messages
                    combined_message = "; ".join(set(transformed_messages))

                    output = ErrorOutput(
                        has_error=True,
                        caught_errors=caught_errors,
                        transformed_message=combined_message,
                        action_taken="transformed"
                    )

                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="success",
                        output={
                            **output.model_dump(),
                            "output": combined_message,
                            "original_errors": [e.original_error for e in caught_errors]
                        },
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )
                else:
                    # No errors - pass through
                    output = ErrorOutput(
                        has_error=False,
                        action_taken="passed"
                    )

                    pass_through = None
                    for node_output in context.inputs.values():
                        pass_through = node_output
                        break

                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="success",
                        output={
                            **output.model_dump(),
                            "output": pass_through
                        },
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            else:
                # Unknown error type - default to explicit
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Unknown error type: {error_type}",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

        except Exception as e:
            logger.exception(f"Error executor failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Error handling failed: {str(e)}",
                elapsed_ms=int((time.time() - start_time) * 1000)
            )
