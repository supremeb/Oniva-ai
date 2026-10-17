from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# Import all models here for Alembic
from app.models.tenant import Tenant
from app.models.user import User
from app.models.api_key import APIKey
from app.models.connector import Connector
from app.models.custom_agent import CustomAgent
from app.models.workflow import Workflow, WorkflowExecution
from app.models.document import Document
from app.models.audit_log import AuditLog
from app.models.audit_event import AuditEvent
from app.models.skills import Skill, SkillExecution
from app.models.chat_session import ChatSession, ChatSessionMessage
from app.models.agent_version import AgentVersion
from app.models.agent_alias import AgentAlias
from app.models.background_job import BackgroundJob, JobStatus, JobType
from app.models.compliance_execution import ComplianceExecution, ComplianceScreeningResult, ComplianceStatus, ReviewDecision, RiskLevelEnum
from app.models.workflow_session import WorkflowSession, SessionStatus
from app.models.agent_chat_session import AgentChatSession
from app.models.agent_test_case import AgentTestCase
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.deploy_history import DeployHistory
from app.models.compliance_audit_log import (
    ComplianceAuditLog,
    WorkflowStateLog,
    HumanReviewTicket,
    AuditAction,
    AuditChainHead,
)
from app.models.workflow_checkpoint import WorkflowCheckpoint
from app.models.episodic_memory import EpisodicMemory
from app.models.user_preferences import UserPreferences
from app.models.pending_user import PendingUser
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution, ScheduleType, ScheduleStatus
from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist
from app.models.llm_provider import LLMProvider
from app.models.llm_usage import LLMUsageRecord
from app.models.workflow_entity import WorkflowEntity, EntityType
from app.models.oauth_client import OAuthClient
from app.models.workflow_batch import WorkflowBatch, WorkflowBatchItem, BatchStatus, BatchItemStatus
from app.models.workflow_permission import WorkflowPermission, GranteeType, PermissionLevel
from app.models.alerting import NotificationChannel, AlertRule, AlertHistory, ChannelType, TriggerType, NotificationStatus
from app.models.usage_quota import UsageQuota, UsageHistory, ScopeType, QuotaType, QuotaPeriod
from app.models.webhook_trigger import WebhookTrigger, WebhookDelivery
from app.models.team import Team, TeamMembership, TeamRole
from app.models.approval_request import ApprovalRequest, ApprovalStatus, ApprovalRequestType
from app.models.agent_learning import AgentLearning
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.agent_permission import AgentPermission
from app.models.agent_feedback import AgentFeedback
from app.models.sandbox_execution import SandboxExecution, SandboxExecutionStatus
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.routing_policy import RoutingPolicy
from app.models.run_event import RunEvent
from app.models.engine import ToolInvocation, EngineFence, EngineConversation
from app.models.deep_work_run import DeepWorkRun

__all__ = [
    'Base',
    'Tenant',
    'User',
    'APIKey',
    'Connector',
    'CustomAgent',
    'Workflow',
    'WorkflowExecution',
    'Document',
    'AuditLog',
    'AuditEvent',
    'Skill',
    'SkillExecution',
    'ChatSession',
    'AgentVersion',
    'AgentAlias',
    'BackgroundJob',
    'JobStatus',
    'JobType',
    'ComplianceExecution',
    'ComplianceScreeningResult',
    'ComplianceStatus',
    'ReviewDecision',
    'RiskLevelEnum',
    'WorkflowSession',
    'SessionStatus',
    'AgentChatSession',
    'AgentTestCase',
    'AgentAccuracyHistory',
    'DeployHistory',
    'ComplianceAuditLog',
    'WorkflowStateLog',
    'HumanReviewTicket',
    'AuditAction',
    'AuditChainHead',
    'WorkflowCheckpoint',
    'EpisodicMemory',
    'UserPreferences',
    'PendingUser',
    'PasswordResetToken',
    'RefreshToken',
    'ScheduledWorkflow',
    'ScheduledWorkflowExecution',
    'ScheduleType',
    'ScheduleStatus',
    'InternalEndpointWhitelist',
    'LLMProvider',
    'LLMUsageRecord',
    'WorkflowEntity',
    'EntityType',
    'OAuthClient',
    'WorkflowBatch',
    'WorkflowBatchItem',
    'BatchStatus',
    'BatchItemStatus',
    'WorkflowPermission',
    'GranteeType',
    'PermissionLevel',
    # Alerting
    'NotificationChannel',
    'AlertRule',
    'AlertHistory',
    'ChannelType',
    'TriggerType',
    'NotificationStatus',
    # Usage Quotas
    'UsageQuota',
    'UsageHistory',
    'ScopeType',
    'QuotaType',
    'QuotaPeriod',
    # Webhook Triggers
    'WebhookTrigger',
    'WebhookDelivery',
    # Teams (Phase A.2: RBAC)
    'Team',
    'TeamMembership',
    'TeamRole',
    # Approval Requests (Phase A.3: Approval Workflows)
    'ApprovalRequest',
    'ApprovalStatus',
    'ApprovalRequestType',
    # Agent Learning System
    'AgentLearning',
    # Knowledge Management (Frontier Phase C)
    'KnowledgeBase',
    'KnowledgeDocument',
    'KnowledgeChunk',
    # Org Admin Dashboard (Frontier Phase D)
    'AgentPermission',
    # Inline Feedback (Frontier Phase E)
    'AgentFeedback',
    # Sandbox Execution (Deep Work Phase 2)
    'SandboxExecution',
    'SandboxExecutionStatus',
    # Projects (Workspace Feature)
    'Project',
    'ProjectMember',
    # Model Routing (Deep Work Phase B5)
    'RoutingPolicy',
    # Run Observability (Platform Architecture Hardening E-1)
    'RunEvent',
    'ToolInvocation',
    'EngineFence',
    'EngineConversation',
    'DeepWorkRun',
]
