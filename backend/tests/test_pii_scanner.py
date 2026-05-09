"""
Unit tests for PII scanner utility.

Tests cover:
- SSN detection
- Credit card detection (with Luhn validation)
- Email address detection
- Phone number detection
- Clean text passes through
- has_pii convenience method
"""


from app.utils.pii_scanner import scan_for_pii, has_pii, _luhn_check


class TestSSNDetection:
    """Tests for SSN pattern detection."""

    def test_detects_standard_ssn(self):
        text = "Customer SSN is 123-45-6789"
        matches = scan_for_pii(text)
        assert len(matches) == 1
        assert matches[0].pii_type == "SSN"
        assert matches[0].pattern_match == "123-45-6789"

    def test_detects_multiple_ssns(self):
        text = "SSN 123-45-6789 and 987-65-4321"
        matches = scan_for_pii(text)
        ssn_matches = [m for m in matches if m.pii_type == "SSN"]
        assert len(ssn_matches) == 2

    def test_ignores_non_ssn_numbers(self):
        text = "Reference number 123456789 is not an SSN"
        matches = scan_for_pii(text)
        ssn_matches = [m for m in matches if m.pii_type == "SSN"]
        assert len(ssn_matches) == 0


class TestCreditCardDetection:
    """Tests for credit card detection with Luhn validation."""

    def test_luhn_check_valid(self):
        # Known valid test card number (Visa)
        assert _luhn_check("4111111111111111") is True

    def test_luhn_check_invalid(self):
        assert _luhn_check("4111111111111112") is False

    def test_luhn_check_too_short(self):
        assert _luhn_check("411111") is False

    def test_detects_visa_card(self):
        # Valid Visa test number
        text = "Card: 4111 1111 1111 1111"
        matches = scan_for_pii(text)
        cc_matches = [m for m in matches if m.pii_type == "credit card number"]
        assert len(cc_matches) == 1

    def test_ignores_invalid_card_luhn(self):
        # Invalid Luhn checksum
        text = "Card: 4111 1111 1111 1112"
        matches = scan_for_pii(text)
        cc_matches = [m for m in matches if m.pii_type == "credit card number"]
        assert len(cc_matches) == 0


class TestEmailDetection:
    """Tests for email address detection."""

    def test_detects_email(self):
        text = "Contact john.doe@example.com for details"
        matches = scan_for_pii(text)
        email_matches = [m for m in matches if m.pii_type == "email address"]
        assert len(email_matches) == 1
        assert email_matches[0].pattern_match == "john.doe@example.com"

    def test_detects_multiple_emails(self):
        text = "Send to user@test.com and admin@corp.io"
        matches = scan_for_pii(text)
        email_matches = [m for m in matches if m.pii_type == "email address"]
        assert len(email_matches) == 2


class TestPhoneDetection:
    """Tests for phone number detection."""

    def test_detects_phone_with_parens(self):
        text = "Call (555) 123-4567"
        matches = scan_for_pii(text)
        phone_matches = [m for m in matches if m.pii_type == "phone number"]
        assert len(phone_matches) == 1

    def test_detects_phone_with_dashes(self):
        text = "Phone: 555-123-4567"
        matches = scan_for_pii(text)
        phone_matches = [m for m in matches if m.pii_type == "phone number"]
        assert len(phone_matches) == 1


class TestCleanText:
    """Tests for text without PII."""

    def test_clean_text_returns_empty(self):
        text = "Acme Corp has elevated credit risk due to 18% revenue decline"
        matches = scan_for_pii(text)
        assert len(matches) == 0

    def test_financial_data_no_false_positives(self):
        text = "Revenue: $2.1B, EBITDA: $450M, D/E ratio: 2.3x"
        matches = scan_for_pii(text)
        assert len(matches) == 0

    def test_empty_text(self):
        matches = scan_for_pii("")
        assert len(matches) == 0


class TestHasPII:
    """Tests for has_pii convenience method."""

    def test_returns_false_for_clean_text(self):
        found, description = has_pii("Clean financial analysis text")
        assert found is False
        assert description is None

    def test_returns_true_for_ssn(self):
        found, description = has_pii("Customer SSN 123-45-6789")
        assert found is True
        assert "SSN" in description

    def test_returns_true_for_email(self):
        found, description = has_pii("Contact user@example.com")
        assert found is True
        assert "email" in description

    def test_returns_first_match_type(self):
        found, description = has_pii("SSN 123-45-6789 and user@test.com")
        assert found is True
        assert description is not None
