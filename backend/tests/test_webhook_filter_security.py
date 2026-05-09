"""Security tests for webhook filter expression evaluation.

Verifies that simpleeval blocks RCE attempts while allowing
legitimate filter expressions.
"""
import pytest
from unittest.mock import AsyncMock

from app.services.webhook_trigger_service import WebhookTriggerService


@pytest.fixture
def service():
    """Create a WebhookTriggerService with a mock db session."""
    mock_db = AsyncMock()
    return WebhookTriggerService(db=mock_db)


# --- Legitimate expressions that MUST work ---

class TestLegitimateFilters:
    def test_simple_equality(self, service):
        payload = {"status": "active"}
        assert service._evaluate_filter("payload['status'] == 'active'", payload) is True

    def test_simple_inequality(self, service):
        payload = {"status": "inactive"}
        assert service._evaluate_filter("payload['status'] == 'active'", payload) is False

    def test_numeric_comparison(self, service):
        payload = {"amount": 1500}
        assert service._evaluate_filter("payload['amount'] > 1000", payload) is True

    def test_compound_boolean(self, service):
        payload = {"amount": 1500, "type": "payment"}
        expr = "payload['amount'] > 1000 and payload['type'] == 'payment'"
        assert service._evaluate_filter(expr, payload) is True

    def test_compound_boolean_false(self, service):
        payload = {"amount": 500, "type": "payment"}
        expr = "payload['amount'] > 1000 and payload['type'] == 'payment'"
        assert service._evaluate_filter(expr, payload) is False

    def test_or_expression(self, service):
        payload = {"status": "error"}
        expr = "payload['status'] == 'error' or payload['status'] == 'failed'"
        assert service._evaluate_filter(expr, payload) is True

    def test_nested_dict_access(self, service):
        payload = {"data": {"user": {"role": "admin"}}}
        assert service._evaluate_filter("payload['data']['user']['role'] == 'admin'", payload) is True

    def test_len_function(self, service):
        payload = {"items": [1, 2, 3]}
        assert service._evaluate_filter("len(payload['items']) > 2", payload) is True

    def test_str_function(self, service):
        payload = {"code": 200}
        assert service._evaluate_filter("str(payload['code']) == '200'", payload) is True

    def test_int_function(self, service):
        payload = {"value": "42"}
        assert service._evaluate_filter("int(payload['value']) > 10", payload) is True

    def test_in_operator(self, service):
        payload = {"status": "active"}
        assert service._evaluate_filter("payload['status'] in ('active', 'pending')", payload) is True

    def test_not_operator(self, service):
        payload = {"deleted": False}
        assert service._evaluate_filter("not payload['deleted']", payload) is True


# --- RCE bypass attempts that MUST be blocked ---

class TestRCEPrevention:
    def test_import_os(self, service):
        payload = {}
        result = service._evaluate_filter("__import__('os').system('rm -rf /')", payload)
        assert result is False

    def test_import_subprocess(self, service):
        payload = {}
        result = service._evaluate_filter("__import__('subprocess').call(['ls'])", payload)
        assert result is False

    def test_class_traversal(self, service):
        payload = {}
        result = service._evaluate_filter(
            "().__class__.__bases__[0].__subclasses__()", payload
        )
        assert result is False

    def test_mro_traversal(self, service):
        payload = {}
        result = service._evaluate_filter(
            "payload.__class__.__mro__", payload
        )
        assert result is False

    def test_globals_access(self, service):
        payload = {}
        result = service._evaluate_filter(
            "payload.__class__.__init__.__globals__", payload
        )
        assert result is False

    def test_builtins_access(self, service):
        payload = {}
        result = service._evaluate_filter("__builtins__", payload)
        assert result is False

    def test_eval_call(self, service):
        payload = {}
        result = service._evaluate_filter("eval('1+1')", payload)
        assert result is False

    def test_exec_call(self, service):
        payload = {}
        result = service._evaluate_filter("exec('import os')", payload)
        assert result is False

    def test_open_file(self, service):
        payload = {}
        result = service._evaluate_filter("open('/etc/passwd').read()", payload)
        assert result is False

    def test_compile_call(self, service):
        payload = {}
        result = service._evaluate_filter(
            "compile('import os', '<string>', 'exec')", payload
        )
        assert result is False

    def test_getattr_bypass(self, service):
        payload = {}
        result = service._evaluate_filter(
            "getattr(payload, '__class__')", payload
        )
        assert result is False

    def test_type_call(self, service):
        payload = {}
        result = service._evaluate_filter(
            "type('exploit', (object,), {'__init__': lambda s: None})()", payload
        )
        assert result is False


# --- Edge cases ---

class TestEdgeCases:
    def test_empty_expression_returns_false(self, service):
        assert service._evaluate_filter("", {}) is False

    def test_syntax_error_returns_false(self, service):
        assert service._evaluate_filter("payload[", {}) is False

    def test_missing_key_returns_false(self, service):
        assert service._evaluate_filter("payload['nonexistent'] == 'x'", {}) is False

    def test_none_payload_value(self, service):
        payload = {"value": None}
        assert service._evaluate_filter("payload['value'] is None", payload) is True

    def test_boolean_payload_value(self, service):
        payload = {"enabled": True}
        assert service._evaluate_filter("payload['enabled']", payload) is True


# --- Concurrency safety ---

class TestConcurrencySafety:
    def test_independent_evaluations(self, service):
        """Verify separate evaluations don't interfere with each other."""
        payload_a = {"value": 10}
        payload_b = {"value": 20}
        expr = "payload['value'] > 15"

        assert service._evaluate_filter(expr, payload_a) is False
        assert service._evaluate_filter(expr, payload_b) is True
        # Re-verify first payload wasn't affected
        assert service._evaluate_filter(expr, payload_a) is False
