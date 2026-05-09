"""
Seed script for the synthetic bank database.

Entry points
------------
auto_seed()   — called at app startup; skips if customers table is non-empty.
reset_seed()  — deletes curated scenario records by stable UUID, truncates random
                bulk data, then re-seeds from scratch.

CLI
---
python -m app.seed           # auto (skips if already seeded)
python -m app.seed --reset   # full reset + reseed
"""

from __future__ import annotations

import asyncio
import random
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from faker import Faker
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import (
    Account,
    BeneficialOwner,
    ComplianceAlert,
    Customer,
    Document,
    Holding,
    Incident,
    KYCRecord,
    Loan,
    LoanPayment,
    RiskScore,
    TestScenario,
    Transaction,
    WatchlistEntry,
)
from app.scenarios import (
    SCENARIO_CLEAN_PASS_UUID,
    SCENARIO_CLEAN_PORTFOLIO_UUID,
    SCENARIO_EXPIRED_REVIEW_UUID,
    SCENARIO_HIGH_RISK_TXN_UUID,
    SCENARIO_INCOMPLETE_KYC_UUID,
    SCENARIO_LOAN_DEFAULT_UUID,
    SCENARIO_MULTI_ACCOUNT_UUID,
    SCENARIO_PEP_MATCH_UUID,
    SCENARIO_SANCTIONS_HIT_UUID,
    SCENARIO_SANCTIONS_WATCHLIST_UUID,
    SCENARIO_STRUCTURING_UUID,
    SCENARIOS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FAKER_SEED = 42
RANDOM_SEED = 42

TARGET_CUSTOMERS = 990        # random customers (+ 10 curated = 1000 total)
AVG_ACCOUNTS_PER_CUSTOMER = 5
AVG_TRANSACTIONS_PER_ACCOUNT = 5
RANDOM_LOANS = 500
RANDOM_KYC_CUSTOMERS = 200    # subset of random customers that get KYC records
RANDOM_ALERTS = 100
RANDOM_WATCHLIST = 50
RANDOM_INCIDENTS = 30

# All stable customer UUIDs from curated scenarios
CURATED_CUSTOMER_UUIDS: list[uuid.UUID] = [
    SCENARIO_CLEAN_PASS_UUID,
    SCENARIO_SANCTIONS_HIT_UUID,
    SCENARIO_HIGH_RISK_TXN_UUID,
    SCENARIO_INCOMPLETE_KYC_UUID,
    SCENARIO_PEP_MATCH_UUID,
    SCENARIO_STRUCTURING_UUID,
    SCENARIO_EXPIRED_REVIEW_UUID,
    SCENARIO_MULTI_ACCOUNT_UUID,
    SCENARIO_LOAN_DEFAULT_UUID,
    SCENARIO_CLEAN_PORTFOLIO_UUID,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(days_ago: int = 0) -> date:
    return date.today() - timedelta(days=days_ago)


def _dt(days_ago: int = 0) -> datetime:
    return datetime.utcnow() - timedelta(days=days_ago)


def _rand_date(fake: Faker, start_days_ago: int, end_days_ago: int) -> date:
    """Return a random date between [end_days_ago, start_days_ago] ago."""
    days = random.randint(end_days_ago, start_days_ago)
    return _d(days)


def _rand_dt(fake: Faker, start_days_ago: int, end_days_ago: int) -> datetime:
    days = random.randint(end_days_ago, start_days_ago)
    return _dt(days)


def _rand_amount(lo: float, hi: float) -> Decimal:
    return Decimal(str(round(random.uniform(lo, hi), 2)))


def _account_number(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq:08d}"


# ---------------------------------------------------------------------------
# Phase 1: Curated scenarios
# ---------------------------------------------------------------------------

async def _seed_scenarios(db: AsyncSession) -> None:
    """Insert all 10 curated test scenarios with their full data graphs."""
    print("  [Phase 1] Seeding curated scenarios...")

    for scenario in SCENARIOS:
        cust_data = scenario["customer"]

        # --- Customer ---
        customer = Customer(
            id=cust_data["id"],
            first_name=cust_data["first_name"],
            last_name=cust_data["last_name"],
            entity_type=cust_data["entity_type"],
            email=cust_data["email"],
            phone=cust_data.get("phone"),
            date_of_birth=cust_data.get("date_of_birth"),
            nationality=cust_data.get("nationality"),
            country=cust_data["country"],
            address=cust_data.get("address"),
            risk_level=cust_data["risk_level"],
            pep_status=cust_data["pep_status"],
            status=cust_data["status"],
            onboarding_date=cust_data["onboarding_date"],
        )
        db.add(customer)
        await db.flush()

        # --- Accounts (keyed by account_number for transaction lookup) ---
        account_map: dict[str, uuid.UUID] = {}
        for acc_data in scenario.get("accounts", []):
            account = Account(
                id=acc_data["id"],
                customer_id=customer.id,
                account_number=acc_data["account_number"],
                account_type=acc_data["account_type"],
                currency=acc_data["currency"],
                balance=acc_data["balance"],
                status=acc_data["status"],
                opened_date=acc_data["opened_date"],
            )
            db.add(account)
            account_map[acc_data["account_number"]] = acc_data["id"]
        await db.flush()

        # --- Transactions ---
        for txn_data in scenario.get("transactions", []):
            acct_id = account_map[txn_data["account_key"]]
            transaction = Transaction(
                account_id=acct_id,
                transaction_type=txn_data["transaction_type"],
                amount=txn_data["amount"],
                currency="USD",
                description=txn_data.get("description"),
                counterparty=txn_data.get("counterparty"),
                reference=txn_data.get("reference"),
                status=txn_data.get("status", "completed"),
                transaction_date=txn_data["transaction_date"],
            )
            db.add(transaction)

        # --- KYC Records and Documents ---
        for kyc_data in scenario.get("kyc_records", []):
            kyc = KYCRecord(
                id=kyc_data["id"],
                customer_id=customer.id,
                verification_level=kyc_data["verification_level"],
                status=kyc_data["status"],
                reviewer=kyc_data.get("reviewer"),
                notes=kyc_data.get("notes"),
                review_date=kyc_data.get("review_date"),
                expiry_date=kyc_data.get("expiry_date"),
            )
            db.add(kyc)
            await db.flush()

            for doc_data in kyc_data.get("documents", []):
                doc = Document(
                    kyc_record_id=kyc.id,
                    document_type=doc_data["document_type"],
                    document_number=doc_data.get("document_number"),
                    issuing_country=doc_data.get("issuing_country"),
                    expiry_date=doc_data.get("expiry_date"),
                    verified=doc_data.get("verified", False),
                )
                db.add(doc)

        # --- Risk Score ---
        rs_data = scenario.get("risk_score")
        if rs_data:
            risk_score = RiskScore(
                customer_id=customer.id,
                overall_score=rs_data["overall_score"],
                kyc_score=rs_data["kyc_score"],
                transaction_score=rs_data["transaction_score"],
                geography_score=rs_data["geography_score"],
                pep_score=rs_data["pep_score"],
                last_assessment_date=rs_data["last_assessment_date"],
                next_review_date=rs_data["next_review_date"],
            )
            db.add(risk_score)

        # --- Compliance Alerts ---
        for alert_data in scenario.get("compliance_alerts", []):
            alert = ComplianceAlert(
                customer_id=customer.id,
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                status=alert_data["status"],
                title=alert_data["title"],
                description=alert_data.get("description"),
                triggered_date=alert_data["triggered_date"],
            )
            db.add(alert)

        # --- Loans and LoanPayments ---
        for loan_data in scenario.get("loans", []):
            loan = Loan(
                id=loan_data["id"],
                customer_id=customer.id,
                loan_type=loan_data["loan_type"],
                principal=loan_data["principal"],
                interest_rate=loan_data["interest_rate"],
                term_months=loan_data["term_months"],
                monthly_payment=loan_data["monthly_payment"],
                outstanding_balance=loan_data["outstanding_balance"],
                status=loan_data["status"],
                origination_date=loan_data["origination_date"],
                maturity_date=loan_data["maturity_date"],
            )
            db.add(loan)
            await db.flush()

            for pmt_data in loan_data.get("payments", []):
                payment = LoanPayment(
                    loan_id=loan.id,
                    amount=pmt_data["amount"],
                    payment_date=pmt_data["payment_date"],
                    status=pmt_data["status"],
                )
                db.add(payment)

        # --- Holdings ---
        for holding_data in scenario.get("holdings", []):
            acct_id = account_map[holding_data["account_key"]]
            holding = Holding(
                account_id=acct_id,
                ticker=holding_data["ticker"],
                name=holding_data["name"],
                quantity=holding_data["quantity"],
                average_cost=holding_data["average_cost"],
                current_price=holding_data["current_price"],
                market_value=holding_data["market_value"],
                sector=holding_data.get("sector"),
            )
            db.add(holding)

        # --- Watchlist Entries (scenario-specific) ---
        for wl_data in scenario.get("watchlist_entries", []):
            entry = WatchlistEntry(
                id=wl_data["id"],
                list_name=wl_data["list_name"],
                entity_name=wl_data["entity_name"],
                entity_type=wl_data["entity_type"],
                country=wl_data.get("country"),
                aliases=wl_data.get("aliases"),
                reason=wl_data.get("reason"),
                listed_date=wl_data.get("listed_date"),
            )
            db.add(entry)

        # --- TestScenario metadata row ---
        ts = TestScenario(
            id=scenario["row_uuid"],
            scenario_key=scenario["scenario_key"],
            name=scenario["name"],
            description=scenario["description"],
            customer_id=customer.id,
            expected_outcome=scenario["expected_outcome"],
            workflow_target=scenario["workflow_target"],
        )
        db.add(ts)

        await db.flush()
        print(f"    + {scenario['scenario_key']}")

    await db.flush()
    print("  [Phase 1] Done.")


# ---------------------------------------------------------------------------
# Phase 2: Random bulk data
# ---------------------------------------------------------------------------

async def _seed_bulk(db: AsyncSession) -> None:
    """Generate ~990 random customers plus associated records."""
    print("  [Phase 2] Seeding bulk random data (seed=42)...")

    fake = Faker()
    Faker.seed(FAKER_SEED)
    random.seed(RANDOM_SEED)

    # ------------------------------------------------------------------
    # 2a. Customers
    # ------------------------------------------------------------------
    print(f"    Generating {TARGET_CUSTOMERS} random customers...")

    entity_types = ["individual", "corporate"]
    risk_levels = ["low", "low", "low", "medium", "medium", "high", "critical"]
    statuses = ["active", "active", "active", "active", "inactive", "frozen"]
    nationalities = [
        "US", "UK", "Canadian", "German", "French", "Japanese", "Australian",
        "Brazilian", "Indian", "Chinese", "South Korean", "Mexican", "Italian",
        "Spanish", "Dutch", "Swedish", "Norwegian", "Swiss", "Singapore", "UAE",
    ]
    countries = [
        "United States", "United Kingdom", "Canada", "Germany", "France",
        "Japan", "Australia", "Brazil", "India", "China", "South Korea",
        "Mexico", "Italy", "Spain", "Netherlands", "Sweden", "Switzerland",
    ]

    customers: list[Customer] = []
    for i in range(TARGET_CUSTOMERS):
        entity_type = random.choice(entity_types)
        is_corp = entity_type == "corporate"
        dob = None if is_corp else fake.date_of_birth(minimum_age=18, maximum_age=80)
        c = Customer(
            first_name=fake.company() if is_corp else fake.first_name(),
            last_name="" if is_corp else fake.last_name(),
            entity_type=entity_type,
            email=fake.email(),
            phone=fake.phone_number()[:50],
            date_of_birth=dob,
            nationality=random.choice(nationalities),
            country=random.choice(countries),
            address=fake.address().replace("\n", ", ")[:255],
            risk_level=random.choice(risk_levels),
            pep_status=random.random() < 0.03,  # 3% PEP rate
            status=random.choice(statuses),
            onboarding_date=_rand_date(fake, 730, 30),
        )
        customers.append(c)
        db.add(c)

        # Flush every 100 to avoid memory bloat
        if (i + 1) % 100 == 0:
            await db.flush()

    await db.flush()
    print(f"    Customers flushed. Generating accounts...")

    # ------------------------------------------------------------------
    # 2b. Accounts (~5 per customer)
    # ------------------------------------------------------------------
    account_types = ["checking", "savings", "investment", "loan"]
    account_weights = [0.45, 0.35, 0.15, 0.05]
    account_statuses = ["active", "active", "active", "active", "dormant", "frozen"]
    currencies = ["USD", "USD", "USD", "USD", "EUR", "GBP", "CAD", "JPY"]

    all_accounts: list[Account] = []
    acct_seq = 100000  # start seq so it won't collide with scenario numbers

    for customer in customers:
        n_accounts = random.choices(
            [1, 2, 3, 4, 5, 6, 7, 8],
            weights=[5, 10, 20, 25, 20, 10, 7, 3],
        )[0]
        for _ in range(n_accounts):
            acct_type = random.choices(account_types, weights=account_weights)[0]
            currency = random.choice(currencies)
            balance = _rand_amount(0, 150000)
            opened = _rand_date(fake, 700, 10)
            acc = Account(
                customer_id=customer.id,
                account_number=_account_number("RND", acct_seq),
                account_type=acct_type,
                currency=currency,
                balance=balance,
                status=random.choice(account_statuses),
                opened_date=opened,
            )
            all_accounts.append(acc)
            db.add(acc)
            acct_seq += 1

    await db.flush()
    print(f"    {len(all_accounts)} accounts flushed. Generating transactions...")

    # ------------------------------------------------------------------
    # 2c. Transactions (~5 per account)
    # ------------------------------------------------------------------
    txn_types = ["deposit", "withdrawal", "transfer", "payment", "fee"]
    txn_weights = [0.30, 0.20, 0.20, 0.25, 0.05]
    txn_statuses = ["completed", "completed", "completed", "completed", "pending", "failed"]

    txn_count = 0
    for account in all_accounts:
        n_txns = random.choices(
            [1, 2, 3, 4, 5, 6, 7, 8, 10],
            weights=[5, 10, 15, 20, 20, 15, 8, 5, 2],
        )[0]
        for _ in range(n_txns):
            txn = Transaction(
                account_id=account.id,
                transaction_type=random.choices(txn_types, weights=txn_weights)[0],
                amount=_rand_amount(1, 50000),
                currency=account.currency,
                description=fake.sentence(nb_words=5),
                counterparty=fake.company() if random.random() > 0.3 else None,
                reference=fake.bothify("REF-??-######") if random.random() > 0.4 else None,
                status=random.choice(txn_statuses),
                transaction_date=_rand_dt(fake, 700, 0),
            )
            db.add(txn)
            txn_count += 1

        if txn_count % 1000 == 0:
            await db.flush()

    await db.flush()
    print(f"    ~{txn_count} transactions flushed. Generating loans...")

    # ------------------------------------------------------------------
    # 2d. Loans (~500 total)
    # ------------------------------------------------------------------
    loan_customers = random.sample(customers, min(RANDOM_LOANS, len(customers)))
    loan_types = ["mortgage", "personal", "auto", "business"]
    loan_statuses = ["active", "active", "active", "paid_off", "delinquent", "defaulted"]

    for lc in loan_customers:
        loan_type = random.choice(loan_types)
        if loan_type == "mortgage":
            principal = _rand_amount(100000, 800000)
            term_months = 360
            rate = Decimal(str(round(random.uniform(0.03, 0.075), 4)))
        elif loan_type == "business":
            principal = _rand_amount(50000, 500000)
            term_months = 60
            rate = Decimal(str(round(random.uniform(0.05, 0.12), 4)))
        elif loan_type == "auto":
            principal = _rand_amount(10000, 60000)
            term_months = 60
            rate = Decimal(str(round(random.uniform(0.04, 0.10), 4)))
        else:
            principal = _rand_amount(2000, 40000)
            term_months = 36
            rate = Decimal(str(round(random.uniform(0.07, 0.24), 4)))

        monthly_payment = _rand_amount(
            float(principal) * float(rate) / 12,
            float(principal) * float(rate) / 12 * 1.5 + 100,
        )
        origination_date = _rand_date(fake, 700, 90)
        maturity_date = origination_date + timedelta(days=term_months * 30)
        outstanding_balance = _rand_amount(
            float(principal) * 0.1, float(principal) * 0.95
        )
        status = random.choice(loan_statuses)

        loan = Loan(
            customer_id=lc.id,
            loan_type=loan_type,
            principal=principal,
            interest_rate=rate,
            term_months=term_months,
            monthly_payment=monthly_payment,
            outstanding_balance=outstanding_balance,
            status=status,
            origination_date=origination_date,
            maturity_date=maturity_date,
        )
        db.add(loan)
        await db.flush()

        # Generate 3-12 payment records per loan
        n_payments = random.randint(3, 12)
        for p_idx in range(n_payments):
            pmt_date = origination_date + timedelta(days=30 * (p_idx + 1))
            pmt_status: str
            if status == "delinquent" and p_idx >= n_payments - 2:
                pmt_status = "missed"
            elif status == "defaulted" and p_idx >= n_payments - 3:
                pmt_status = random.choice(["missed", "late"])
            else:
                pmt_status = random.choices(
                    ["completed", "late"], weights=[0.92, 0.08]
                )[0]
            payment = LoanPayment(
                loan_id=loan.id,
                amount=monthly_payment,
                payment_date=pmt_date,
                status=pmt_status,
            )
            db.add(payment)

    await db.flush()
    print(f"    Loans flushed. Generating KYC records...")

    # ------------------------------------------------------------------
    # 2e. KYC Records (~200 customers)
    # ------------------------------------------------------------------
    kyc_customers = random.sample(customers, min(RANDOM_KYC_CUSTOMERS, len(customers)))
    verification_levels = ["basic", "standard", "enhanced"]
    kyc_statuses = ["pending", "approved", "approved", "approved", "rejected", "expired"]
    doc_types = ["passport", "drivers_license", "utility_bill", "articles_of_incorporation"]

    for kc in kyc_customers:
        v_level = random.choice(verification_levels)
        k_status = random.choice(kyc_statuses)
        review_date = _rand_dt(fake, 500, 30) if k_status != "pending" else None
        kyc = KYCRecord(
            customer_id=kc.id,
            verification_level=v_level,
            status=k_status,
            reviewer="kyc.system@bank.com" if k_status != "pending" else None,
            notes=fake.sentence(nb_words=10) if random.random() > 0.5 else None,
            review_date=review_date,
            expiry_date=_rand_date(fake, 0, -400) if k_status == "approved" else None,
        )
        db.add(kyc)
        await db.flush()

        # 1-3 documents per KYC
        n_docs = random.randint(1, 3)
        for _ in range(n_docs):
            doc = Document(
                kyc_record_id=kyc.id,
                document_type=random.choice(doc_types),
                document_number=fake.bothify("??-######-??") if random.random() > 0.3 else None,
                issuing_country=random.choice(countries),
                expiry_date=_rand_date(fake, -365, -1825) if random.random() > 0.3 else None,
                verified=k_status == "approved",
            )
            db.add(doc)

    await db.flush()
    print(f"    KYC records flushed. Generating compliance alerts...")

    # ------------------------------------------------------------------
    # 2f. Compliance Alerts (~100)
    # ------------------------------------------------------------------
    alert_types = [
        "suspicious_activity", "sanctions_match", "pep_match",
        "unusual_transaction", "expired_review",
    ]
    alert_severities = ["low", "medium", "high", "critical"]
    alert_statuses = ["open", "investigating", "resolved", "dismissed"]

    alert_customers = random.sample(customers, min(RANDOM_ALERTS, len(customers)))
    for ac in alert_customers:
        alert = ComplianceAlert(
            customer_id=ac.id,
            alert_type=random.choice(alert_types),
            severity=random.choice(alert_severities),
            status=random.choice(alert_statuses),
            title=fake.sentence(nb_words=7),
            description=fake.paragraph(nb_sentences=2) if random.random() > 0.4 else None,
            triggered_date=_rand_dt(fake, 365, 0),
        )
        db.add(alert)

    await db.flush()
    print(f"    Compliance alerts flushed. Generating watchlist entries...")

    # ------------------------------------------------------------------
    # 2g. Watchlist Entries (~50)
    # ------------------------------------------------------------------
    list_names = ["OFAC_SDN", "EU_SANCTIONS", "UN_SANCTIONS", "PEP_LIST"]
    entity_types_wl = ["individual", "organization"]

    for _ in range(RANDOM_WATCHLIST):
        entry = WatchlistEntry(
            list_name=random.choice(list_names),
            entity_name=fake.name() if random.random() > 0.4 else fake.company(),
            entity_type=random.choice(entity_types_wl),
            country=random.choice(countries),
            aliases=", ".join(fake.name() for _ in range(random.randint(0, 3))) or None,
            reason=fake.sentence(nb_words=10) if random.random() > 0.3 else None,
            listed_date=_rand_date(fake, 2000, 180),
        )
        db.add(entry)

    await db.flush()
    print(f"    Watchlist entries flushed. Generating incidents...")

    # ------------------------------------------------------------------
    # 2h. Incidents (~30)
    # ------------------------------------------------------------------
    incident_types = [
        "fraud_attempt", "system_breach", "data_leak",
        "unauthorized_access", "anomaly",
    ]
    incident_severities = ["low", "medium", "high", "critical"]
    incident_statuses = ["open", "investigating", "resolved", "escalated"]

    incident_customers = random.sample(customers, min(RANDOM_INCIDENTS, len(customers)))
    for ic in incident_customers:
        incident = Incident(
            customer_id=ic.id,
            incident_type=random.choice(incident_types),
            severity=random.choice(incident_severities),
            status=random.choice(incident_statuses),
            title=fake.sentence(nb_words=6),
            description=fake.paragraph(nb_sentences=2) if random.random() > 0.5 else None,
            reported_by="security@bank.com",
            reported_date=_rand_dt(fake, 365, 0),
        )
        db.add(incident)

    await db.flush()
    print(f"    Incidents flushed. Generating investment holdings...")

    # ------------------------------------------------------------------
    # 2i. Holdings for investment accounts
    # ------------------------------------------------------------------
    investment_accounts = [a for a in all_accounts if a.account_type == "investment"]
    tickers = [
        ("AAPL", "Apple Inc.", "Technology"),
        ("MSFT", "Microsoft Corporation", "Technology"),
        ("GOOGL", "Alphabet Inc.", "Technology"),
        ("AMZN", "Amazon.com Inc.", "Consumer Discretionary"),
        ("TSLA", "Tesla Inc.", "Consumer Discretionary"),
        ("JPM", "JPMorgan Chase & Co.", "Financials"),
        ("BAC", "Bank of America Corp.", "Financials"),
        ("JNJ", "Johnson & Johnson", "Healthcare"),
        ("UNH", "UnitedHealth Group Inc.", "Healthcare"),
        ("XOM", "Exxon Mobil Corporation", "Energy"),
        ("CVX", "Chevron Corporation", "Energy"),
        ("NEE", "NextEra Energy Inc.", "Utilities"),
        ("PLD", "Prologis Inc.", "Real Estate"),
        ("SPY", "SPDR S&P 500 ETF Trust", "ETF"),
        ("QQQ", "Invesco QQQ Trust", "ETF"),
        ("GLD", "SPDR Gold Shares", "Commodities"),
        ("BND", "Vanguard Total Bond Market ETF", "Fixed Income"),
        ("VEA", "Vanguard FTSE Developed Markets ETF", "International"),
    ]

    holding_count = 0
    for inv_acc in investment_accounts:
        n_holdings = random.randint(2, 8)
        chosen_tickers = random.sample(tickers, min(n_holdings, len(tickers)))
        for ticker, name, sector in chosen_tickers:
            avg_cost = _rand_amount(20, 500)
            quantity = Decimal(str(round(random.uniform(1, 500), 6)))
            current_price = avg_cost * Decimal(str(round(random.uniform(0.7, 1.5), 4)))
            market_value = (quantity * current_price).quantize(Decimal("0.01"))
            holding = Holding(
                account_id=inv_acc.id,
                ticker=ticker,
                name=name,
                quantity=quantity,
                average_cost=avg_cost,
                current_price=current_price.quantize(Decimal("0.01")),
                market_value=market_value,
                sector=sector,
            )
            db.add(holding)
            holding_count += 1

        if holding_count % 500 == 0:
            await db.flush()

    await db.flush()
    print(f"    {holding_count} holdings flushed.")

    # ------------------------------------------------------------------
    # 2j. Beneficial Owners for a subset of corporate customers
    # ------------------------------------------------------------------
    corporate_customers = [c for c in customers if c.entity_type == "corporate"]
    bo_sample = random.sample(corporate_customers, min(80, len(corporate_customers)))
    for bc in bo_sample:
        n_owners = random.randint(1, 4)
        remaining = Decimal("100.00")
        for idx in range(n_owners):
            if idx == n_owners - 1:
                pct = remaining
            else:
                pct = Decimal(str(round(random.uniform(10, float(remaining) - 10), 2)))
                remaining -= pct
            bo = BeneficialOwner(
                customer_id=bc.id,
                name=fake.name(),
                ownership_percentage=pct,
                nationality=random.choice(nationalities),
                pep_status=random.random() < 0.05,
                verified=random.random() > 0.3,
            )
            db.add(bo)

    await db.flush()
    print("    Beneficial owners flushed.")
    print("  [Phase 2] Done.")


# ---------------------------------------------------------------------------
# Deletion helpers for reset
# ---------------------------------------------------------------------------

async def _delete_curated_data(db: AsyncSession) -> None:
    """
    Delete all curated scenario records by their stable UUIDs.

    Relies on CASCADE deletes defined on the models so child records
    (accounts, transactions, kyc_records, documents, etc.) are removed
    automatically when the customer row is deleted.
    """
    print("  Deleting curated scenario customers (cascade)...")
    for cuid in CURATED_CUSTOMER_UUIDS:
        await db.execute(delete(Customer).where(Customer.id == cuid))

    # Delete the matching watchlist entry for sanctions scenario
    await db.execute(
        delete(WatchlistEntry).where(WatchlistEntry.id == SCENARIO_SANCTIONS_WATCHLIST_UUID)
    )

    # Delete TestScenario metadata rows (no FK cascade to customer)
    await db.execute(delete(TestScenario))

    await db.flush()
    print("  Curated records deleted.")


async def _truncate_bulk_data(db: AsyncSession) -> None:
    """
    Remove all non-curated (random) data.

    We do this by deleting customers whose IDs are NOT in the curated set,
    which cascades to all child tables. Then clean up any orphaned watchlist
    entries and incidents that aren't customer-linked.
    """
    print("  Truncating bulk random data...")

    # All customers not in curated set → cascade removes their children
    await db.execute(
        delete(Customer).where(Customer.id.not_in(CURATED_CUSTOMER_UUIDS))
    )

    # Incidents with null customer_id (no cascade target)
    await db.execute(
        delete(Incident).where(Incident.customer_id.is_(None))
    )

    # Watchlist entries that aren't the curated sanctions entry
    await db.execute(
        delete(WatchlistEntry).where(
            WatchlistEntry.id != SCENARIO_SANCTIONS_WATCHLIST_UUID
        )
    )

    await db.flush()
    print("  Bulk data truncated.")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def auto_seed() -> None:
    """
    Called at application startup.

    Checks if the customers table is empty. If yes, runs the full seed.
    If data already exists, skips silently.
    """
    async with async_session() as db:
        result = await db.execute(select(func.count()).select_from(Customer))
        count: int = result.scalar_one()

        if count > 0:
            print(f"[seed] Database already contains {count} customers — skipping seed.")
            return

        print("[seed] Database empty — running initial seed...")
        try:
            await _seed_scenarios(db)
            await _seed_bulk(db)
            await db.commit()
            print("[seed] Seed complete.")
        except Exception:
            await db.rollback()
            raise


async def reset_seed() -> None:
    """
    Full reset: delete all existing data (curated + bulk), then re-seed.
    """
    print("[seed] Starting full reset...")
    async with async_session() as db:
        try:
            # Delete curated data first (watchlist + customers + test_scenarios)
            await _delete_curated_data(db)

            # Delete random bulk data
            await _truncate_bulk_data(db)

            await db.commit()
            print("[seed] All data deleted. Re-seeding...")
        except Exception:
            await db.rollback()
            raise

    async with async_session() as db:
        try:
            await _seed_scenarios(db)
            await _seed_bulk(db)
            await db.commit()
            print("[seed] Reset complete.")
        except Exception:
            await db.rollback()
            raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--reset" in sys.argv:
        asyncio.run(reset_seed())
    else:
        asyncio.run(auto_seed())
