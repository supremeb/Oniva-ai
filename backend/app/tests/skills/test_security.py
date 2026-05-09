
from app.agents.skills.security import SecurityValidator

class TestSecurityValidator:

    def test_valid_code(self):
        code = """
import json
import datetime
from typing import List

def run(context, args):
    data = {"key": "value"}
    return json.dumps(data)
"""
        is_valid, errors = SecurityValidator.validate_code(code)
        assert is_valid
        assert len(errors) == 0

    def test_forbidden_import(self):
        code = """
import os
import sys

def run():
    print("bad")
"""
        is_valid, errors = SecurityValidator.validate_code(code)
        assert not is_valid
        assert any("Import forbidden: 'os'" in e for e in errors)
        assert any("Import forbidden: 'sys'" in e for e in errors)

    def test_forbidden_function_call(self):
        code = """
def run():
    eval('print("hacking")')
    exec('import os')
"""
        is_valid, errors = SecurityValidator.validate_code(code)
        assert not is_valid
        assert any("Function call forbidden: 'eval'" in e for e in errors)
        assert any("Function call forbidden: 'exec'" in e for e in errors)

    def test_forbidden_attribute(self):
        code = """
def run():
    return ().__class__.__bases__[0].__subclasses__()
"""
        is_valid, errors = SecurityValidator.validate_code(code)
        assert not is_valid
        assert any("Attribute access forbidden: '__subclasses__'" in e for e in errors)

    def test_secrets_detection(self):
        code = """
def run():
    api_key = "sk-1234567890abcdef1234567890abcdef1234567890abcdef"
    return "done"
"""
        # Note: The regex might need to be robust enough to catch this specific mock key if it matches the pattern
        # The pattern for OpenAI is sk-[a-zA-Z0-9]{48}
        is_valid, errors = SecurityValidator.validate_code(code)
        assert not is_valid
        assert any("Potential OpenAI API key detected" in e for e in errors)

    def test_allowed_pydantic_import(self):
        code = """
from pydantic import BaseModel

class Config(BaseModel):
    foo: str
"""
        is_valid, errors = SecurityValidator.validate_code(code)
        assert is_valid

