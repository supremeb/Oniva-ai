"""Synthetic Bank API — Test fixture for Oniva E2E QA."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import engine, Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup, auto-seed if empty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Auto-seed on first boot if tables are empty
    from app.seed import auto_seed
    await auto_seed()

    yield


app = FastAPI(
    title="Synthetic Bank API",
    description="Synthetic banking data for Oniva E2E QA testing",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Route imports — added as they are implemented
from app.routes import customers, accounts, transactions, loans, kyc  # noqa: E402
from app.routes import compliance, risk, watchlist, incidents, holdings  # noqa: E402
from app.routes import scenarios  # noqa: E402

app.include_router(customers.router, prefix="/customers", tags=["Customers"])
app.include_router(accounts.router, prefix="/accounts", tags=["Accounts"])
app.include_router(transactions.router, prefix="/transactions", tags=["Transactions"])
app.include_router(loans.router, prefix="/loans", tags=["Loans"])
app.include_router(kyc.router, prefix="/kyc", tags=["KYC"])
app.include_router(compliance.router, prefix="/compliance-alerts", tags=["Compliance"])
app.include_router(risk.router, prefix="/risk-scores", tags=["Risk"])
app.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])
app.include_router(incidents.router, prefix="/incidents", tags=["Incidents"])
app.include_router(holdings.router, prefix="/holdings", tags=["Holdings"])
app.include_router(scenarios.router, prefix="/scenarios", tags=["Scenarios"])
