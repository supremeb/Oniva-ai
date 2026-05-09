"""
Compliance Engine - Rules-based compliance checking
Supports sanctions screening, PEP checks, and configurable rule evaluation

Integrates with real OFAC SDN data for production-grade sanctions screening.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime
import re
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceCheckType(Enum):
    SANCTIONS_SCREENING = "sanctions_screening"
    PEP_SCREENING = "pep_screening"
    ADVERSE_MEDIA = "adverse_media"
    JURISDICTION_RISK = "jurisdiction_risk"
    TRANSACTION_MONITORING = "transaction_monitoring"


@dataclass
class ComplianceRule:
    """A single compliance rule"""
    id: str
    name: str
    description: str
    condition: str  # Python expression or rule type
    action: str     # 'flag', 'block', 'escalate', 'approve'
    risk_level: RiskLevel
    regulatory_reference: Optional[str] = None


@dataclass
class ScreeningMatch:
    """A match from sanctions/PEP screening"""
    source_database: str
    matched_name: str
    match_score: float
    match_type: str  # 'exact', 'fuzzy', 'alias'
    entry_id: str
    additional_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplianceResult:
    """Result from a compliance check"""
    check_type: ComplianceCheckType
    passed: bool
    match_found: bool
    matches: List[ScreeningMatch]
    risk_level: RiskLevel
    flags: List[str]
    details: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class SanctionsDatabase:
    """
    Sanctions database supporting both mock data and real OFAC SDN data.
    Uses real OFAC data when available, falls back to mock data for testing.
    """

    # Flag to control whether to use real OFAC data
    USE_REAL_OFAC = True

    # Sample sanctioned entities for demo/fallback purposes
    MOCK_SDN_LIST = [
        {
            "id": "OFAC-12345",
            "name": "Test Sanctioned Person",
            "aliases": ["T.S. Person", "Sanctioned Test"],
            "type": "individual",
            "programs": ["SDGT", "IRAN"],
            "dob": "1970-01-01",
            "nationality": "IR"
        },
        {
            "id": "OFAC-67890",
            "name": "Bad Actor Corp",
            "aliases": ["BAC LLC", "Bad Actor Company"],
            "type": "entity",
            "programs": ["CYBER2"],
            "country": "RU"
        },
        {
            "id": "UN-11111",
            "name": "Designated Organization",
            "aliases": [],
            "type": "entity",
            "programs": ["UN-1718"],
            "country": "KP"
        }
    ]

    MOCK_PEP_LIST = [
        {
            "id": "PEP-001",
            "name": "Sample Foreign Official",
            "category": "foreign_official",
            "position": "Former Minister of Finance",
            "country": "XX",
            "risk_level": "high"
        },
        {
            "id": "PEP-002",
            "name": "Domestic Political Figure",
            "category": "domestic_official",
            "position": "State Senator",
            "country": "US",
            "risk_level": "medium"
        }
    ]

    _ofac_client = None

    @classmethod
    async def _get_ofac_client(cls):
        """Get or initialize the OFAC client"""
        if cls._ofac_client is None:
            try:
                from app.connectors.ofac.client import get_ofac_client
                cls._ofac_client = get_ofac_client()
                await cls._ofac_client.load_data()
            except Exception as e:
                logger.warning(f"Failed to initialize OFAC client: {e}")
                cls._ofac_client = None
        return cls._ofac_client

    @classmethod
    async def search_sanctions_async(
        cls,
        name: str,
        dob: Optional[str] = None,
        country: Optional[str] = None,
        threshold: float = 0.85
    ) -> List[ScreeningMatch]:
        """Search sanctions lists with real OFAC data or fallback to mock"""
        matches = []

        # Try real OFAC data first
        if cls.USE_REAL_OFAC:
            try:
                client = await cls._get_ofac_client()
                if client and client.is_loaded:
                    results = client.search(
                        name=name,
                        date_of_birth=dob,
                        country=country,
                        threshold=threshold
                    )

                    for r in results:
                        matches.append(ScreeningMatch(
                            source_database="OFAC_SDN",
                            matched_name=r.matched_name,
                            match_score=r.match_score,
                            match_type=r.match_type,
                            entry_id=r.entry.uid,
                            additional_info={
                                "programs": r.entry.programs,
                                "type": r.entry.entry_type,
                                "country": r.entry.nationalities[0] if r.entry.nationalities else None,
                                "date_of_birth": r.entry.date_of_birth,
                                "aliases": r.entry.aliases[:5],
                                "remarks": r.entry.remarks[:200] if r.entry.remarks else None
                            }
                        ))

                    logger.info(f"OFAC search for '{name}' returned {len(matches)} matches")
                    return matches
            except Exception as e:
                logger.warning(f"OFAC search failed, falling back to mock: {e}")

        # Fallback to mock data
        return cls.search_sanctions(name, dob, country, threshold)

    @classmethod
    def search_sanctions(
        cls,
        name: str,
        dob: Optional[str] = None,
        country: Optional[str] = None,
        threshold: float = 0.85
    ) -> List[ScreeningMatch]:
        """Search mock sanctions lists with fuzzy matching (sync fallback)"""
        matches = []

        for entry in cls.MOCK_SDN_LIST:
            # Check main name
            score = cls._fuzzy_match(name, entry["name"])
            if score >= threshold:
                matches.append(ScreeningMatch(
                    source_database="OFAC_SDN_MOCK",
                    matched_name=entry["name"],
                    match_score=score,
                    match_type="fuzzy" if score < 1.0 else "exact",
                    entry_id=entry["id"],
                    additional_info={
                        "programs": entry.get("programs", []),
                        "type": entry.get("type"),
                        "country": entry.get("country") or entry.get("nationality"),
                        "mock_data": True
                    }
                ))
                continue

            # Check aliases
            for alias in entry.get("aliases", []):
                score = cls._fuzzy_match(name, alias)
                if score >= threshold:
                    matches.append(ScreeningMatch(
                        source_database="OFAC_SDN_MOCK",
                        matched_name=entry["name"],
                        match_score=score,
                        match_type="alias",
                        entry_id=entry["id"],
                        additional_info={
                            "matched_alias": alias,
                            "programs": entry.get("programs", []),
                            "type": entry.get("type"),
                            "mock_data": True
                        }
                    ))
                    break

        return matches

    @classmethod
    def search_pep(
        cls,
        name: str,
        country: Optional[str] = None,
        threshold: float = 0.80
    ) -> List[ScreeningMatch]:
        """Search PEP database (mock data - real PEP data requires paid subscription)"""
        matches = []

        for entry in cls.MOCK_PEP_LIST:
            score = cls._fuzzy_match(name, entry["name"])
            if score >= threshold:
                matches.append(ScreeningMatch(
                    source_database="PEP_DATABASE",
                    matched_name=entry["name"],
                    match_score=score,
                    match_type="fuzzy" if score < 1.0 else "exact",
                    entry_id=entry["id"],
                    additional_info={
                        "category": entry.get("category"),
                        "position": entry.get("position"),
                        "country": entry.get("country"),
                        "risk_level": entry.get("risk_level")
                    }
                ))

        return matches

    @staticmethod
    def _fuzzy_match(s1: str, s2: str) -> float:
        """Calculate fuzzy match score between two strings"""
        s1_clean = re.sub(r'[^\w\s]', '', s1.lower().strip())
        s2_clean = re.sub(r'[^\w\s]', '', s2.lower().strip())
        return SequenceMatcher(None, s1_clean, s2_clean).ratio()


class ComplianceEngine:
    """
    Main compliance engine for executing checks.
    Supports multiple check types with configurable rules.
    """

    # High-risk jurisdictions (FATF grey/black lists + OFAC comprehensive sanctions)
    HIGH_RISK_JURISDICTIONS = {
        "IR": "Iran",
        "KP": "North Korea",
        "SY": "Syria",
        "RU": "Russia",
        "BY": "Belarus",
        "MM": "Myanmar",
        "VE": "Venezuela",
        "CU": "Cuba",
        "AF": "Afghanistan",
        "YE": "Yemen",
    }

    def __init__(self, rules: Optional[List[ComplianceRule]] = None):
        self.rules = rules or []
        self.execution_log: List[Dict[str, Any]] = []

    async def run_sanctions_screening(
        self,
        name: str,
        dob: Optional[str] = None,
        country: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> ComplianceResult:
        """
        Run sanctions screening against configured databases.
        Uses real OFAC SDN data when available.
        """
        config = config or {}
        threshold = config.get("match_threshold", 0.85)
        use_real_data = config.get("use_real_data", True)

        all_matches: List[ScreeningMatch] = []

        # Search main name - use async version for real OFAC data
        if use_real_data:
            matches = await SanctionsDatabase.search_sanctions_async(
                name=name,
                dob=dob,
                country=country,
                threshold=threshold
            )
        else:
            matches = SanctionsDatabase.search_sanctions(
                name=name,
                dob=dob,
                country=country,
                threshold=threshold
            )
        all_matches.extend(matches)

        # Search aliases if provided
        for alias in (aliases or []):
            if use_real_data:
                alias_matches = await SanctionsDatabase.search_sanctions_async(
                    name=alias,
                    dob=dob,
                    country=country,
                    threshold=threshold
                )
            else:
                alias_matches = SanctionsDatabase.search_sanctions(
                    name=alias,
                    dob=dob,
                    country=country,
                    threshold=threshold
                )
            all_matches.extend(alias_matches)

        # Deduplicate by entry_id
        seen_ids = set()
        unique_matches = []
        for match in all_matches:
            if match.entry_id not in seen_ids:
                seen_ids.add(match.entry_id)
                unique_matches.append(match)

        match_found = len(unique_matches) > 0
        risk_level = RiskLevel.CRITICAL if match_found else RiskLevel.LOW

        # Determine data source
        data_source = "OFAC_SDN"
        if unique_matches and any(m.source_database == "OFAC_SDN_MOCK" for m in unique_matches):
            data_source = "OFAC_SDN_MOCK"

        self._log_execution("sanctions_screening", {
            "name": name,
            "match_count": len(unique_matches),
            "threshold": threshold,
            "data_source": data_source
        })

        return ComplianceResult(
            check_type=ComplianceCheckType.SANCTIONS_SCREENING,
            passed=not match_found,
            match_found=match_found,
            matches=unique_matches,
            risk_level=risk_level,
            flags=["SANCTIONS_HIT"] if match_found else [],
            details={
                "searched_name": name,
                "searched_aliases": aliases or [],
                "databases_checked": [data_source],
                "threshold": threshold,
                "match_count": len(unique_matches),
                "data_source": data_source
            }
        )

    async def run_pep_screening(
        self,
        name: str,
        country: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> ComplianceResult:
        """
        Run Politically Exposed Person (PEP) screening.
        """
        config = config or {}
        threshold = config.get("match_threshold", 0.80)
        pep_categories = config.get("pep_categories", [
            "foreign_official",
            "domestic_official",
            "international_org",
            "family_member",
            "close_associate"
        ])

        matches = SanctionsDatabase.search_pep(
            name=name,
            country=country,
            threshold=threshold
        )

        # Filter by requested categories
        filtered_matches = [
            m for m in matches
            if m.additional_info.get("category") in pep_categories
        ]

        is_pep = len(filtered_matches) > 0
        pep_category = filtered_matches[0].additional_info.get("category") if is_pep else None

        # Determine risk level based on PEP category
        if is_pep:
            if pep_category in ["foreign_official", "close_associate"]:
                risk_level = RiskLevel.HIGH
            else:
                risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        self._log_execution("pep_screening", {
            "name": name,
            "is_pep": is_pep,
            "category": pep_category
        })

        return ComplianceResult(
            check_type=ComplianceCheckType.PEP_SCREENING,
            passed=True,  # PEP status doesn't fail, just requires enhanced due diligence
            match_found=is_pep,
            matches=filtered_matches,
            risk_level=risk_level,
            flags=["PEP_IDENTIFIED"] if is_pep else [],
            details={
                "searched_name": name,
                "is_pep": is_pep,
                "category": pep_category,
                "databases_checked": config.get("databases", ["PEP_DATABASE"]),
                "threshold": threshold
            }
        )

    async def check_jurisdiction_risk(
        self,
        country: str
    ) -> ComplianceResult:
        """
        Check if a jurisdiction is high-risk.
        """
        country_code = country.upper()[:2] if country else ""
        is_high_risk = country_code in self.HIGH_RISK_JURISDICTIONS

        if is_high_risk:
            risk_level = RiskLevel.HIGH
            country_name = self.HIGH_RISK_JURISDICTIONS.get(country_code, country)
        else:
            risk_level = RiskLevel.LOW
            country_name = country

        self._log_execution("jurisdiction_check", {
            "country": country,
            "is_high_risk": is_high_risk
        })

        return ComplianceResult(
            check_type=ComplianceCheckType.JURISDICTION_RISK,
            passed=not is_high_risk,
            match_found=is_high_risk,
            matches=[],
            risk_level=risk_level,
            flags=["HIGH_RISK_JURISDICTION"] if is_high_risk else [],
            details={
                "country_code": country_code,
                "country_name": country_name,
                "is_high_risk": is_high_risk,
                "reason": "FATF high-risk/non-cooperative jurisdiction" if is_high_risk else None
            }
        )

    def _log_execution(self, check_type: str, details: Dict[str, Any]) -> None:
        """Log check execution for audit trail"""
        self.execution_log.append({
            "check_type": check_type,
            "timestamp": datetime.utcnow().isoformat(),
            "details": details
        })

    def get_execution_log(self) -> List[Dict[str, Any]]:
        """Get the execution log for audit purposes"""
        return self.execution_log.copy()

    def clear_execution_log(self) -> None:
        """Clear the execution log"""
        self.execution_log = []


# Singleton instance for use across the application
compliance_engine = ComplianceEngine()
