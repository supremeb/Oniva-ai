/**
 * Code Step Validation
 *
 * Frontend validation for code execution steps.
 * Detects potentially dangerous patterns before submission.
 *
 * NOTE: This is defense-in-depth. The backend must also
 * sandbox code execution using AST validation and isolated execution.
 */

// ============ Dangerous Patterns ============

/**
 * Regex patterns that may indicate dangerous code constructs.
 * These are warnings, not hard blocks (backend is authoritative).
 */
const DANGEROUS_PATTERNS: Record<'python' | 'javascript', Array<{ pattern: RegExp; reason: string }>> = {
  python: [
    // Reflection/introspection escapes
    { pattern: /__class__/, reason: 'Class introspection can bypass sandbox' },
    { pattern: /__globals__/, reason: 'Access to global namespace is restricted' },
    { pattern: /__builtins__/, reason: 'Direct builtins access is restricted' },
    { pattern: /__subclasses__/, reason: 'Subclass access can escape sandbox' },
    { pattern: /__mro__/, reason: 'Method resolution order access is restricted' },

    // Code execution
    { pattern: /\beval\s*\(/, reason: 'eval() is not allowed' },
    { pattern: /\bexec\s*\(/, reason: 'exec() is not allowed' },
    { pattern: /\bcompile\s*\(/, reason: 'compile() is not allowed' },

    // File system
    { pattern: /\bopen\s*\(/, reason: 'File operations are restricted' },

    // Dangerous imports
    { pattern: /\bimport\s+os\b/, reason: 'os module is restricted' },
    { pattern: /\bfrom\s+os\b/, reason: 'os module is restricted' },
    { pattern: /\bimport\s+sys\b/, reason: 'sys module is restricted' },
    { pattern: /\bfrom\s+sys\b/, reason: 'sys module is restricted' },
    { pattern: /\bimport\s+subprocess\b/, reason: 'subprocess is restricted' },
    { pattern: /\bimport\s+shutil\b/, reason: 'shutil is restricted' },
    { pattern: /\bimport\s+pickle\b/, reason: 'pickle is restricted (security risk)' },
    { pattern: /\bimport\s+socket\b/, reason: 'socket is restricted' },
    { pattern: /\bimport\s+ctypes\b/, reason: 'ctypes is restricted' },

    // Network
    { pattern: /\burllib\b/, reason: 'Network access is restricted' },
    { pattern: /\brequests\b/, reason: 'Network access is restricted' },
  ],
  javascript: [
    // Code execution
    { pattern: /\beval\s*\(/, reason: 'eval() is not allowed' },
    { pattern: /\bFunction\s*\(/, reason: 'Function constructor is not allowed' },
    { pattern: /new\s+Function\b/, reason: 'new Function() is not allowed' },

    // Module system
    { pattern: /\brequire\s*\(/, reason: 'require() is not available in sandbox' },
    { pattern: /\bimport\s*\(/, reason: 'Dynamic import is not allowed' },

    // Node.js specifics
    { pattern: /\bprocess\./, reason: 'process object is not available' },
    { pattern: /child_process/, reason: 'child_process is not available' },
    { pattern: /\bfs\./, reason: 'File system access is not available' },

    // Prototype pollution
    { pattern: /__proto__/, reason: 'Prototype manipulation is restricted' },
    { pattern: /\.constructor\./, reason: 'Constructor access may be restricted' },
    { pattern: /Object\.prototype/, reason: 'Prototype modification is restricted' },

    // Global access
    { pattern: /\bglobalThis\b/, reason: 'Global access is restricted' },
    { pattern: /\bglobal\b/, reason: 'Global access is restricted' },
    { pattern: /\bwindow\b/, reason: 'window object is not available' },
  ],
};

// ============ Validation Types ============

export interface CodeValidationResult {
  valid: boolean;
  warnings: CodeWarning[];
  errorCount: number;
  warningCount: number;
}

export interface CodeWarning {
  severity: 'error' | 'warning';
  message: string;
  pattern: string;
  line?: number;
}

// ============ Main Validation Function ============

/**
 * Validate code for potentially dangerous patterns.
 * Returns warnings/errors that should be shown to the user.
 */
export function validateCode(
  code: string,
  language: 'python' | 'javascript'
): CodeValidationResult {
  const warnings: CodeWarning[] = [];
  const patterns = DANGEROUS_PATTERNS[language];

  // Split into lines for better error reporting
  const lines = code.split('\n');

  for (const { pattern, reason } of patterns) {
    // Check each line
    for (let i = 0; i < lines.length; i++) {
      if (pattern.test(lines[i])) {
        warnings.push({
          severity: 'warning',
          message: reason,
          pattern: pattern.source,
          line: i + 1,
        });
      }
    }
  }

  // Deduplicate warnings (same pattern may match multiple times)
  const uniqueWarnings = deduplicateWarnings(warnings);

  return {
    valid: uniqueWarnings.length === 0,
    warnings: uniqueWarnings,
    errorCount: uniqueWarnings.filter(w => w.severity === 'error').length,
    warningCount: uniqueWarnings.filter(w => w.severity === 'warning').length,
  };
}

/**
 * Quick check if code is likely safe (no warnings).
 * Use for UI indicators without full validation detail.
 */
export function isCodeLikelySafe(
  code: string,
  language: 'python' | 'javascript'
): boolean {
  const patterns = DANGEROUS_PATTERNS[language];

  for (const { pattern } of patterns) {
    if (pattern.test(code)) {
      return false;
    }
  }

  return true;
}

// ============ Timeout Validation ============

/**
 * Maximum timeout allowed (must match backend config).
 */
export const MAX_CODE_TIMEOUT_SECONDS = 30;
export const MIN_CODE_TIMEOUT_SECONDS = 1;

/**
 * Validate timeout value.
 */
export function validateTimeout(timeout: number): {
  valid: boolean;
  message?: string;
} {
  if (timeout < MIN_CODE_TIMEOUT_SECONDS) {
    return {
      valid: false,
      message: `Timeout must be at least ${MIN_CODE_TIMEOUT_SECONDS} second`,
    };
  }

  if (timeout > MAX_CODE_TIMEOUT_SECONDS) {
    return {
      valid: false,
      message: `Timeout cannot exceed ${MAX_CODE_TIMEOUT_SECONDS} seconds`,
    };
  }

  return { valid: true };
}

// ============ Output Variable Validation ============

/**
 * Valid Python/JavaScript identifier pattern.
 */
const IDENTIFIER_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

/**
 * Reserved words that cannot be used as output variable names.
 */
const RESERVED_WORDS = new Set([
  // Python
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
  'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
  'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
  'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try',
  'while', 'with', 'yield',
  // JavaScript
  'abstract', 'boolean', 'break', 'byte', 'case', 'catch', 'char',
  'class', 'const', 'continue', 'debugger', 'default', 'delete', 'do',
  'double', 'else', 'enum', 'export', 'extends', 'false', 'final',
  'finally', 'float', 'for', 'function', 'goto', 'if', 'implements',
  'import', 'in', 'instanceof', 'int', 'interface', 'let', 'long',
  'native', 'new', 'null', 'package', 'private', 'protected', 'public',
  'return', 'short', 'static', 'super', 'switch', 'synchronized', 'this',
  'throw', 'throws', 'transient', 'true', 'try', 'typeof', 'var', 'void',
  'volatile', 'while', 'with', 'yield',
]);

/**
 * Validate output variable name.
 */
export function validateOutputVariable(name: string): {
  valid: boolean;
  message?: string;
} {
  if (!name || name.trim() === '') {
    return {
      valid: false,
      message: 'Output variable name is required',
    };
  }

  const trimmed = name.trim();

  if (!IDENTIFIER_PATTERN.test(trimmed)) {
    return {
      valid: false,
      message: 'Must be a valid identifier (letters, numbers, underscore)',
    };
  }

  if (RESERVED_WORDS.has(trimmed)) {
    return {
      valid: false,
      message: `"${trimmed}" is a reserved word`,
    };
  }

  return { valid: true };
}

// ============ Helpers ============

/**
 * Deduplicate warnings by pattern (keep first occurrence).
 */
function deduplicateWarnings(warnings: CodeWarning[]): CodeWarning[] {
  const seen = new Set<string>();
  const result: CodeWarning[] = [];

  for (const warning of warnings) {
    if (!seen.has(warning.pattern)) {
      seen.add(warning.pattern);
      result.push(warning);
    }
  }

  return result;
}

/**
 * Format warnings for display in UI.
 */
export function formatWarningsForDisplay(warnings: CodeWarning[]): string {
  if (warnings.length === 0) return '';

  return warnings
    .map(w => {
      const lineInfo = w.line ? ` (line ${w.line})` : '';
      return `${w.severity === 'error' ? '!' : '?'} ${w.message}${lineInfo}`;
    })
    .join('\n');
}
