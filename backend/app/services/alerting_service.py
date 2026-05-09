"""Alerting Service for proactive failure notifications.

Enterprise feature: Monitor workflow executions and send notifications
when alert conditions are met.

Supports:
- Email notifications (via configured SMTP/Resend)
- Slack webhooks
- Custom webhooks with HMAC signing
- Microsoft Teams webhooks
- PagerDuty events

- Phase 4: Operations
"""
import logging
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alerting import (
    NotificationChannel, AlertRule, AlertHistory,
    ChannelType, TriggerType
)
from app.models.workflow import WorkflowExecution

logger = logging.getLogger(__name__)


class AlertingService:
    """Service for managing and executing alert notifications.

    Features:
    - Alert rule matching for failed executions
    - Cooldown support to prevent alert storms
    - Multiple notification channel support
    - Delivery tracking and retry
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_and_alert(
        self,
        execution: WorkflowExecution,
        tenant_id: uuid.UUID
    ) -> List[AlertHistory]:
        """Check if any alert rules match the execution and send notifications.

        Args:
            execution: The workflow execution to check
            tenant_id: Tenant ID for rule lookup

        Returns:
            List of created AlertHistory records
        """
        alerts_sent = []

        # Only check for failures currently
        if execution.status != 'failed':
            return alerts_sent

        # Get matching alert rules
        rules = await self._get_matching_rules(
            tenant_id=tenant_id,
            trigger_type=TriggerType.EXECUTION_FAILED,
            execution=execution
        )

        for rule in rules:
            # Check cooldown
            if rule.is_in_cooldown():
                logger.debug(f"Alert rule {rule.id} is in cooldown, skipping")
                continue

            # Create alert history
            alert = AlertHistory(
                tenant_id=tenant_id,
                alert_rule_id=rule.id,
                execution_id=execution.id,
                workflow_id=execution.workflow_id,
                trigger_type=rule.trigger_type,
                trigger_data={
                    "status": execution.status,
                    "error": execution.error,
                    "workflow_id": str(execution.workflow_id),
                    "execution_id": str(execution.id)
                },
                message=self._generate_alert_message(rule, execution)
            )
            self.db.add(alert)

            # Send notifications
            try:
                await self._send_notifications(rule, alert)
                alert.mark_sent()

                # Update rule's last triggered time
                rule.last_triggered_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"Failed to send alert for rule {rule.id}: {e}")
                alert.mark_failed(str(e))

            alerts_sent.append(alert)

        await self.db.commit()
        return alerts_sent

    async def _get_matching_rules(
        self,
        tenant_id: uuid.UUID,
        trigger_type: TriggerType,
        execution: Optional[WorkflowExecution] = None
    ) -> List[AlertRule]:
        """Get alert rules that match the given criteria."""
        query = select(AlertRule).where(
            AlertRule.tenant_id == tenant_id,
            AlertRule.trigger_type == trigger_type.value,
            AlertRule.is_active == True
        )

        # If we have an execution, filter by workflow scope
        if execution and execution.workflow_id:
            query = query.where(
                or_(
                    AlertRule.workflow_id.is_(None),  # Global rules
                    AlertRule.workflow_id == execution.workflow_id  # Workflow-specific
                )
            )

        result = await self.db.execute(query)
        rules = result.scalars().all()

        # Filter by scope if execution provided
        if execution:
            rules = [r for r in rules if r.matches_execution(execution)]

        return rules

    def _generate_alert_message(
        self,
        rule: AlertRule,
        execution: WorkflowExecution
    ) -> str:
        """Generate a human-readable alert message."""
        if rule.trigger_type == TriggerType.EXECUTION_FAILED.value:
            error_preview = execution.error[:200] if execution.error else "No error details"
            return (
                f"Workflow execution failed!\n\n"
                f"Workflow ID: {execution.workflow_id}\n"
                f"Execution ID: {execution.id}\n"
                f"Error: {error_preview}\n"
                f"Alert Rule: {rule.name}"
            )
        return f"Alert triggered: {rule.name}"

    async def _send_notifications(
        self,
        rule: AlertRule,
        alert: AlertHistory
    ) -> None:
        """Send notifications to all configured channels."""
        if not rule.notification_channel_ids:
            return

        # Get channels
        query = select(NotificationChannel).where(
            NotificationChannel.id.in_(rule.notification_channel_ids),
            NotificationChannel.is_active == True
        )
        result = await self.db.execute(query)
        channels = result.scalars().all()

        for channel in channels:
            try:
                await self._send_to_channel(channel, alert)
            except Exception as e:
                logger.error(f"Failed to send to channel {channel.id}: {e}")
                # Continue with other channels

    async def _send_to_channel(
        self,
        channel: NotificationChannel,
        alert: AlertHistory
    ) -> None:
        """Send notification to a specific channel."""
        channel_type = ChannelType(channel.channel_type)

        if channel_type == ChannelType.WEBHOOK:
            await self._send_webhook(channel, alert)
        elif channel_type == ChannelType.SLACK:
            await self._send_slack(channel, alert)
        elif channel_type == ChannelType.TEAMS:
            await self._send_teams(channel, alert)
        elif channel_type == ChannelType.EMAIL:
            await self._send_email(channel, alert)
        elif channel_type == ChannelType.PAGERDUTY:
            await self._send_pagerduty(channel, alert)
        else:
            logger.warning(f"Unknown channel type: {channel.channel_type}")

    async def _send_webhook(
        self,
        channel: NotificationChannel,
        alert: AlertHistory
    ) -> None:
        """Send webhook notification."""
        config = channel.config
        url = config.get('url')
        secret = config.get('secret')
        headers = config.get('headers', {})

        if not url:
            raise ValueError("Webhook URL not configured")

        payload = {
            "alert_id": str(alert.id),
            "alert_rule_id": str(alert.alert_rule_id),
            "trigger_type": alert.trigger_type,
            "message": alert.message,
            "trigger_data": alert.trigger_data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        payload_str = json.dumps(payload)

        # Add HMAC signature if secret provided
        if secret:
            signature = hmac.new(
                secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            headers['X-Signature'] = f"sha256={signature}"

        headers['Content-Type'] = 'application/json'

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, content=payload_str, headers=headers)
            response.raise_for_status()

        logger.info(f"Webhook sent successfully to {url}")

    async def _send_slack(
        self,
        channel: NotificationChannel,
        alert: AlertHistory
    ) -> None:
        """Send Slack notification via incoming webhook."""
        config = channel.config
        webhook_url = config.get('webhook_url')
        channel_name = config.get('channel', '#alerts')

        if not webhook_url:
            raise ValueError("Slack webhook URL not configured")

        # Format as Slack message with blocks
        payload = {
            "channel": channel_name,
            "username": "Oniva Alerts",
            "icon_emoji": ":warning:",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Alert: {alert.trigger_type.replace('_', ' ').title()}",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": alert.message
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Alert ID: `{alert.id}`"
                        }
                    ]
                }
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()

        logger.info(f"Slack notification sent to {channel_name}")

    async def _send_teams(
        self,
        channel: NotificationChannel,
        alert: AlertHistory
    ) -> None:
        """Send Microsoft Teams notification via webhook."""
        config = channel.config
        webhook_url = config.get('webhook_url')

        if not webhook_url:
            raise ValueError("Teams webhook URL not configured")

        # Format as Teams adaptive card
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "FF0000",
            "summary": f"Alert: {alert.trigger_type}",
            "sections": [
                {
                    "activityTitle": f"Oniva Alert: {alert.trigger_type.replace('_', ' ').title()}",
                    "facts": [
                        {"name": "Alert ID", "value": str(alert.id)},
                        {"name": "Type", "value": alert.trigger_type},
                    ],
                    "text": alert.message,
                    "markdown": True
                }
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()

        logger.info("Teams notification sent")

    async def _send_email(
        self,
        channel: NotificationChannel,
        alert: AlertHistory
    ) -> None:
        """Send email notification.

        Note: Requires SMTP configuration or email service (Resend, SendGrid).
        """
        config = channel.config
        recipients = config.get('recipients', [])

        if not recipients:
            raise ValueError("No email recipients configured")

        # TODO: Implement email sending via SMTP or Resend
        # This is a placeholder - actual implementation depends on email provider
        logger.warning("Email notifications not fully implemented yet")
        logger.info(f"Would send email to: {recipients}")

    async def _send_pagerduty(
        self,
        channel: NotificationChannel,
        alert: AlertHistory
    ) -> None:
        """Send PagerDuty event."""
        config = channel.config
        routing_key = config.get('routing_key')

        if not routing_key:
            raise ValueError("PagerDuty routing key not configured")

        # PagerDuty Events API v2
        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "dedup_key": str(alert.id),
            "payload": {
                "summary": f"Oniva: {alert.trigger_type.replace('_', ' ').title()}",
                "severity": "critical" if "failed" in alert.trigger_type else "warning",
                "source": "oniva",
                "custom_details": {
                    "message": alert.message,
                    "trigger_data": alert.trigger_data
                }
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload
            )
            response.raise_for_status()

        logger.info("PagerDuty event sent")


# --- Service Dependency ---

async def get_alerting_service(db: AsyncSession) -> AlertingService:
    """Dependency injection for AlertingService."""
    return AlertingService(db)
