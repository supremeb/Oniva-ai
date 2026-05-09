"""Pydantic models for agent tool return values.

These models declare the shape of data that tools already return.
They do NOT change tool behavior -- they formalize existing contracts.
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any


# --- Analysis Agent (analysis.py) ---

class AnalysisReportResult(BaseModel):
    """Returned by save_analysis_report."""
    success: bool = True
    artifact_uri: Optional[str] = None
    file_path: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None


# --- Data Retrieval Agent (data_retrieval.py) ---

class VolumeFileResult(BaseModel):
    """Returned by read_volume_file."""
    success: bool = True
    volume: Optional[str] = None
    file_path: Optional[str] = None
    content: Optional[str] = None
    size_bytes: Optional[int] = None
    error: Optional[str] = None


class ConnectorFetchResult(BaseModel):
    """Returned by fetch_from_connector.

    Note: current implementation is a mock. This model matches the mock's shape.
    """
    connector_id: Optional[str] = None
    query: Optional[str] = None
    data: Optional[Any] = None
    timestamp: Optional[str] = None


class RestApiFetchResult(BaseModel):
    """Returned by fetch_from_rest_api.

    Success path: status_code + data + headers.
    Failure path: error + status="failed".
    """
    status_code: Optional[int] = None
    data: Optional[Any] = None
    headers: Optional[Dict[str, str]] = None
    error: Optional[str] = None
    status: Optional[str] = None


# --- Compliance Agent (compliance.py) ---

class ComplianceReportResult(BaseModel):
    """Returned by save_compliance_report."""
    success: bool = True
    artifact_uri: Optional[str] = None
    file_path: Optional[str] = None
    retention_years: Optional[int] = None
    error: Optional[str] = None
    warning: Optional[str] = None


class InsiderTradingRiskResult(BaseModel):
    """Returned by check_insider_trading_risk."""
    risk_level: str
    high_risk_role: bool
    material_events_present: bool
    recommendation: str


# --- Research Agent (research.py) ---

class ResearchVolumeFileResult(BaseModel):
    """Returned by read_from_volume (research agent version).

    Similar to VolumeFileResult but without size_bytes.
    """
    success: bool = True
    volume: Optional[str] = None
    file_path: Optional[str] = None
    content: Optional[str] = None
    error: Optional[str] = None


class ResearchReportResult(BaseModel):
    """Returned by save_research_report."""
    success: bool = True
    artifact_uri: Optional[str] = None
    file_path: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None


class DocumentSearchItem(BaseModel):
    """Single document in search results."""
    id: str
    title: Optional[str] = None
    content: str
    source: Optional[str] = None
    relevance_score: float = 0.0


class DocumentDetailResult(BaseModel):
    """Returned by get_document_by_id."""
    id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    source: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    error: Optional[str] = None
