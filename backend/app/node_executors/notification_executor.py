"""
Notification Node Executor
Sends notifications via email, Slack, Teams, SMS, webhooks, or in-app
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import logging
from typing import Any, Dict
import httpx

logger = logging.getLogger(__name__)


class NotificationExecutor(BaseNodeExecutor):
    """Executor for Notification node - sends notifications across channels"""

    node_type = "notification"

    # Notifications can send to external services (Slack, email, etc.)
    makes_external_calls = True

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            channel = node_data.get("channel", "email")
            priority = node_data.get("priority", "normal")

            # Resolve template values
            recipients = self.substitute_variables(
                node_data.get("recipients", ""),
                context.variables,
                context.inputs
            )
            subject = self.substitute_variables(
                node_data.get("subject", "Workflow Notification"),
                context.variables,
                context.inputs
            )
            message = self.substitute_variables(
                node_data.get("messageTemplate", ""),
                context.variables,
                context.inputs
            )

            # Send notification based on channel
            result = None
            if channel == "email":
                result = await self._send_email(recipients, subject, message, priority, db)
            elif channel == "slack":
                slack_channel = node_data.get("slackChannel", "")
                result = await self._send_slack(slack_channel, message, priority, db)
            elif channel == "teams":
                teams_channel = node_data.get("teamsChannel", "")
                result = await self._send_teams(teams_channel, message, priority, db)
            elif channel == "sms":
                result = await self._send_sms(recipients, message, db)
            elif channel == "webhook":
                webhook_url = node_data.get("webhookUrl", "")
                result = await self._send_webhook(webhook_url, message, context)
            elif channel == "in_app":
                result = await self._send_in_app(recipients, subject, message, priority, context, db)
            else:
                result = {"success": False, "error": f"Unknown channel: {channel}"}

            elapsed_ms = int((time.time() - start_time) * 1000)

            if not result.get("success", False):
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=result.get("error", "Notification failed"),
                    elapsed_ms=elapsed_ms
                )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": "Notification sent",
                    "channel": channel,
                    "recipients": recipients,
                    "message_id": result.get("message_id"),
                    "delivered_at": result.get("delivered_at"),
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

    async def _send_email(
        self,
        recipients: str,
        subject: str,
        message: str,
        priority: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Send email notification"""
        # In production, this would use an email service (SendGrid, SES, etc.)
        logger.info(f"[EMAIL] To: {recipients}, Subject: {subject}")

        # Stub implementation - would integrate with email service
        return {
            "success": True,
            "message_id": f"email_{int(time.time())}",
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "recipients_count": len(recipients.split(","))
        }

    async def _send_slack(
        self,
        channel: str,
        message: str,
        priority: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Send Slack notification"""
        logger.info(f"[SLACK] Channel: {channel}")

        # Priority emoji mapping
        priority_emoji = {
            "low": ":information_source:",
            "normal": ":bell:",
            "high": ":warning:",
            "urgent": ":rotating_light:"
        }

        # In production, this would use Slack API
        # Would need to retrieve bot token from settings
        return {
            "success": True,
            "message_id": f"slack_{int(time.time())}",
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "channel": channel
        }

    async def _send_teams(
        self,
        channel: str,
        message: str,
        priority: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Send Microsoft Teams notification"""
        logger.info(f"[TEAMS] Channel: {channel}")

        # In production, this would use MS Teams webhook or Graph API
        return {
            "success": True,
            "message_id": f"teams_{int(time.time())}",
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "channel": channel
        }

    async def _send_sms(
        self,
        recipients: str,
        message: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Send SMS notification"""
        logger.info(f"[SMS] To: {recipients}")

        # In production, this would use Twilio, Vonage, etc.
        return {
            "success": True,
            "message_id": f"sms_{int(time.time())}",
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "recipients_count": len(recipients.split(","))
        }

    async def _send_webhook(
        self,
        webhook_url: str,
        message: str,
        context: NodeExecutionContext
    ) -> Dict[str, Any]:
        """Send webhook notification"""
        if not webhook_url:
            return {"success": False, "error": "Webhook URL not configured"}

        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "message": message,
                    "workflow_id": context.workflow_spec.get("id") if context.workflow_spec else None,
                    "node_id": context.node_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "variables": context.variables
                }

                response = await client.post(
                    webhook_url,
                    json=payload,
                    timeout=30.0
                )

                if response.status_code >= 400:
                    return {
                        "success": False,
                        "error": f"Webhook returned {response.status_code}: {response.text[:200]}"
                    }

                return {
                    "success": True,
                    "message_id": f"webhook_{int(time.time())}",
                    "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status_code": response.status_code
                }

        except httpx.TimeoutException:
            return {"success": False, "error": "Webhook request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _send_in_app(
        self,
        recipients: str,
        subject: str,
        message: str,
        priority: str,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Send in-app notification"""
        logger.info(f"[IN_APP] To: {recipients}, Subject: {subject}")

        # In production, this would create notification records in the database
        # and potentially use WebSocket or Server-Sent Events for real-time delivery
        return {
            "success": True,
            "message_id": f"inapp_{int(time.time())}",
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "recipients_count": len(recipients.split(",")) if recipients else 0
        }
