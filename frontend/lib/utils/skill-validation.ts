
export interface ValidationResult {
    isValid: boolean;
    errors: string[];
}

export function validateSkillCode(code: string): ValidationResult {
    const errors: string[] = [];

    // Check for frontmatter
    if (!code.trim().startsWith('---')) {
        errors.push('Skill code must start with metadata block in YAML frontmatter (---)');
    }

    // Check if frontmatter closes
    const parts = code.split('---');
    if (parts.length < 3) {
        errors.push('Metadata block must verify closed with ---');
    } else {
        const frontmatter = parts[1];
        if (!frontmatter.includes('name:')) {
            errors.push('Metadata must include "name"');
        }
        if (!frontmatter.includes('description:')) {
            errors.push('Metadata must include "description"');
        }
    }

    // Check implementation
    if (!code.includes('def run(')) {
        errors.push('Skill must define a main execution function (e.g. def run)');
    }

    return {
        isValid: errors.length === 0,
        errors,
    };
}
