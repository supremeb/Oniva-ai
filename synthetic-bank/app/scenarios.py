"""
Curated test scenario definitions with stable UUIDs.

Each scenario describes a specific compliance/risk testing persona. The UUIDs
are hardcoded so they survive across seed resets and remain referenceable in
test fixtures.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

# ---------------------------------------------------------------------------
# Stable customer UUIDs — one per scenario
# ---------------------------------------------------------------------------

SCENARIO_CLEAN_PASS_UUID         = uuid.UUID("00000000-0001-0000-0000-000000000001")
SCENARIO_SANCTIONS_HIT_UUID      = uuid.UUID("00000000-0002-0000-0000-000000000002")
SCENARIO_HIGH_RISK_TXN_UUID      = uuid.UUID("00000000-0003-0000-0000-000000000003")
SCENARIO_INCOMPLETE_KYC_UUID     = uuid.UUID("00000000-0004-0000-0000-000000000004")
SCENARIO_PEP_MATCH_UUID          = uuid.UUID("00000000-0005-0000-0000-000000000005")
SCENARIO_STRUCTURING_UUID        = uuid.UUID("00000000-0006-0000-0000-000000000006")
SCENARIO_EXPIRED_REVIEW_UUID     = uuid.UUID("00000000-0007-0000-0000-000000000007")
SCENARIO_MULTI_ACCOUNT_UUID      = uuid.UUID("00000000-0008-0000-0000-000000000008")
SCENARIO_LOAN_DEFAULT_UUID       = uuid.UUID("00000000-0009-0000-0000-000000000009")
SCENARIO_CLEAN_PORTFOLIO_UUID    = uuid.UUID("00000000-0010-0000-0000-000000000010")

# Stable TestScenario row UUIDs
SCENARIO_CLEAN_PASS_ROW_UUID      = uuid.UUID("00000001-0001-0000-0000-000000000001")
SCENARIO_SANCTIONS_HIT_ROW_UUID   = uuid.UUID("00000001-0002-0000-0000-000000000002")
SCENARIO_HIGH_RISK_TXN_ROW_UUID   = uuid.UUID("00000001-0003-0000-0000-000000000003")
SCENARIO_INCOMPLETE_KYC_ROW_UUID  = uuid.UUID("00000001-0004-0000-0000-000000000004")
SCENARIO_PEP_MATCH_ROW_UUID       = uuid.UUID("00000001-0005-0000-0000-000000000005")
SCENARIO_STRUCTURING_ROW_UUID     = uuid.UUID("00000001-0006-0000-0000-000000000006")
SCENARIO_EXPIRED_REVIEW_ROW_UUID  = uuid.UUID("00000001-0007-0000-0000-000000000007")
SCENARIO_MULTI_ACCOUNT_ROW_UUID   = uuid.UUID("00000001-0008-0000-0000-000000000008")
SCENARIO_LOAN_DEFAULT_ROW_UUID    = uuid.UUID("00000001-0009-0000-0000-000000000009")
SCENARIO_CLEAN_PORTFOLIO_ROW_UUID = uuid.UUID("00000001-0010-0000-0000-000000000010")

# Sanctions watchlist entry UUID — stable so reset can delete it cleanly
SCENARIO_SANCTIONS_WATCHLIST_UUID = uuid.UUID("00000002-0001-0000-0000-000000000001")

# Exact name used for the sanctions hit — must match exactly in both customer
# and watchlist_entries so screening logic finds the match.
SANCTIONS_HIT_NAME = "Viktor Aleksandr Morozov"


def _today() -> date:
    return date.today()


def _dt(days_ago: int = 0) -> datetime:
    return datetime.utcnow() - timedelta(days=days_ago)


def _d(days_ago: int = 0) -> date:
    return _today() - timedelta(days=days_ago)


# ---------------------------------------------------------------------------
# SCENARIO DEFINITIONS
# Each dict carries all data needed to build the full graph in seed.py.
# Fields that reference sub-objects are plain Python lists/dicts; seed.py
# maps them to ORM objects.
# ---------------------------------------------------------------------------

SCENARIOS: list[dict] = [
    # ------------------------------------------------------------------
    # 1. CLEAN PASS
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_CLEAN_PASS_ROW_UUID,
        "scenario_key": "SCENARIO_CLEAN_PASS",
        "name": "Clean KYC Pass",
        "description": (
            "Fully onboarded individual with approved KYC, all documents verified, "
            "no compliance alerts, and normal transaction history. Used as the positive "
            "control — every KYC/onboarding workflow should pass this customer."
        ),
        "expected_outcome": "pass",
        "workflow_target": "kyc_onboarding",
        "customer": {
            "id": SCENARIO_CLEAN_PASS_UUID,
            "first_name": "Eleanor",
            "last_name": "Whitfield",
            "entity_type": "individual",
            "email": "eleanor.whitfield@example.com",
            "phone": "+1-555-0101",
            "date_of_birth": date(1982, 4, 15),
            "nationality": "US",
            "country": "United States",
            "address": "142 Maple Grove Lane, Portland, OR 97201",
            "risk_level": "low",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(400),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0001-0001-0000-000000000001"),
                "account_number": "SCN-001-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("12450.00"),
                "status": "active",
                "opened_date": _d(400),
            },
            {
                "id": uuid.UUID("00000003-0001-0002-0000-000000000001"),
                "account_number": "SCN-001-SAV-0001",
                "account_type": "savings",
                "currency": "USD",
                "balance": Decimal("34200.00"),
                "status": "active",
                "opened_date": _d(395),
            },
        ],
        "transactions": [
            # Regular salary deposits and routine spending
            {
                "account_key": "SCN-001-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("5500.00"),
                "description": "Monthly salary deposit",
                "counterparty": "Acme Corp Payroll",
                "reference": "PAY-2024-0301",
                "status": "completed",
                "transaction_date": _dt(30),
            },
            {
                "account_key": "SCN-001-CHK-0001",
                "transaction_type": "payment",
                "amount": Decimal("1850.00"),
                "description": "Mortgage payment",
                "counterparty": "Pacific Mortgage Services",
                "reference": "MTG-2024-03",
                "status": "completed",
                "transaction_date": _dt(28),
            },
            {
                "account_key": "SCN-001-CHK-0001",
                "transaction_type": "withdrawal",
                "amount": Decimal("300.00"),
                "description": "ATM withdrawal",
                "counterparty": None,
                "reference": "ATM-PDX-00127",
                "status": "completed",
                "transaction_date": _dt(15),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0001-0001-0000-000000000001"),
                "verification_level": "standard",
                "status": "approved",
                "reviewer": "compliance.officer@bank.com",
                "notes": "All documents verified. Clean background check.",
                "review_date": _dt(390),
                "expiry_date": _d(365),  # expires in ~1 year from now
                "documents": [
                    {
                        "document_type": "passport",
                        "document_number": "US-P-987654321",
                        "issuing_country": "United States",
                        "expiry_date": _d(-730),  # expires in 2 years
                        "verified": True,
                    },
                    {
                        "document_type": "utility_bill",
                        "document_number": None,
                        "issuing_country": "United States",
                        "expiry_date": None,
                        "verified": True,
                    },
                ],
            }
        ],
        "risk_score": {
            "overall_score": 12,
            "kyc_score": 5,
            "transaction_score": 8,
            "geography_score": 10,
            "pep_score": 0,
            "last_assessment_date": _dt(30),
            "next_review_date": _d(-335),  # review in ~1 year
        },
        "compliance_alerts": [],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 2. SANCTIONS HIT
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_SANCTIONS_HIT_ROW_UUID,
        "scenario_key": "SCENARIO_SANCTIONS_HIT",
        "name": "Sanctions Watchlist Match",
        "description": (
            "Customer's full name exactly matches an OFAC SDN watchlist entry. "
            "Screening workflows must detect the match and block/escalate the account."
        ),
        "expected_outcome": "fail",
        "workflow_target": "sanctions_screening",
        "customer": {
            "id": SCENARIO_SANCTIONS_HIT_UUID,
            "first_name": "Viktor",
            "last_name": "Aleksandr Morozov",  # first+last == SANCTIONS_HIT_NAME
            "entity_type": "individual",
            "email": "v.morozov.personal@mailnet.ru",
            "phone": "+7-495-555-0199",
            "date_of_birth": date(1971, 9, 3),
            "nationality": "Russian",
            "country": "Russia",
            "address": "Ulitsa Lenina 12, Moscow, 101000",
            "risk_level": "critical",
            "pep_status": False,
            "status": "frozen",
            "onboarding_date": _d(180),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0002-0001-0000-000000000002"),
                "account_number": "SCN-002-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("88350.00"),
                "status": "frozen",
                "opened_date": _d(180),
            }
        ],
        "transactions": [
            {
                "account_key": "SCN-002-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("88350.00"),
                "description": "Wire transfer from overseas",
                "counterparty": "Novatech Holdings Ltd",
                "reference": "WIRE-20240115-001",
                "status": "completed",
                "transaction_date": _dt(170),
            }
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0002-0001-0000-000000000002"),
                "verification_level": "basic",
                "status": "rejected",
                "reviewer": "aml.system@bank.com",
                "notes": "OFAC SDN match detected. Account frozen pending review.",
                "review_date": _dt(160),
                "expiry_date": None,
                "documents": [
                    {
                        "document_type": "passport",
                        "document_number": "RU-6512-889002",
                        "issuing_country": "Russia",
                        "expiry_date": _d(-200),
                        "verified": False,
                    }
                ],
            }
        ],
        "risk_score": {
            "overall_score": 98,
            "kyc_score": 95,
            "transaction_score": 90,
            "geography_score": 95,
            "pep_score": 50,
            "last_assessment_date": _dt(160),
            "next_review_date": _d(-130),
        },
        "compliance_alerts": [
            {
                "alert_type": "sanctions_match",
                "severity": "critical",
                "status": "investigating",
                "title": "OFAC SDN Match — Viktor Aleksandr Morozov",
                "description": (
                    "Automated screening identified exact name match against OFAC "
                    "Specially Designated Nationals list. Account frozen automatically."
                ),
                "triggered_date": _dt(160),
            }
        ],
        "loans": [],
        "holdings": [],
        # This watchlist entry is the key fixture — same name as customer
        "watchlist_entries": [
            {
                "id": SCENARIO_SANCTIONS_WATCHLIST_UUID,
                "list_name": "OFAC_SDN",
                "entity_name": SANCTIONS_HIT_NAME,
                "entity_type": "individual",
                "country": "Russia",
                "aliases": "Viktor Morozov, V.A. Morozov",
                "reason": "Designated for financing prohibited weapons procurement network.",
                "listed_date": date(2021, 3, 15),
            }
        ],
    },

    # ------------------------------------------------------------------
    # 3. HIGH RISK TRANSACTION
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_HIGH_RISK_TXN_ROW_UUID,
        "scenario_key": "SCENARIO_HIGH_RISK_TXN",
        "name": "High-Value Single Transaction",
        "description": (
            "Account contains a single transaction of $500,000+. Transaction monitoring "
            "rules must flag and escalate to senior compliance for review."
        ),
        "expected_outcome": "escalate",
        "workflow_target": "transaction_monitoring",
        "customer": {
            "id": SCENARIO_HIGH_RISK_TXN_UUID,
            "first_name": "Marcus",
            "last_name": "Okonkwo",
            "entity_type": "corporate",
            "email": "m.okonkwo@okonkwo-holdings.com",
            "phone": "+1-212-555-0340",
            "date_of_birth": None,
            "nationality": "Nigerian",
            "country": "United States",
            "address": "350 Fifth Avenue, Suite 6100, New York, NY 10118",
            "risk_level": "high",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(600),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0003-0001-0000-000000000003"),
                "account_number": "SCN-003-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("1240000.00"),
                "status": "active",
                "opened_date": _d(600),
            }
        ],
        "transactions": [
            # The trigger: one massive deposit
            {
                "account_key": "SCN-003-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("750000.00"),
                "description": "Real estate acquisition proceeds",
                "counterparty": "Atlantic Title & Escrow LLC",
                "reference": "ESC-2024-NY-00443",
                "status": "completed",
                "transaction_date": _dt(10),
            },
            # Some normal activity to show contrast
            {
                "account_key": "SCN-003-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("18500.00"),
                "description": "Consulting fee",
                "counterparty": "BuildTech Partners",
                "reference": "INV-BTP-2024-0812",
                "status": "completed",
                "transaction_date": _dt(45),
            },
            {
                "account_key": "SCN-003-CHK-0001",
                "transaction_type": "payment",
                "amount": Decimal("4200.00"),
                "description": "Office rent",
                "counterparty": "Manhattan Commercial Properties",
                "reference": "RENT-2024-03",
                "status": "completed",
                "transaction_date": _dt(30),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0003-0001-0000-000000000003"),
                "verification_level": "enhanced",
                "status": "approved",
                "reviewer": "senior.compliance@bank.com",
                "notes": "Corporate entity — enhanced due diligence completed.",
                "review_date": _dt(590),
                "expiry_date": _d(10),  # approaching expiry
                "documents": [
                    {
                        "document_type": "articles_of_incorporation",
                        "document_number": "NY-CORP-2018-443821",
                        "issuing_country": "United States",
                        "expiry_date": None,
                        "verified": True,
                    }
                ],
            }
        ],
        "risk_score": {
            "overall_score": 72,
            "kyc_score": 30,
            "transaction_score": 90,
            "geography_score": 40,
            "pep_score": 0,
            "last_assessment_date": _dt(10),
            "next_review_date": _d(-90),
        },
        "compliance_alerts": [
            {
                "alert_type": "unusual_transaction",
                "severity": "high",
                "status": "open",
                "title": "Large Transaction Alert — $750,000 Deposit",
                "description": (
                    "Single deposit of $750,000 detected. Exceeds automated threshold of "
                    "$500,000. Requires senior compliance review within 48 hours."
                ),
                "triggered_date": _dt(10),
            }
        ],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 4. INCOMPLETE KYC
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_INCOMPLETE_KYC_ROW_UUID,
        "scenario_key": "SCENARIO_INCOMPLETE_KYC",
        "name": "Incomplete KYC — Missing Passport",
        "description": (
            "Customer has a pending KYC record with drivers_license submitted but "
            "passport missing. Onboarding workflow must flag as incomplete and block "
            "account activation until all required documents are received."
        ),
        "expected_outcome": "flag",
        "workflow_target": "kyc_onboarding",
        "customer": {
            "id": SCENARIO_INCOMPLETE_KYC_UUID,
            "first_name": "Priya",
            "last_name": "Nair",
            "entity_type": "individual",
            "email": "priya.nair.onboarding@tempmail.io",
            "phone": "+44-20-7946-0811",
            "date_of_birth": date(1995, 11, 22),
            "nationality": "Indian",
            "country": "United Kingdom",
            "address": "47 Clerkenwell Road, London, EC1R 5BN",
            "risk_level": "medium",
            "pep_status": False,
            "status": "inactive",
            "onboarding_date": _d(45),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0004-0001-0000-000000000004"),
                "account_number": "SCN-004-CHK-0001",
                "account_type": "checking",
                "currency": "GBP",
                "balance": Decimal("0.00"),
                "status": "frozen",
                "opened_date": _d(45),
            }
        ],
        "transactions": [],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0004-0001-0000-000000000004"),
                "verification_level": "standard",
                "status": "pending",
                "reviewer": None,
                "notes": (
                    "Passport document not submitted. Customer provided drivers license "
                    "only. Follow-up email sent 2024-02-20. Awaiting passport copy."
                ),
                "review_date": None,
                "expiry_date": None,
                "documents": [
                    {
                        "document_type": "drivers_license",
                        "document_number": "UK-DL-PN-19951122",
                        "issuing_country": "United Kingdom",
                        "expiry_date": _d(-730),
                        "verified": True,
                    }
                    # Passport intentionally absent — this is the test trigger
                ],
            }
        ],
        "risk_score": {
            "overall_score": 45,
            "kyc_score": 70,  # high because incomplete
            "transaction_score": 5,
            "geography_score": 20,
            "pep_score": 0,
            "last_assessment_date": _dt(45),
            "next_review_date": _d(-15),
        },
        "compliance_alerts": [
            {
                "alert_type": "suspicious_activity",
                "severity": "medium",
                "status": "open",
                "title": "KYC Incomplete — Passport Document Missing",
                "description": (
                    "Standard KYC requires passport or national ID. Only drivers license "
                    "submitted. Account activation blocked until passport is provided."
                ),
                "triggered_date": _dt(44),
            }
        ],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 5. PEP MATCH
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_PEP_MATCH_ROW_UUID,
        "scenario_key": "SCENARIO_PEP_MATCH",
        "name": "Politically Exposed Person",
        "description": (
            "Customer is flagged as a PEP (Politically Exposed Person). Enhanced due "
            "diligence workflow must trigger, requiring source-of-wealth documentation "
            "and senior approval before account activation."
        ),
        "expected_outcome": "escalate",
        "workflow_target": "pep_due_diligence",
        "customer": {
            "id": SCENARIO_PEP_MATCH_UUID,
            "first_name": "Amara",
            "last_name": "Diallo",
            "entity_type": "individual",
            "email": "a.diallo.private@senegal-gov.sn",
            "phone": "+221-77-555-0042",
            "date_of_birth": date(1968, 7, 14),
            "nationality": "Senegalese",
            "country": "Senegal",
            "address": "Avenue Cheikh Anta Diop, Dakar, BP 4029",
            "risk_level": "high",
            "pep_status": True,  # KEY: PEP flag set
            "status": "active",
            "onboarding_date": _d(200),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0005-0001-0000-000000000005"),
                "account_number": "SCN-005-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("215000.00"),
                "status": "active",
                "opened_date": _d(200),
            },
            {
                "id": uuid.UUID("00000003-0005-0002-0000-000000000005"),
                "account_number": "SCN-005-SAV-0001",
                "account_type": "savings",
                "currency": "EUR",
                "balance": Decimal("340000.00"),
                "status": "active",
                "opened_date": _d(195),
            },
        ],
        "transactions": [
            {
                "account_key": "SCN-005-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("85000.00"),
                "description": "Consulting retainer — Afrique Capital Partners",
                "counterparty": "Afrique Capital Partners SA",
                "reference": "ACP-RET-2024-001",
                "status": "completed",
                "transaction_date": _dt(60),
            },
            {
                "account_key": "SCN-005-SAV-0001",
                "transaction_type": "transfer",
                "amount": Decimal("50000.00"),
                "description": "Transfer from external account",
                "counterparty": "BNP Paribas Dakar",
                "reference": "SWIFT-BNPP-20240110",
                "status": "completed",
                "transaction_date": _dt(90),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0005-0001-0000-000000000005"),
                "verification_level": "enhanced",
                "status": "approved",
                "reviewer": "edd.team@bank.com",
                "notes": (
                    "PEP status confirmed — former minister of finance. Enhanced due "
                    "diligence completed. Source of wealth: government pension + "
                    "consulting income. Annual review required."
                ),
                "review_date": _dt(190),
                "expiry_date": _d(-165),  # next review in ~6 months
                "documents": [
                    {
                        "document_type": "passport",
                        "document_number": "SN-P-A12345678",
                        "issuing_country": "Senegal",
                        "expiry_date": _d(-500),
                        "verified": True,
                    },
                    {
                        "document_type": "utility_bill",
                        "document_number": None,
                        "issuing_country": "Senegal",
                        "expiry_date": None,
                        "verified": True,
                    },
                ],
            }
        ],
        "risk_score": {
            "overall_score": 68,
            "kyc_score": 20,
            "transaction_score": 55,
            "geography_score": 60,
            "pep_score": 100,  # maximum PEP score
            "last_assessment_date": _dt(30),
            "next_review_date": _d(-165),
        },
        "compliance_alerts": [
            {
                "alert_type": "pep_match",
                "severity": "high",
                "status": "resolved",
                "title": "PEP Flag — Enhanced Due Diligence Required",
                "description": (
                    "Customer identified as PEP: former Minister of Finance, Republic of "
                    "Senegal. EDD completed and documented. Ongoing monitoring active."
                ),
                "triggered_date": _dt(200),
            }
        ],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 6. STRUCTURING
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_STRUCTURING_ROW_UUID,
        "scenario_key": "SCENARIO_STRUCTURING",
        "name": "Transaction Structuring (Smurfing)",
        "description": (
            "Multiple cash deposits just below the $10,000 CTR reporting threshold "
            "within the same week. Pattern indicates deliberate structuring to evade "
            "mandatory currency transaction reporting (31 U.S.C. § 5324). "
            "Workflow must file or recommend a SAR."
        ),
        "expected_outcome": "flag",
        "workflow_target": "sar_evaluation",
        "customer": {
            "id": SCENARIO_STRUCTURING_UUID,
            "first_name": "Terrence",
            "last_name": "Bouchard",
            "entity_type": "individual",
            "email": "terry.bouchard82@webmail.net",
            "phone": "+1-504-555-0278",
            "date_of_birth": date(1982, 3, 7),
            "nationality": "US",
            "country": "United States",
            "address": "882 Bourbon Street, New Orleans, LA 70116",
            "risk_level": "high",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(730),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0006-0001-0000-000000000006"),
                "account_number": "SCN-006-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("47800.00"),
                "status": "active",
                "opened_date": _d(730),
            }
        ],
        # 7 deposits in 7 days, all $9,500-$9,999 — classic structuring pattern
        "transactions": [
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9800.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-001",
                "status": "completed",
                "transaction_date": _dt(7),
            },
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9500.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-002",
                "status": "completed",
                "transaction_date": _dt(6),
            },
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9750.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-003",
                "status": "completed",
                "transaction_date": _dt(5),
            },
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9999.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-004",
                "status": "completed",
                "transaction_date": _dt(4),
            },
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9600.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-005",
                "status": "completed",
                "transaction_date": _dt(3),
            },
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9850.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-006",
                "status": "completed",
                "transaction_date": _dt(2),
            },
            {
                "account_key": "SCN-006-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("9700.00"),
                "description": "Cash deposit",
                "counterparty": None,
                "reference": "CASH-DEP-007",
                "status": "completed",
                "transaction_date": _dt(1),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0006-0001-0000-000000000006"),
                "verification_level": "standard",
                "status": "approved",
                "reviewer": "kyc.team@bank.com",
                "notes": "Standard KYC passed at onboarding.",
                "review_date": _dt(720),
                "expiry_date": _d(-270),  # ~9 months away
                "documents": [
                    {
                        "document_type": "drivers_license",
                        "document_number": "LA-DL-T-198230700",
                        "issuing_country": "United States",
                        "expiry_date": _d(-730),
                        "verified": True,
                    }
                ],
            }
        ],
        "risk_score": {
            "overall_score": 85,
            "kyc_score": 20,
            "transaction_score": 95,
            "geography_score": 30,
            "pep_score": 0,
            "last_assessment_date": _dt(1),
            "next_review_date": _d(-90),
        },
        "compliance_alerts": [
            {
                "alert_type": "suspicious_activity",
                "severity": "high",
                "status": "investigating",
                "title": "Structuring Pattern Detected — 7 Sub-Threshold Deposits in 7 Days",
                "description": (
                    "System detected 7 cash deposits ranging $9,500-$9,999 over 7 "
                    "consecutive days. Total: $68,199. Pattern is consistent with "
                    "deliberate structuring to evade $10,000 CTR requirement (31 USC 5324). "
                    "SAR filing recommended."
                ),
                "triggered_date": _dt(1),
            }
        ],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 7. EXPIRED REVIEW
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_EXPIRED_REVIEW_ROW_UUID,
        "scenario_key": "SCENARIO_EXPIRED_REVIEW",
        "name": "Overdue Compliance Review",
        "description": (
            "Customer's last risk assessment was more than 12 months ago and "
            "next_review_date is in the past. Periodic review workflow must trigger "
            "a re-assessment task automatically."
        ),
        "expected_outcome": "flag",
        "workflow_target": "periodic_review",
        "customer": {
            "id": SCENARIO_EXPIRED_REVIEW_UUID,
            "first_name": "Harold",
            "last_name": "Steinberg",
            "entity_type": "individual",
            "email": "hsteinberg@steinberg-associates.com",
            "phone": "+1-312-555-0177",
            "date_of_birth": date(1955, 6, 30),
            "nationality": "US",
            "country": "United States",
            "address": "233 S Wacker Drive, Chicago, IL 60606",
            "risk_level": "medium",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(1400),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0007-0001-0000-000000000007"),
                "account_number": "SCN-007-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("28700.00"),
                "status": "active",
                "opened_date": _d(1400),
            }
        ],
        "transactions": [
            {
                "account_key": "SCN-007-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("6200.00"),
                "description": "Consulting income",
                "counterparty": "Midwest Advisory Group",
                "reference": "MAG-INV-2024-047",
                "status": "completed",
                "transaction_date": _dt(60),
            }
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0007-0001-0000-000000000007"),
                "verification_level": "standard",
                "status": "expired",
                "reviewer": "kyc.team@bank.com",
                "notes": "Annual review not completed. KYC record expired.",
                "review_date": _dt(430),  # last reviewed 430 days ago
                "expiry_date": _d(65),  # expired 65 days ago
                "documents": [
                    {
                        "document_type": "passport",
                        "document_number": "US-P-123456789",
                        "issuing_country": "United States",
                        "expiry_date": _d(-200),
                        "verified": True,
                    }
                ],
            }
        ],
        "risk_score": {
            "overall_score": 48,
            "kyc_score": 60,
            "transaction_score": 25,
            "geography_score": 15,
            "pep_score": 0,
            "last_assessment_date": _dt(430),    # last assessed 430 days ago
            "next_review_date": _d(65),           # overdue by 65 days (in the past)
        },
        "compliance_alerts": [
            {
                "alert_type": "expired_review",
                "severity": "medium",
                "status": "open",
                "title": "Periodic Review Overdue — 430 Days Since Last Assessment",
                "description": (
                    "Customer's scheduled annual compliance review was due 65 days ago. "
                    "Risk score may not reflect current customer profile. Re-assessment "
                    "required within 30 days per policy."
                ),
                "triggered_date": _dt(65),
            }
        ],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 8. MULTI-ACCOUNT LAYERING
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_MULTI_ACCOUNT_ROW_UUID,
        "scenario_key": "SCENARIO_MULTI_ACCOUNT",
        "name": "Multi-Account Layering Pattern",
        "description": (
            "Customer holds four accounts. Funds are deposited into account A, "
            "transferred to B, then C, then withdrawn from D — a classic layering "
            "pattern. Cross-account transfer analysis workflow must flag this as "
            "potential money laundering."
        ),
        "expected_outcome": "flag",
        "workflow_target": "aml_layering_detection",
        "customer": {
            "id": SCENARIO_MULTI_ACCOUNT_UUID,
            "first_name": "Catalina",
            "last_name": "Vega-Romero",
            "entity_type": "individual",
            "email": "c.vegaromero@pvt.email",
            "phone": "+34-91-555-0204",
            "date_of_birth": date(1979, 2, 28),
            "nationality": "Spanish",
            "country": "Spain",
            "address": "Calle Gran Via 32, 4A, Madrid, 28013",
            "risk_level": "high",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(500),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0008-0001-0000-000000000008"),
                "account_number": "SCN-008-CHK-0001",
                "account_type": "checking",
                "currency": "EUR",
                "balance": Decimal("1200.00"),
                "status": "active",
                "opened_date": _d(500),
            },
            {
                "id": uuid.UUID("00000003-0008-0002-0000-000000000008"),
                "account_number": "SCN-008-SAV-0001",
                "account_type": "savings",
                "currency": "EUR",
                "balance": Decimal("850.00"),
                "status": "active",
                "opened_date": _d(480),
            },
            {
                "id": uuid.UUID("00000003-0008-0003-0000-000000000008"),
                "account_number": "SCN-008-INV-0001",
                "account_type": "investment",
                "currency": "USD",
                "balance": Decimal("200.00"),
                "status": "active",
                "opened_date": _d(300),
            },
            {
                "id": uuid.UUID("00000003-0008-0004-0000-000000000008"),
                "account_number": "SCN-008-SAV-0002",
                "account_type": "savings",
                "currency": "USD",
                "balance": Decimal("500.00"),
                "status": "active",
                "opened_date": _d(200),
            },
        ],
        # Layering chain: deposit → A, transfer A→B, B→C, withdraw C
        "transactions": [
            {
                "account_key": "SCN-008-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("45000.00"),
                "description": "Initial placement",
                "counterparty": "External Wire",
                "reference": "SWIFT-PLACE-001",
                "status": "completed",
                "transaction_date": _dt(20),
            },
            {
                "account_key": "SCN-008-CHK-0001",
                "transaction_type": "transfer",
                "amount": Decimal("44500.00"),
                "description": "Transfer to savings",
                "counterparty": "SCN-008-SAV-0001",
                "reference": "INT-TXF-001",
                "status": "completed",
                "transaction_date": _dt(19),
            },
            {
                "account_key": "SCN-008-SAV-0001",
                "transaction_type": "transfer",
                "amount": Decimal("44000.00"),
                "description": "Transfer to investment",
                "counterparty": "SCN-008-INV-0001",
                "reference": "INT-TXF-002",
                "status": "completed",
                "transaction_date": _dt(18),
            },
            {
                "account_key": "SCN-008-INV-0001",
                "transaction_type": "transfer",
                "amount": Decimal("43500.00"),
                "description": "Transfer to secondary savings",
                "counterparty": "SCN-008-SAV-0002",
                "reference": "INT-TXF-003",
                "status": "completed",
                "transaction_date": _dt(17),
            },
            {
                "account_key": "SCN-008-SAV-0002",
                "transaction_type": "withdrawal",
                "amount": Decimal("43000.00"),
                "description": "Cash withdrawal — integration",
                "counterparty": None,
                "reference": "CASH-OUT-001",
                "status": "completed",
                "transaction_date": _dt(16),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0008-0001-0000-000000000008"),
                "verification_level": "standard",
                "status": "approved",
                "reviewer": "kyc.team@bank.com",
                "notes": "Standard KYC completed at onboarding.",
                "review_date": _dt(495),
                "expiry_date": _d(-230),
                "documents": [
                    {
                        "document_type": "passport",
                        "document_number": "ES-P-XDB123456",
                        "issuing_country": "Spain",
                        "expiry_date": _d(-400),
                        "verified": True,
                    }
                ],
            }
        ],
        "risk_score": {
            "overall_score": 82,
            "kyc_score": 25,
            "transaction_score": 95,
            "geography_score": 45,
            "pep_score": 0,
            "last_assessment_date": _dt(16),
            "next_review_date": _d(-140),
        },
        "compliance_alerts": [
            {
                "alert_type": "suspicious_activity",
                "severity": "high",
                "status": "investigating",
                "title": "Multi-Account Layering Pattern Detected",
                "description": (
                    "$45,000 placed in account A and layered through 3 additional "
                    "accounts within 5 days, with final cash withdrawal of $43,000. "
                    "Pattern consistent with money laundering layering stage."
                ),
                "triggered_date": _dt(16),
            }
        ],
        "loans": [],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 9. LOAN DEFAULT
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_LOAN_DEFAULT_ROW_UUID,
        "scenario_key": "SCENARIO_LOAN_DEFAULT",
        "name": "Loan Default — Missed Payments",
        "description": (
            "Customer has a delinquent personal loan with 3 missed payments. "
            "Credit deterioration workflow must trigger risk escalation and "
            "collection process initiation."
        ),
        "expected_outcome": "escalate",
        "workflow_target": "loan_collections",
        "customer": {
            "id": SCENARIO_LOAN_DEFAULT_UUID,
            "first_name": "Darnell",
            "last_name": "Watkins",
            "entity_type": "individual",
            "email": "d.watkins.personal@gmail.example",
            "phone": "+1-404-555-0388",
            "date_of_birth": date(1988, 12, 5),
            "nationality": "US",
            "country": "United States",
            "address": "1540 Peachtree St NE, Apt 12, Atlanta, GA 30309",
            "risk_level": "high",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(900),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0009-0001-0000-000000000009"),
                "account_number": "SCN-009-CHK-0001",
                "account_type": "checking",
                "currency": "USD",
                "balance": Decimal("480.00"),  # nearly empty — cashflow problem
                "status": "active",
                "opened_date": _d(900),
            }
        ],
        "transactions": [
            {
                "account_key": "SCN-009-CHK-0001",
                "transaction_type": "deposit",
                "amount": Decimal("2800.00"),
                "description": "Paycheck",
                "counterparty": "Metro Transit Authority",
                "reference": "PAY-2024-0228",
                "status": "completed",
                "transaction_date": _dt(60),
            },
            {
                "account_key": "SCN-009-CHK-0001",
                "transaction_type": "payment",
                "amount": Decimal("1200.00"),
                "description": "Rent",
                "counterparty": "Peachtree Properties LLC",
                "reference": "RENT-FEB-2024",
                "status": "completed",
                "transaction_date": _dt(58),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0009-0001-0000-000000000009"),
                "verification_level": "basic",
                "status": "approved",
                "reviewer": "kyc.team@bank.com",
                "notes": "Basic KYC completed.",
                "review_date": _dt(890),
                "expiry_date": _d(-170),
                "documents": [
                    {
                        "document_type": "drivers_license",
                        "document_number": "GA-DL-DW-19881205",
                        "issuing_country": "United States",
                        "expiry_date": _d(-365),
                        "verified": True,
                    }
                ],
            }
        ],
        "risk_score": {
            "overall_score": 77,
            "kyc_score": 30,
            "transaction_score": 70,
            "geography_score": 20,
            "pep_score": 0,
            "last_assessment_date": _dt(30),
            "next_review_date": _d(-60),
        },
        "compliance_alerts": [
            {
                "alert_type": "unusual_transaction",
                "severity": "high",
                "status": "open",
                "title": "Loan Delinquency — 3 Consecutive Missed Payments",
                "description": (
                    "Personal loan #SCN-LOAN-0009 is 90 days past due. Three consecutive "
                    "monthly payments missed. Outstanding balance: $11,850. "
                    "Collection workflow triggered."
                ),
                "triggered_date": _dt(30),
            }
        ],
        "loans": [
            {
                "id": uuid.UUID("00000005-0009-0001-0000-000000000009"),
                "loan_type": "personal",
                "principal": Decimal("15000.00"),
                "interest_rate": Decimal("0.1199"),
                "term_months": 48,
                "monthly_payment": Decimal("395.00"),
                "outstanding_balance": Decimal("11850.00"),
                "status": "delinquent",
                "origination_date": _d(720),
                "maturity_date": _d(-630),  # 4-year term from 720 days ago
                "payments": [
                    # First year: on-time payments
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(690),
                        "status": "completed",
                    },
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(660),
                        "status": "completed",
                    },
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(630),
                        "status": "completed",
                    },
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(600),
                        "status": "completed",
                    },
                    # Then starts missing
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(90),
                        "status": "missed",
                    },
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(60),
                        "status": "missed",
                    },
                    {
                        "amount": Decimal("395.00"),
                        "payment_date": _d(30),
                        "status": "missed",
                    },
                ],
            }
        ],
        "holdings": [],
        "watchlist_entries": [],
    },

    # ------------------------------------------------------------------
    # 10. CLEAN PORTFOLIO
    # ------------------------------------------------------------------
    {
        "row_uuid": SCENARIO_CLEAN_PORTFOLIO_ROW_UUID,
        "scenario_key": "SCENARIO_CLEAN_PORTFOLIO",
        "name": "Clean Investment Portfolio",
        "description": (
            "Fully compliant customer with a diversified investment account. "
            "Used as negative test — all portfolio compliance checks must pass. "
            "No alerts, current KYC, diversified holdings across sectors."
        ),
        "expected_outcome": "pass",
        "workflow_target": "portfolio_compliance",
        "customer": {
            "id": SCENARIO_CLEAN_PORTFOLIO_UUID,
            "first_name": "Ingrid",
            "last_name": "Svensson",
            "entity_type": "individual",
            "email": "i.svensson@nordfinance.se",
            "phone": "+46-8-555-0166",
            "date_of_birth": date(1974, 8, 19),
            "nationality": "Swedish",
            "country": "Sweden",
            "address": "Kungsgatan 44, 111 35 Stockholm",
            "risk_level": "low",
            "pep_status": False,
            "status": "active",
            "onboarding_date": _d(1100),
        },
        "accounts": [
            {
                "id": uuid.UUID("00000003-0010-0001-0000-000000000010"),
                "account_number": "SCN-010-INV-0001",
                "account_type": "investment",
                "currency": "USD",
                "balance": Decimal("285000.00"),
                "status": "active",
                "opened_date": _d(1100),
            },
            {
                "id": uuid.UUID("00000003-0010-0002-0000-000000000010"),
                "account_number": "SCN-010-SAV-0001",
                "account_type": "savings",
                "currency": "SEK",
                "balance": Decimal("120000.00"),
                "status": "active",
                "opened_date": _d(1090),
            },
        ],
        "transactions": [
            {
                "account_key": "SCN-010-INV-0001",
                "transaction_type": "deposit",
                "amount": Decimal("25000.00"),
                "description": "Quarterly portfolio contribution",
                "counterparty": "Svensson Personal Savings",
                "reference": "CONTRIB-Q1-2024",
                "status": "completed",
                "transaction_date": _dt(90),
            },
            {
                "account_key": "SCN-010-INV-0001",
                "transaction_type": "deposit",
                "amount": Decimal("25000.00"),
                "description": "Quarterly portfolio contribution",
                "counterparty": "Svensson Personal Savings",
                "reference": "CONTRIB-Q4-2023",
                "status": "completed",
                "transaction_date": _dt(180),
            },
        ],
        "kyc_records": [
            {
                "id": uuid.UUID("00000004-0010-0001-0000-000000000010"),
                "verification_level": "enhanced",
                "status": "approved",
                "reviewer": "kyc.senior@bank.com",
                "notes": (
                    "Enhanced KYC for investment account. Source of wealth: "
                    "senior engineering executive. All documents verified. "
                    "Low-risk profile confirmed."
                ),
                "review_date": _dt(90),
                "expiry_date": _d(-275),  # ~9 months until next review
                "documents": [
                    {
                        "document_type": "passport",
                        "document_number": "SE-P-AB9876543",
                        "issuing_country": "Sweden",
                        "expiry_date": _d(-1000),
                        "verified": True,
                    },
                    {
                        "document_type": "utility_bill",
                        "document_number": None,
                        "issuing_country": "Sweden",
                        "expiry_date": None,
                        "verified": True,
                    },
                ],
            }
        ],
        "risk_score": {
            "overall_score": 8,
            "kyc_score": 5,
            "transaction_score": 5,
            "geography_score": 8,
            "pep_score": 0,
            "last_assessment_date": _dt(90),
            "next_review_date": _d(-275),
        },
        "compliance_alerts": [],
        "loans": [],
        # Diversified holdings across 6 sectors
        "holdings": [
            {
                "account_key": "SCN-010-INV-0001",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "quantity": Decimal("120.000000"),
                "average_cost": Decimal("155.20"),
                "current_price": Decimal("189.30"),
                "market_value": Decimal("22716.00"),
                "sector": "Technology",
            },
            {
                "account_key": "SCN-010-INV-0001",
                "ticker": "JPM",
                "name": "JPMorgan Chase & Co.",
                "quantity": Decimal("80.000000"),
                "average_cost": Decimal("148.50"),
                "current_price": Decimal("198.40"),
                "market_value": Decimal("15872.00"),
                "sector": "Financials",
            },
            {
                "account_key": "SCN-010-INV-0001",
                "ticker": "JNJ",
                "name": "Johnson & Johnson",
                "quantity": Decimal("100.000000"),
                "average_cost": Decimal("165.00"),
                "current_price": Decimal("152.30"),
                "market_value": Decimal("15230.00"),
                "sector": "Healthcare",
            },
            {
                "account_key": "SCN-010-INV-0001",
                "ticker": "NEE",
                "name": "NextEra Energy Inc.",
                "quantity": Decimal("150.000000"),
                "average_cost": Decimal("72.80"),
                "current_price": Decimal("62.40"),
                "market_value": Decimal("9360.00"),
                "sector": "Utilities",
            },
            {
                "account_key": "SCN-010-INV-0001",
                "ticker": "PLD",
                "name": "Prologis Inc.",
                "quantity": Decimal("90.000000"),
                "average_cost": Decimal("118.20"),
                "current_price": Decimal("124.70"),
                "market_value": Decimal("11223.00"),
                "sector": "Real Estate",
            },
            {
                "account_key": "SCN-010-INV-0001",
                "ticker": "XOM",
                "name": "Exxon Mobil Corporation",
                "quantity": Decimal("110.000000"),
                "average_cost": Decimal("98.40"),
                "current_price": Decimal("112.80"),
                "market_value": Decimal("12408.00"),
                "sector": "Energy",
            },
        ],
        "watchlist_entries": [],
    },
]
