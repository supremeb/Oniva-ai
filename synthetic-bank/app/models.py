"""SQLAlchemy models for the synthetic banking schema."""

import uuid
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    String, Text, Numeric, Integer, Boolean, Date, DateTime,
    ForeignKey, Index, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------
class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(20))  # individual, corporate
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")  # low, medium, high, critical
    pep_status: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, inactive, frozen, closed
    onboarding_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    accounts: Mapped[list["Account"]] = relationship(back_populates="customer", lazy="selectin")
    kyc_records: Mapped[list["KYCRecord"]] = relationship(back_populates="customer", lazy="selectin")
    beneficial_owners: Mapped[list["BeneficialOwner"]] = relationship(back_populates="customer", lazy="selectin")
    risk_score: Mapped["RiskScore | None"] = relationship(back_populates="customer", uselist=False, lazy="selectin")
    compliance_alerts: Mapped[list["ComplianceAlert"]] = relationship(back_populates="customer", lazy="selectin")
    loans: Mapped[list["Loan"]] = relationship(back_populates="customer", lazy="selectin")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="customer", lazy="selectin")

    __table_args__ = (
        Index("ix_customers_entity_type", "entity_type"),
        Index("ix_customers_risk_level", "risk_level"),
        Index("ix_customers_status", "status"),
        Index("ix_customers_name", "last_name", "first_name"),
    )


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    account_number: Mapped[str] = mapped_column(String(30), unique=True)
    account_type: Mapped[str] = mapped_column(String(30))  # checking, savings, investment, loan
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, dormant, frozen, closed
    opened_date: Mapped[date] = mapped_column(Date)
    closed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account", lazy="selectin")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="account", lazy="selectin")

    __table_args__ = (
        Index("ix_accounts_customer_id", "customer_id"),
        Index("ix_accounts_type", "account_type"),
        Index("ix_accounts_status", "status"),
    )


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------
class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    transaction_type: Mapped[str] = mapped_column(String(30))  # deposit, withdrawal, transfer, payment, fee
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")  # pending, completed, failed, reversed
    transaction_date: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    account: Mapped["Account"] = relationship(back_populates="transactions")

    __table_args__ = (
        Index("ix_transactions_account_id", "account_id"),
        Index("ix_transactions_date", "transaction_date"),
        Index("ix_transactions_type", "transaction_type"),
        Index("ix_transactions_amount", "amount"),
    )


# ---------------------------------------------------------------------------
# Loan
# ---------------------------------------------------------------------------
class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    loan_type: Mapped[str] = mapped_column(String(30))  # mortgage, personal, auto, business
    principal: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    term_months: Mapped[int] = mapped_column(Integer)
    monthly_payment: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    outstanding_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, paid_off, defaulted, delinquent
    origination_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="loans")
    payments: Mapped[list["LoanPayment"]] = relationship(back_populates="loan", lazy="selectin")

    __table_args__ = (
        Index("ix_loans_customer_id", "customer_id"),
        Index("ix_loans_status", "status"),
        Index("ix_loans_type", "loan_type"),
    )


# ---------------------------------------------------------------------------
# LoanPayment
# ---------------------------------------------------------------------------
class LoanPayment(Base):
    __tablename__ = "loan_payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    loan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loans.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    payment_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="completed")  # completed, missed, late
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    loan: Mapped["Loan"] = relationship(back_populates="payments")

    __table_args__ = (
        Index("ix_loan_payments_loan_id", "loan_id"),
    )


# ---------------------------------------------------------------------------
# KYCRecord
# ---------------------------------------------------------------------------
class KYCRecord(Base):
    __tablename__ = "kyc_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    verification_level: Mapped[str] = mapped_column(String(20))  # basic, standard, enhanced
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, approved, rejected, expired
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="kyc_records")
    documents: Mapped[list["Document"]] = relationship(back_populates="kyc_record", lazy="selectin")

    __table_args__ = (
        Index("ix_kyc_records_customer_id", "customer_id"),
        Index("ix_kyc_records_status", "status"),
    )


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    kyc_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kyc_records.id", ondelete="CASCADE"))
    document_type: Mapped[str] = mapped_column(String(50))  # passport, drivers_license, utility_bill, articles_of_incorporation
    document_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issuing_country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    kyc_record: Mapped["KYCRecord"] = relationship(back_populates="documents")

    __table_args__ = (
        Index("ix_documents_kyc_record_id", "kyc_record_id"),
    )


