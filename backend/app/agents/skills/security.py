import ast
import re
from typing import List, Tuple

class SecurityValidator:
    """
    Validates Python code for custom skills using AST analysis.
    Enforces security policies by restricting imports and dangerous operations.
    """

    # Whitelisted modules that are safe to import
    ALLOWED_IMPORTS = {
        'json',
        'datetime',
        'math',
        'random',
        're',
        'typing',
        'uuid',
        'collections',
        'itertools',
        'functools',
        'urllib.parse',
        'requests', # Often needed, but can be dangerous. verification needed.
        # Project specific
        'app.agents.context',
        'pydantic',
    }

    # Dangerous functions that are explicitly forbidden
    FORBIDDEN_FUNCTIONS = {
        'eval',
        'exec',
        'globals',
        'locals',
        'compile',
        'open',
        'input',
        '__import__',
        'exit',
        'quit',
        'help',
        'dir',
        'vars'
    }

    # Dangerous attributes to block
    FORBIDDEN_ATTRIBUTES = {
        '__subclasses__',
        '__bases__',
        '__globals__',
        '__code__',
        '__closure__',
        '__unwrap__',
    }

    @classmethod
    def validate_code(cls, code: str) -> Tuple[bool, List[str]]:
        """
        Statically analyze code for security violations.
        
        Args:
            code: The Python source code string.
            
        Returns:
            Tuple[bool, List[str]]: (is_valid, list_of_errors)
        """
        errors = []
        
        # 1. Secret Scanning
        secret_errors = cls._scan_for_secrets(code)
        errors.extend(secret_errors)

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"Syntax Error: {str(e)}"]

        # 2. AST Analysis
        for node in ast.walk(tree):
            # Check Imports
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                errors.extend(cls._validate_import(node))
            
            # Check Function Calls
            if isinstance(node, ast.Call):
                errors.extend(cls._validate_call(node))

            # Check Attribute Access
            if isinstance(node, ast.Attribute):
                errors.extend(cls._validate_attribute(node))

        return len(errors) == 0, errors

    @classmethod
    def _validate_import(cls, node: ast.AST) -> List[str]:
        errors = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in cls.ALLOWED_IMPORTS:
                    errors.append(f"Import forbidden: '{alias.name}'. Module not in allowlist.")
        
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                # Relative imports are tricky, blocking for now for custom skills
                errors.append("Relative imports are not allowed.")
                return errors
            
            if node.module.split('.')[0] not in cls.ALLOWED_IMPORTS:
                errors.append(f"Import forbidden: '{node.module}'. Module not in allowlist.")
        
        return errors

    @classmethod
    def _validate_call(cls, node: ast.Call) -> List[str]:
        errors = []
        if isinstance(node.func, ast.Name):
            if node.func.id in cls.FORBIDDEN_FUNCTIONS:
                errors.append(f"Function call forbidden: '{node.func.id}'")
        return errors

    @classmethod
    def _validate_attribute(cls, node: ast.Attribute) -> List[str]:
        errors = []
        if node.attr in cls.FORBIDDEN_ATTRIBUTES:
            errors.append(f"Attribute access forbidden: '{node.attr}'")
        return errors

    @classmethod
    def _scan_for_secrets(cls, code: str) -> List[str]:
        """Simple regex scan for potential hardcoded secrets"""
        errors = []
        
        # Patterns for common API keys
        patterns = [
            (r'(?i)(api_key|apikey|secret|token)\s*=\s*[\'"][a-zA-Z0-9_\-]{20,}[\'"]', "Potential hardcoded API key or secret detected"),
            (r'sk-[a-zA-Z0-9]{48}', "Potential OpenAI API key detected"),
        ]
        
        for pattern, msg in patterns:
            if re.search(pattern, code):
                errors.append(msg)
                
        return errors
