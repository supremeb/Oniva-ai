/**
 * Unit Tests: Code Step Validator
 *
 * Tests for dangerous pattern detection in Python and JavaScript code,
 * timeout validation, and output variable validation.
 */

import {
  validateCode,
  isCodeLikelySafe,
  validateTimeout,
  validateOutputVariable,
  formatWarningsForDisplay,
  MAX_CODE_TIMEOUT_SECONDS,
  MIN_CODE_TIMEOUT_SECONDS,
} from '../validators/code-step';

describe('validateCode - Python', () => {
  describe('dangerous patterns', () => {
    it('should detect eval()', () => {
      const result = validateCode('result = eval(user_input)', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('eval'))).toBe(true);
    });

    it('should detect exec()', () => {
      const result = validateCode('exec(code_string)', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('exec'))).toBe(true);
    });

    it('should detect compile()', () => {
      const result = validateCode('code = compile(source, "file", "exec")', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('compile'))).toBe(true);
    });

    it('should detect __class__ introspection', () => {
      const result = validateCode('obj.__class__.__bases__', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('introspection'))).toBe(true);
    });

    it('should detect __globals__ access', () => {
      const result = validateCode('func.__globals__["os"]', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('global'))).toBe(true);
    });

    it('should detect __builtins__ access', () => {
      const result = validateCode('__builtins__["eval"]', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('builtins'))).toBe(true);
    });

    it('should detect import os', () => {
      const result = validateCode('import os\nos.system("ls")', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('os module'))).toBe(true);
    });

    it('should detect from os import', () => {
      const result = validateCode('from os import path', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('os module'))).toBe(true);
    });

    it('should detect import sys', () => {
      const result = validateCode('import sys\nsys.exit()', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('sys module'))).toBe(true);
    });

    it('should detect subprocess', () => {
      const result = validateCode('import subprocess\nsubprocess.run(["ls"])', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('subprocess'))).toBe(true);
    });

    it('should detect open()', () => {
      const result = validateCode('f = open("/etc/passwd", "r")', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('File operations'))).toBe(true);
    });

    it('should detect pickle', () => {
      const result = validateCode('import pickle\npickle.loads(data)', 'python');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('pickle'))).toBe(true);
    });
  });

  describe('safe code', () => {
    it('should pass simple arithmetic', () => {
      const result = validateCode('result = 2 + 2', 'python');
      expect(result.valid).toBe(true);
      expect(result.warnings).toHaveLength(0);
    });

    it('should pass list comprehensions', () => {
      const result = validateCode('result = [x * 2 for x in range(10)]', 'python');
      expect(result.valid).toBe(true);
    });

    it('should pass function definitions', () => {
      const code = `
def process(data):
    return data.upper()

result = process(input_data)
      `;
      const result = validateCode(code, 'python');
      expect(result.valid).toBe(true);
    });

    it('should pass class definitions', () => {
      const code = `
class Processor:
    def run(self, data):
        return data

result = Processor().run(input_data)
      `;
      const result = validateCode(code, 'python');
      expect(result.valid).toBe(true);
    });

    it('should pass safe imports', () => {
      const result = validateCode('import json\nresult = json.loads(data)', 'python');
      expect(result.valid).toBe(true);
    });
  });
});

describe('validateCode - JavaScript', () => {
  describe('dangerous patterns', () => {
    it('should detect eval()', () => {
      const result = validateCode('const result = eval(userInput);', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('eval'))).toBe(true);
    });

    it('should detect Function()', () => {
      const result = validateCode('const fn = Function("return 1");', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('Function'))).toBe(true);
    });

    it('should detect new Function()', () => {
      const result = validateCode('const fn = new Function("x", "return x");', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('new Function'))).toBe(true);
    });

    it('should detect require()', () => {
      const result = validateCode('const fs = require("fs");', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('require'))).toBe(true);
    });

    it('should detect dynamic import()', () => {
      const result = validateCode('const mod = await import("./module");', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('Dynamic import'))).toBe(true);
    });

    it('should detect process.', () => {
      const result = validateCode('console.log(process.env.SECRET);', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('process'))).toBe(true);
    });

    it('should detect child_process', () => {
      const result = validateCode('const { exec } = child_process;', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('child_process'))).toBe(true);
    });

    it('should detect fs.', () => {
      const result = validateCode('fs.readFileSync("/etc/passwd");', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('File system'))).toBe(true);
    });

    it('should detect __proto__', () => {
      const result = validateCode('obj.__proto__.polluted = true;', 'javascript');
      expect(result.valid).toBe(false);
      expect(result.warnings.some(w => w.message.includes('Prototype'))).toBe(true);
    });
  });

  describe('safe code', () => {
    it('should pass simple operations', () => {
      const result = validateCode('const result = 2 + 2;', 'javascript');
      expect(result.valid).toBe(true);
    });

    it('should pass arrow functions', () => {
      const result = validateCode('const double = x => x * 2;\nconst result = double(5);', 'javascript');
      expect(result.valid).toBe(true);
    });

    it('should pass array methods', () => {
      const result = validateCode('const result = [1, 2, 3].map(x => x * 2);', 'javascript');
      expect(result.valid).toBe(true);
    });

    it('should pass object manipulation', () => {
      const code = `
const obj = { a: 1, b: 2 };
const result = { ...obj, c: 3 };
      `;
      const result = validateCode(code, 'javascript');
      expect(result.valid).toBe(true);
    });

    it('should pass async/await', () => {
      const code = `
const delay = ms => new Promise(r => setTimeout(r, ms));
await delay(100);
const result = "done";
      `;
      const result = validateCode(code, 'javascript');
      expect(result.valid).toBe(true);
    });
  });
});

