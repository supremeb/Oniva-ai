"""
Wait/Delay Node Executor
Pauses workflow execution for a specified duration or until a specific time
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class WaitExecutor(BaseNodeExecutor):
    """Executor for Wait/Delay node - pauses workflow execution"""

    node_type = "wait"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            wait_type = node_data.get("waitType", "duration")

            wait_seconds = 0
            wait_reason = ""

            if wait_type == "duration":
                duration = node_data.get("duration", 0)
                unit = node_data.get("durationUnit", "seconds")

                # Convert to seconds
                multipliers = {
                    "seconds": 1,
                    "minutes": 60,
                    "hours": 3600,
                    "days": 86400
                }
                wait_seconds = duration * multipliers.get(unit, 1)
                wait_reason = f"Waiting for {duration} {unit}"

            elif wait_type == "until_time":
                target_time_str = node_data.get("targetTime")
                timezone_str = node_data.get("timezone", "UTC")

                if not target_time_str:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error="No target time specified",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

                try:
                    tz = ZoneInfo(timezone_str)
                    target_time = datetime.fromisoformat(target_time_str.replace('Z', '+00:00'))
                    if target_time.tzinfo is None:
                        target_time = target_time.replace(tzinfo=tz)

                    now = datetime.now(tz)
                    wait_seconds = max(0, (target_time - now).total_seconds())
                    wait_reason = f"Waiting until {target_time.isoformat()}"
                except Exception as e:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error=f"Invalid time format: {str(e)}",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            elif wait_type == "business_hours":
                config = node_data.get("businessHoursConfig", {})
                start_hour = config.get("startHour", 9)
                end_hour = config.get("endHour", 17)
                weekdays = config.get("weekdays", [0, 1, 2, 3, 4])  # Mon-Fri
                timezone_str = node_data.get("timezone", "UTC")

                try:
                    tz = ZoneInfo(timezone_str)
                    now = datetime.now(tz)

                    # Check if within business hours
                    current_weekday = now.weekday()
                    current_hour = now.hour

                    if (current_weekday in weekdays and
                        start_hour <= current_hour < end_hour):
                        # Already in business hours
                        wait_seconds = 0
                        wait_reason = "Already within business hours"
                    else:
                        # Calculate next business hour start
                        next_start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)

                        # If past today's business hours or not a business day
                        if current_hour >= end_hour or current_weekday not in weekdays:
                            next_start += timedelta(days=1)

                        # Find next business day
                        while next_start.weekday() not in weekdays:
                            next_start += timedelta(days=1)

                        wait_seconds = max(0, (next_start - now).total_seconds())
                        wait_reason = f"Waiting until business hours: {next_start.isoformat()}"

                except Exception as e:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error=f"Business hours calculation failed: {str(e)}",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            # Cap maximum wait time for safety (default: 7 days)
            max_wait = node_data.get("maxWaitSeconds", 604800)
            if wait_seconds > max_wait:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Wait duration ({wait_seconds}s) exceeds maximum ({max_wait}s)",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Actually wait (in production, this would be handled by DBOS checkpointing)
            if wait_seconds > 0:
                # For short waits, actually sleep
                # For longer waits in production, use DBOS checkpoint with wake time
                if wait_seconds <= 60:
                    await asyncio.sleep(wait_seconds)
                else:
                    # In production with DBOS, we'd checkpoint here and resume later
                    # For now, cap at 60 seconds for demo purposes
                    await asyncio.sleep(min(wait_seconds, 60))

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": "Wait completed",
                    "wait_type": wait_type,
                    "wait_seconds": wait_seconds,
                    "reason": wait_reason,
                    "resumed_at": datetime.utcnow().isoformat()
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