# ---------------------------------------------------------------------------
# BeneficialOwner
# ---------------------------------------------------------------------------
class BeneficialOwner(Base):
    __tablename__ = "beneficial_owners"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    ownership_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pep_status: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="beneficial_owners")

    __table_args__ = (
        Index("ix_beneficial_owners_customer_id", "customer_id"),
    )


# ---------------------------------------------------------------------------
# RiskScore
# ---------------------------------------------------------------------------
class RiskScore(Base):
    __tablename__ = "risk_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), unique=True)
    overall_score: Mapped[int] = mapped_column(Integer)  # 0-100
    kyc_score: Mapped[int] = mapped_column(Integer)  # 0-100
    transaction_score: Mapped[int] = mapped_column(Integer)  # 0-100
    geography_score: Mapped[int] = mapped_column(Integer)  # 0-100
    pep_score: Mapped[int] = mapped_column(Integer)  # 0-100
    last_assessment_date: Mapped[datetime] = mapped_column(DateTime)
    next_review_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="risk_score")

    __table_args__ = (
        Index("ix_risk_scores_customer_id", "customer_id"),
    )


# ---------------------------------------------------------------------------
# ComplianceAlert
# ---------------------------------------------------------------------------
class ComplianceAlert(Base):
    __tablename__ = "compliance_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String(50))  # suspicious_activity, sanctions_match, pep_match, unusual_transaction, expired_review
    severity: Mapped[str] = mapped_column(String(20))  # low, medium, high, critical
    status: Mapped[str] = mapped_column(String(20), default="open")  # open, investigating, resolved, dismissed
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_date: Mapped[datetime] = mapped_column(DateTime)
    resolved_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship(back_populates="compliance_alerts")

    __table_args__ = (
        Index("ix_compliance_alerts_customer_id", "customer_id"),
        Index("ix_compliance_alerts_severity", "severity"),
        Index("ix_compliance_alerts_status", "status"),
        Index("ix_compliance_alerts_type", "alert_type"),
    )


# ---------------------------------------------------------------------------
# WatchlistEntry
# ---------------------------------------------------------------------------
class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    list_name: Mapped[str] = mapped_column(String(100))  # OFAC_SDN, EU_SANCTIONS, UN_SANCTIONS, PEP_LIST
    entity_name: Mapped[str] = mapped_column(String(255))
    entity_type: Mapped[str] = mapped_column(String(20))  # individual, organization
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    listed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_watchlist_entity_name", "entity_name"),
        Index("ix_watchlist_list_name", "list_name"),
    )


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------
class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(50))  # fraud_attempt, system_breach, data_leak, unauthorized_access, anomaly
    severity: Mapped[str] = mapped_column(String(20))  # low, medium, high, critical
    status: Mapped[str] = mapped_column(String(20), default="open")  # open, investigating, resolved, escalated
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reported_date: Mapped[datetime] = mapped_column(DateTime)
    resolved_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer | None"] = relationship(back_populates="incidents")

    __table_args__ = (
        Index("ix_incidents_customer_id", "customer_id"),
        Index("ix_incidents_severity", "severity"),
        Index("ix_incidents_status", "status"),
    )


# ---------------------------------------------------------------------------
# Holding (portfolio positions)
# ---------------------------------------------------------------------------
class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    ticker: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    current_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    market_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    account: Mapped["Account"] = relationship(back_populates="holdings")

    __table_args__ = (
        Index("ix_holdings_account_id", "account_id"),
        Index("ix_holdings_ticker", "ticker"),
    )


# ---------------------------------------------------------------------------
# TestScenario (metadata — maps scenario IDs to curated customer sets)
# ---------------------------------------------------------------------------
class TestScenario(Base):
    __tablename__ = "test_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    scenario_key: Mapped[str] = mapped_column(String(50), unique=True)  # e.g. SCENARIO_CLEAN_PASS
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    expected_outcome: Mapped[str] = mapped_column(String(50))  # pass, fail, flag, escalate
    workflow_target: Mapped[str] = mapped_column(String(100))  # which workflow this tests
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_test_scenarios_key", "scenario_key"),
    )