describe('isCodeLikelySafe', () => {
  it('should return true for safe Python', () => {
    expect(isCodeLikelySafe('result = 2 + 2', 'python')).toBe(true);
  });

  it('should return false for dangerous Python', () => {
    expect(isCodeLikelySafe('import os', 'python')).toBe(false);
  });

  it('should return true for safe JavaScript', () => {
    expect(isCodeLikelySafe('const x = 5;', 'javascript')).toBe(true);
  });

  it('should return false for dangerous JavaScript', () => {
    expect(isCodeLikelySafe('eval("code")', 'javascript')).toBe(false);
  });
});

describe('validateCode line numbers', () => {
  it('should report correct line number for Python', () => {
    const code = `
# Line 1
x = 1
# Line 3
eval(x)
    `;
    const result = validateCode(code, 'python');
    const evalWarning = result.warnings.find(w => w.message.includes('eval'));
    expect(evalWarning?.line).toBe(5);
  });

  it('should report correct line number for JavaScript', () => {
    const code = `
// Line 1
const x = 1;
// Line 3
eval(x);
    `;
    const result = validateCode(code, 'javascript');
    const evalWarning = result.warnings.find(w => w.message.includes('eval'));
    expect(evalWarning?.line).toBe(5);
  });
});

describe('validateTimeout', () => {
  it('should reject timeout less than minimum', () => {
    const result = validateTimeout(0);
    expect(result.valid).toBe(false);
    expect(result.message).toContain('at least');
  });

  it('should reject timeout greater than maximum', () => {
    const result = validateTimeout(31);
    expect(result.valid).toBe(false);
    expect(result.message).toContain('cannot exceed');
  });

  it('should accept minimum timeout', () => {
    const result = validateTimeout(MIN_CODE_TIMEOUT_SECONDS);
    expect(result.valid).toBe(true);
  });

  it('should accept maximum timeout', () => {
    const result = validateTimeout(MAX_CODE_TIMEOUT_SECONDS);
    expect(result.valid).toBe(true);
  });

  it('should accept timeout in valid range', () => {
    expect(validateTimeout(15).valid).toBe(true);
    expect(validateTimeout(1).valid).toBe(true);
    expect(validateTimeout(30).valid).toBe(true);
  });
});

describe('validateOutputVariable', () => {
  it('should reject empty string', () => {
    const result = validateOutputVariable('');
    expect(result.valid).toBe(false);
    expect(result.message).toContain('required');
  });

  it('should reject whitespace only', () => {
    const result = validateOutputVariable('   ');
    expect(result.valid).toBe(false);
  });

  it('should reject starting with number', () => {
    const result = validateOutputVariable('1result');
    expect(result.valid).toBe(false);
    expect(result.message).toContain('valid identifier');
  });

  it('should reject invalid characters', () => {
    expect(validateOutputVariable('my-result').valid).toBe(false);
    expect(validateOutputVariable('my result').valid).toBe(false);
    expect(validateOutputVariable('result!').valid).toBe(false);
  });

  it('should reject Python reserved words', () => {
    expect(validateOutputVariable('class').valid).toBe(false);
    expect(validateOutputVariable('import').valid).toBe(false);
    expect(validateOutputVariable('def').valid).toBe(false);
    expect(validateOutputVariable('True').valid).toBe(false);
    expect(validateOutputVariable('False').valid).toBe(false);
  });

  it('should reject JavaScript reserved words', () => {
    expect(validateOutputVariable('function').valid).toBe(false);
    expect(validateOutputVariable('const').valid).toBe(false);
    expect(validateOutputVariable('let').valid).toBe(false);
    expect(validateOutputVariable('var').valid).toBe(false);
  });

  it('should accept valid identifiers', () => {
    expect(validateOutputVariable('result').valid).toBe(true);
    expect(validateOutputVariable('output_data').valid).toBe(true);
    expect(validateOutputVariable('myResult').valid).toBe(true);
    expect(validateOutputVariable('_private').valid).toBe(true);
    expect(validateOutputVariable('Result123').valid).toBe(true);
  });
});

describe('formatWarningsForDisplay', () => {
  it('should return empty string for no warnings', () => {
    expect(formatWarningsForDisplay([])).toBe('');
  });

  it('should format single warning', () => {
    const warnings = [{
      severity: 'warning' as const,
      message: 'Test warning',
      pattern: 'test',
      line: 5,
    }];
    const formatted = formatWarningsForDisplay(warnings);
    expect(formatted).toContain('Test warning');
    expect(formatted).toContain('line 5');
  });

  it('should format multiple warnings', () => {
    const warnings = [
      { severity: 'warning' as const, message: 'Warning 1', pattern: 'p1' },
      { severity: 'error' as const, message: 'Error 1', pattern: 'p2' },
    ];
    const formatted = formatWarningsForDisplay(warnings);
    expect(formatted).toContain('Warning 1');
    expect(formatted).toContain('Error 1');
  });
});

describe('warning counts', () => {
  it('should count warnings correctly', () => {
    const result = validateCode('eval(x)\nexec(y)\nopen("file")', 'python');
    expect(result.warningCount).toBe(3);
    expect(result.errorCount).toBe(0);
  });

  it('should have zero counts for safe code', () => {
    const result = validateCode('x = 1 + 2', 'python');
    expect(result.warningCount).toBe(0);
    expect(result.errorCount).toBe(0);
  });
});
