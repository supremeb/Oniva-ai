"""Pydantic response models for the synthetic banking API."""

from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Shared pagination wrapper
# ---------------------------------------------------------------------------
class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class DocumentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kyc_record_id: uuid.UUID
    document_type: str
    document_number: str | None
    issuing_country: str | None
    expiry_date: date | None
    verified: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------
class KYCRecordSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    verification_level: str
    status: str
    reviewer: str | None
    notes: str | None
    review_date: datetime | None
    expiry_date: date | None
    created_at: datetime
    updated_at: datetime
    documents: list[DocumentSchema] = []


# ---------------------------------------------------------------------------
# BeneficialOwner
# ---------------------------------------------------------------------------
class BeneficialOwnerSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    name: str
    ownership_percentage: Decimal
    nationality: str | None
    pep_status: bool
    verified: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# RiskScore
# ---------------------------------------------------------------------------
class RiskScoreSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    overall_score: int
    kyc_score: int
    transaction_score: int
    geography_score: int
    pep_score: int
    last_assessment_date: datetime
    next_review_date: date
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# LoanPayment
# ---------------------------------------------------------------------------
class LoanPaymentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    loan_id: uuid.UUID
    amount: Decimal
    payment_date: date
    status: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Loan
# ---------------------------------------------------------------------------
class LoanSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    loan_type: str
    principal: Decimal
    interest_rate: Decimal
    term_months: int
    monthly_payment: Decimal
    outstanding_balance: Decimal
    status: str
    origination_date: date
    maturity_date: date
    created_at: datetime
    updated_at: datetime
    payments: list[LoanPaymentSchema] = []


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------
class TransactionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    transaction_type: str
    amount: Decimal
    currency: str
    description: str | None
    counterparty: str | None
    reference: str | None
    status: str
    transaction_date: datetime
    created_at: datetime


# ---------------------------------------------------------------------------
# Holding
# ---------------------------------------------------------------------------
class HoldingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    ticker: str
    name: str
    quantity: Decimal
    average_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    sector: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
class AccountSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    account_number: str
    account_type: str
    currency: str
    balance: Decimal
    status: str
    opened_date: date
    closed_date: date | None
    created_at: datetime
    updated_at: datetime


class AccountDetailSchema(AccountSchema):
    transactions: list[TransactionSchema] = []
    holdings: list[HoldingSchema] = []


# ---------------------------------------------------------------------------
# ComplianceAlert
# ---------------------------------------------------------------------------
class ComplianceAlertSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    alert_type: str
    severity: str
    status: str
    title: str
    description: str | None
    triggered_date: datetime
    resolved_date: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------
class IncidentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID | None
    incident_type: str
    severity: str
    status: str
    title: str
    description: str | None
    reported_by: str | None
    reported_date: datetime
    resolved_date: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# WatchlistEntry
# ---------------------------------------------------------------------------
class WatchlistEntrySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    list_name: str
    entity_name: str
    entity_type: str
    country: str | None
    aliases: str | None
    reason: str | None
    listed_date: date | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------
class CustomerSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    entity_type: str
    email: str
    phone: str | None
    date_of_birth: date | None
    nationality: str | None
    country: str
    address: str | None
    risk_level: str
    pep_status: bool
    status: str
    onboarding_date: date
    created_at: datetime
    updated_at: datetime


class CustomerDetailSchema(CustomerSchema):
    accounts: list[AccountSchema] = []
    kyc_records: list[KYCRecordSchema] = []
    beneficial_owners: list[BeneficialOwnerSchema] = []
    risk_score: RiskScoreSchema | None = None
    compliance_alerts: list[ComplianceAlertSchema] = []
    loans: list[LoanSchema] = []
    incidents: list[IncidentSchema] = []


# ---------------------------------------------------------------------------
# KYC response (customer + records + documents)
# ---------------------------------------------------------------------------
class CustomerKYCSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    first_name: str
    last_name: str
    kyc_records: list[KYCRecordSchema] = []


# ---------------------------------------------------------------------------
# Risk breakdown response
# ---------------------------------------------------------------------------
class CustomerRiskSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    first_name: str
    last_name: str
    risk_level: str
    pep_status: bool
    risk_score: RiskScoreSchema | None = None
    beneficial_owners: list[BeneficialOwnerSchema] = []


# ---------------------------------------------------------------------------
# Holdings response
# ---------------------------------------------------------------------------
class AccountHoldingsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    account_number: str
    account_type: str
    holdings: list[HoldingSchema] = []


# ---------------------------------------------------------------------------
# TestScenario
# ---------------------------------------------------------------------------
class TestScenarioSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scenario_key: str
    name: str
    description: str
    customer_id: uuid.UUID
    expected_outcome: str
    workflow_target: str
    created_at: datetime
