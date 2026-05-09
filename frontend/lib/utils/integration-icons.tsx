'use client';

import React from 'react';

// Integration brand icons for workflow nodes
// These are simplified SVG icons representing popular integrations

export const LinearIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#5E6AD2" d="M3.357 14.143l6.5 6.5a9.955 9.955 0 01-6.5-6.5zM1.643 11.357L12.643 22.357a10.028 10.028 0 002.715-.757L2.4 8.643a10.028 10.028 0 00-.757 2.714zM3.643 6.643l13.714 13.714a9.966 9.966 0 002-1.714L5.357 4.643a9.966 9.966 0 00-1.714 2zM6.643 3.643l14.714 14.714a9.966 9.966 0 001.286-2.357L9 2.357a9.966 9.966 0 00-2.357 1.286zM11.143 1.643l11.214 11.214a10.028 10.028 0 00.757-2.714L13.857.886a10.028 10.028 0 00-2.714.757zM16.857 1.357a9.955 9.955 0 016.5 6.5l-6.5-6.5z"/>
    </svg>
);

export const SlackIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" fill="#E01E5A"/>
        <path d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" fill="#36C5F0"/>
        <path d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" fill="#2EB67D"/>
        <path d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#ECB22E"/>
    </svg>
);

export const GmailIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#EA4335" d="M12 12.87l10.5-7.65v-.7A2.52 2.52 0 0020 2H4a2.52 2.52 0 00-2.5 2.52v.7L12 12.87z"/>
        <path fill="#34A853" d="M12 14.13L1.5 6.48v11.5c0 1.4 1.12 2.52 2.5 2.52h16c1.38 0 2.5-1.12 2.5-2.52V6.48L12 14.13z"/>
        <path fill="#FBBC05" d="M1.5 5.22V6.48l10.5 7.65V12.87L1.5 5.22z"/>
        <path fill="#C5221F" d="M22.5 5.22l-10.5 7.65v1.26l10.5-7.65V5.22z"/>
    </svg>
);

export const GoogleSheetsIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#0F9D58" d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6z"/>
        <path fill="#87CEAC" d="M14 2v6h6l-6-6z"/>
        <path fill="#fff" d="M10 13H8v2h2v-2zm0-3H8v2h2v-2zm4 0h-2v2h2v-2zm0 3h-2v2h2v-2zm-4 3H8v2h2v-2zm4 0h-2v2h2v-2z"/>
    </svg>
);

export const GoogleDriveIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#4285F4" d="M7.71 16.29L3 21.99h10l4.71-5.7H7.71z"/>
        <path fill="#0F9D58" d="M13.29 7.71L8.59 2H7.71l-4.7 5.71h10.28z"/>
        <path fill="#FFCD40" d="M21.99 15.99L17.29 7.71H7.71L12.41 16h9.58z"/>
    </svg>
);

export const AirtableIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#FCB400" d="M12.07 2L1.5 6.3v8.4L12.07 19l10.57-4.3V6.3L12.07 2z"/>
        <path fill="#18BFFF" d="M12.07 2v17L1.5 14.7V6.3L12.07 2z"/>
        <path fill="#F82B60" d="M12.07 19l10.57-4.3V6.3L12.07 2v17z"/>
    </svg>
);

export const NotionIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#000" d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 2.02c-.42-.327-.98-.7-2.055-.607L3.01 2.72c-.466.046-.56.28-.374.466l1.823 1.022zM5.25 7.39v13.217c0 .7.327 1.02.98.98l14.523-.84c.654-.046.746-.42.746-1.026V6.457c0-.56-.233-.84-.7-.793l-15.03.84c-.513.046-.52.326-.52.886zM18.36 8.27c.094.42 0 .84-.42.886l-.7.14v9.783c-.607.327-1.167.513-1.633.513-.747 0-.934-.233-1.494-.933l-4.574-7.19v6.958l1.447.327s0 .84-1.166.84l-3.22.187c-.093-.186 0-.653.327-.746l.84-.233V9.526l-1.167-.093c-.093-.42.14-1.026.793-1.073l3.453-.233 4.76 7.283V9.013l-1.213-.14c-.094-.513.28-.886.746-.933l3.22-.187zM2.334 1.6l12.965-.84c1.634-.14 2.054-.047 3.081.7l4.247 2.986c.7.513.934.653.934 1.213v14.197c0 .887-.327 1.4-1.447 1.494l-15.45.933c-.84.046-1.26-.093-1.727-.7L1.4 17.943c-.513-.7-.746-1.213-.746-1.82V3.02c0-.793.327-1.4 1.68-1.42z"/>
    </svg>
);

export const GitHubIcon = () => (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
        <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
    </svg>
);

export const ServiceNowIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <circle cx="12" cy="12" r="10" fill="#81B5A1"/>
        <path fill="#fff" d="M12 6c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm0 10c-2.21 0-4-1.79-4-4s1.79-4 4-4 4 1.79 4 4-1.79 4-4 4z"/>
    </svg>
);

export const SalesforceIcon = () => (
    <svg viewBox="0 0 24 24" className="w-4 h-4">
        <path fill="#00A1E0" d="M10.33 4.96c.79-.83 1.9-1.34 3.13-1.34 1.59 0 2.98.87 3.73 2.15.65-.28 1.36-.44 2.11-.44 2.93 0 5.3 2.37 5.3 5.3 0 2.93-2.37 5.3-5.3 5.3-.37 0-.72-.04-1.07-.11-.62 1.32-1.97 2.23-3.53 2.23-.58 0-1.13-.13-1.62-.35-.69 1.37-2.12 2.31-3.76 2.31-1.94 0-3.57-1.31-4.07-3.09-.24.04-.49.06-.74.06-2.48 0-4.48-2-4.48-4.48 0-1.86 1.14-3.46 2.76-4.13-.08-.4-.13-.81-.13-1.23 0-3.3 2.68-5.97 5.97-5.97 1.91 0 3.62.9 4.7 2.3z"/>
    </svg>
);

// Integration icon mapping - maps connector names to their brand icons
export interface IntegrationIconInfo {
    icon: React.ReactNode;
    color: string;
    isMCP?: boolean;
}

export const INTEGRATION_ICONS: Record<string, IntegrationIconInfo> = {
    linear: { icon: <LinearIcon />, color: '#5E6AD2', isMCP: true },
    slack: { icon: <SlackIcon />, color: '#4A154B', isMCP: true },
    gmail: { icon: <GmailIcon />, color: '#EA4335', isMCP: true },
    google_sheets: { icon: <GoogleSheetsIcon />, color: '#0F9D58', isMCP: true },
    google_drive: { icon: <GoogleDriveIcon />, color: '#4285F4', isMCP: true },
    airtable: { icon: <AirtableIcon />, color: '#18BFFF', isMCP: true },
    notion: { icon: <NotionIcon />, color: '#000000', isMCP: true },
    github: { icon: <GitHubIcon />, color: '#24292e', isMCP: true },
    servicenow: { icon: <ServiceNowIcon />, color: '#81B5A1', isMCP: true },
    salesforce: { icon: <SalesforceIcon />, color: '#00A1E0', isMCP: true },
};

// Helper to get integration info by connector name/ID
export function getIntegrationInfo(connectorName: string | undefined): IntegrationIconInfo | null {
    if (!connectorName) return null;
    const normalizedName = connectorName.toLowerCase().replace(/[^a-z0-9]/g, '_');
    return INTEGRATION_ICONS[normalizedName] || null;
}
