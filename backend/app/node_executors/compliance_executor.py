"""
Compliance Node Executor
Executes compliance checks: sanctions screening, PEP checks, jurisdiction risk
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
import time

from app.services.compliance_engine import (
    ComplianceEngine,
    ComplianceResult,
    ScreeningMatch,
)


class ComplianceExecutor(BaseNodeExecutor):
    """
    Executor for compliance nodes.

    Supports compliance_type:
    - sanctions_screening: Check against OFAC/UN/EU sanctions lists
    - pep_screening: Check for Politically Exposed Persons
    - jurisdiction_risk: Check if country is high-risk
    """

    node_type = "compliance"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            config = node_data.get("config", {})
            compliance_type = node_data.get("compliance_type", "sanctions_screening")

            # Create engine instance for this execution
            engine = ComplianceEngine()

            # Get input values with variable substitution
            inputs = self._get_inputs(context)

            # Execute based on compliance type
            if compliance_type == "sanctions_screening":
                result = await self._run_sanctions_screening(engine, inputs, config)
            elif compliance_type == "pep_screening":
                result = await self._run_pep_screening(engine, inputs, config)
            elif compliance_type == "jurisdiction_risk":
                result = await self._run_jurisdiction_check(engine, inputs)
            else:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Unknown compliance type: {compliance_type}",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=result,
                elapsed_ms=elapsed_ms
            )

        except Exception as e:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Compliance check failed: {str(e)}",
                elapsed_ms=int((time.time() - start_time) * 1000)
            )

    def _get_inputs(self, context: NodeExecutionContext) -> Dict[str, Any]:
        """Extract and substitute input variables"""
        inputs = {}
        variables = context.variables

        # Map variable names to their values
        # Support both direct variable names and {{variable}} syntax
        for var_name in ["customer_name", "entity_name", "name", "date_of_birth",
                         "dob", "nationality", "country", "country_of_residence",
                         "counterparty", "aliases"]:
            # Check variables dict
            if var_name in variables:
                inputs[var_name] = variables[var_name]

            # Check for template syntax in node data
            node_data = context.node_data
            if var_name in node_data:
                value = node_data[var_name]
                if isinstance(value, str) and "{{" in value:
                    inputs[var_name] = self.substitute_variables(
                        value, variables, context.inputs
                    )
                else:
                    inputs[var_name] = value

        # Also check config.fields_to_check for field names to extract
        config = context.node_data.get("config", {})
        for field in config.get("fields_to_check", []):
            if field in variables and field not in inputs:
                inputs[field] = variables[field]

        return inputs

    async def _run_sanctions_screening(
        self,
        engine: ComplianceEngine,
        inputs: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run sanctions screening"""
        # Get name from various possible input fields
        name = (
            inputs.get("customer_name") or
            inputs.get("entity_name") or
            inputs.get("name") or
            inputs.get("counterparty") or
            ""
        )

        dob = inputs.get("date_of_birth") or inputs.get("dob")
        country = inputs.get("country") or inputs.get("nationality") or inputs.get("country_of_residence")
        aliases = inputs.get("aliases", [])

        if not name:
            return {
                "match_found": False,
                "matches": [],
                "error": "No name provided for screening",
                "risk_level": "unknown"
            }

        result: ComplianceResult = await engine.run_sanctions_screening(
            name=name,
            dob=dob,
            country=country,
            aliases=aliases if isinstance(aliases, list) else [],
            config=config
        )

        return self._format_result(result)

    async def _run_pep_screening(
        self,
        engine: ComplianceEngine,
        inputs: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run PEP screening"""
        name = (
            inputs.get("customer_name") or
            inputs.get("entity_name") or
            inputs.get("name") or
            ""
        )
        country = inputs.get("country") or inputs.get("nationality")

        if not name:
            return {
                "is_pep": False,
                "matches": [],
                "error": "No name provided for PEP screening",
                "risk_level": "unknown"
            }

        result: ComplianceResult = await engine.run_pep_screening(
            name=name,
            country=country,
            config=config
        )

        output = self._format_result(result)
        output["is_pep"] = result.match_found
        output["category"] = result.details.get("category")
        return output

    async def _run_jurisdiction_check(
        self,
        engine: ComplianceEngine,
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run jurisdiction risk check"""
        country = (
            inputs.get("country") or
            inputs.get("country_of_residence") or
            inputs.get("nationality") or
            ""
        )

        if not country:
            return {
                "is_high_risk": False,
                "error": "No country provided for jurisdiction check",
                "risk_level": "unknown"
            }

        result: ComplianceResult = await engine.check_jurisdiction_risk(country)

        output = self._format_result(result)
        output["is_high_risk"] = result.match_found
        output["country_code"] = result.details.get("country_code")
        output["country_name"] = result.details.get("country_name")
        return output

    def _format_result(self, result: ComplianceResult) -> Dict[str, Any]:
        """Format ComplianceResult for node output"""
        return {
            "check_type": result.check_type.value,
            "passed": result.passed,
            "match_found": result.match_found,
            "matches": [self._format_match(m) for m in result.matches],
            "match_count": len(result.matches),
            "risk_level": result.risk_level.value,
            "flags": result.flags,
            "details": result.details,
            "timestamp": result.timestamp
        }

    def _format_match(self, match: ScreeningMatch) -> Dict[str, Any]:
        """Format a single screening match"""
        return {
            "source_database": match.source_database,
            "matched_name": match.matched_name,
            "match_score": match.match_score,
            "match_type": match.match_type,
            "entry_id": match.entry_id,
            "additional_info": match.additional_info
        }
