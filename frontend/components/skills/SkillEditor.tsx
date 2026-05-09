'use client';

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { AlertCircle, CheckCircle2, Save } from 'lucide-react';
import { validateSkillCode } from '@/lib/utils/skill-validation';

// Lazy load Monaco Editor - this is a heavy dependency (~2MB)
const Editor = dynamic(() => import('@monaco-editor/react'), {
    ssr: false,
    loading: () => (
        <div className="h-full w-full flex items-center justify-center bg-zinc-950 text-zinc-400 text-sm">
            Loading editor...
        </div>
    ),
});

interface SkillEditorProps {
    initialCode?: string;
    onSave: (code: string) => void;
}

const DEFAULT_CODE = `---
name: my_new_skill
description: A new custom skill
type: python
version: 1.0.0
---
from app.agents.context import AgentContext

def run(context: AgentContext, args: dict):
    # Your logic here
    return "Hello World"
`;

export function SkillEditor({ initialCode = DEFAULT_CODE, onSave }: SkillEditorProps) {
    const [code, setCode] = useState(initialCode);
    const [validation, setValidation] = useState(validateSkillCode(initialCode));

    const handleEditorChange = (value: string | undefined) => {
        const newCode = value || '';
        setCode(newCode);
        setValidation(validateSkillCode(newCode));
    };

    const handleSave = () => {
        if (validation.isValid) {
            onSave(code);
        }
    };

    return (
        <div className="flex flex-col h-[600px] gap-4">
            <div className="flex justify-between items-center mb-2">
                <div className="flex items-center gap-4">
                    {validation.isValid ? (
                        <div className="text-green-600 flex items-center text-sm gap-1">
                            <CheckCircle2 className="w-4 h-4" /> Valid
                        </div>
                    ) : (
                        <div className="text-red-500 flex items-center text-sm gap-1">
                            <AlertCircle className="w-4 h-4" /> Invalid Schema
                        </div>
                    )}
                </div>
                <Button onClick={handleSave} disabled={!validation.isValid}>
                    <Save className="w-4 h-4 mr-2" /> Save Draft
                </Button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 h-full">
                <div className="col-span-3 border rounded-md overflow-hidden bg-zinc-950">
                    <Editor
                        height="100%"
                        defaultLanguage="python"
                        theme="vs-dark"
                        value={code}
                        onChange={handleEditorChange}
                        options={{
                            minimap: { enabled: false },
                            fontSize: 14,
                            lineNumbers: 'on',
                        }}
                    />
                </div>
                <div>
                    {!validation.isValid && validation.errors.length > 0 && (
                        <Alert variant="destructive">
                            <AlertCircle className="h-4 w-4" />
                            <AlertTitle>Validation Errors</AlertTitle>
                            <AlertDescription>
                                <ul className="list-disc pl-4 text-xs mt-2">
                                    {validation.errors.map((err, i) => (
                                        <li key={i}>{err}</li>
                                    ))}
                                </ul>
                            </AlertDescription>
                        </Alert>
                    )}
                    <Card className="mt-4">
                        <CardContent className="pt-6 text-sm text-muted-foreground">
                            <h4 className="font-semibold mb-2">Instructions</h4>
                            <p className="mb-2">Use Yaml Frontmatter for metadata.</p>
                            <p>Define a <code>run</code> function that accepts <code>context</code> and <code>args</code>.</p>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
