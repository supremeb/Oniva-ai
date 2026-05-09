"""
Data Classification Schema for Oniva Security Foundation.

Implements ISO 27001-aligned data classification levels with comparison
methods for high-water mark propagation through workflow execution.

Classification Levels:
- PUBLIC: No business impact if disclosed. Allowed everywhere.
- INTERNAL: Proprietary info, tenant-isolated. External APIs with audit.
- CONFIDENTIAL: PII, financial data, trade secrets. Blocked from external APIs.

Usage:
    from app.schemas.classification import DataClassification

    # Compare classifications
    if DataClassification.CONFIDENTIAL > DataClassification.INTERNAL:
        print("CONFIDENTIAL is more sensitive")

    # High-water mark propagation
    max_classification = max(input_classifications)

    # Export control check
    if not classification.can_export_to_external():
        raise ExportControlViolation("Cannot send to external API")
"""
from enum import Enum


class DataClassification(str, Enum):
    """
    Data classification levels (ISO 27001 aligned).

    Uses str, Enum pattern for database compatibility (stores as string)
    and provides comparison methods for high-water mark propagation.

    Priority values enable comparison:
    - PUBLIC (0): Lowest sensitivity
    - INTERNAL (100): Medium sensitivity
    - CONFIDENTIAL (200): Highest sensitivity
    """
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"

    @property
    def priority(self) -> int:
        """
        Numeric priority for comparison operations.
        Higher values indicate more sensitive data.
        """
        priorities = {
            "public": 0,
            "internal": 100,
            "confidential": 200,
        }
        return priorities[self.value]

    def __gt__(self, other: "DataClassification") -> bool:
        """Greater than comparison based on sensitivity."""
        if not isinstance(other, DataClassification):
            return NotImplemented
        return self.priority > other.priority

    def __ge__(self, other: "DataClassification") -> bool:
        """Greater than or equal comparison based on sensitivity."""
        if not isinstance(other, DataClassification):
            return NotImplemented
        return self.priority >= other.priority

    def __lt__(self, other: "DataClassification") -> bool:
        """Less than comparison based on sensitivity."""
        if not isinstance(other, DataClassification):
            return NotImplemented
        return self.priority < other.priority

    def __le__(self, other: "DataClassification") -> bool:
        """Less than or equal comparison based on sensitivity."""
        if not isinstance(other, DataClassification):
            return NotImplemented
        return self.priority <= other.priority

    def can_export_to_external(self) -> bool:
        """
        Check if data with this classification can be sent to external APIs.

        Returns:
            True if classification allows export to external services.
            False if data must be kept internal (CONFIDENTIAL level).

        Export Policy:
            - PUBLIC: Allowed everywhere
            - INTERNAL: External APIs with audit logging
            - CONFIDENTIAL: BLOCKED from external APIs
        """
        return self.priority < DataClassification.CONFIDENTIAL.priority

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        return self.value.upper()

    @property
    def description(self) -> str:
        """Description of what this classification level means."""
        descriptions = {
            "public": "No business impact if disclosed. Allowed everywhere.",
            "internal": "Proprietary information, tenant-isolated. External APIs with audit.",
            "confidential": "PII, financial data, trade secrets. Blocked from external APIs.",
        }
        return descriptions[self.value]

    @classmethod
    def from_string(cls, value: str) -> "DataClassification":
        """
        Parse classification from string (case-insensitive).

        Args:
            value: String representation of classification level.

        Returns:
            DataClassification enum value.

        Raises:
            ValueError: If value is not a valid classification level.
        """
        if not value:
            return cls.INTERNAL  # Default to INTERNAL for safety
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                f"Invalid classification: '{value}'. "
                f"Must be one of: {', '.join(c.value for c in cls)}"
            )

    @classmethod
    def default(cls) -> "DataClassification":
        """
        Get the default classification level.

        Returns INTERNAL as the safe default - data is assumed to be
        proprietary unless explicitly marked PUBLIC.
        """
        return cls.INTERNAL


# Type alias for cleaner type hints
Classification = DataClassification
