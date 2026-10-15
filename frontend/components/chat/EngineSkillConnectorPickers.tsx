'use client';

import { useState } from 'react';
import { ConnectorPopover } from './ConnectorPopover';
import { SkillPicker } from './SkillPicker';
import type { Skill } from '@/lib/api/skills';

export interface EngineSkillConnectorPickersProps {
    conversationId: string | null;
    skills: Skill[];
    connectorIds: string[];
    onSelectSkill: (skill: Skill) => void;
    onToggleConnector: (connectorId: string) => void;
}

/** Skill picker + connector popover for the engine composer toolbar (reuses the legacy components). */
export function EngineSkillConnectorPickers(props: EngineSkillConnectorPickersProps) {
    const [skillsOpen, setSkillsOpen] = useState(false);
    return (
        <>
            <ConnectorPopover
                sessionId={props.conversationId ?? ''}
                activeConnectorIds={props.connectorIds}
                onToggle={props.onToggleConnector}
            />
            <SkillPicker
                open={skillsOpen}
                onOpenChange={setSkillsOpen}
                selectedSkillIds={props.skills.map((s) => s.id)}
                onSkillSelect={props.onSelectSkill}
            />
        </>
    );
}
