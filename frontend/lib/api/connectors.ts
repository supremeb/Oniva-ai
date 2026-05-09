
import { apiClient } from './client';
import { fetchWithTimeout, FetchError } from './fetch-utils';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface ConnectorMetadata {
    id: string;
    name: string;
    description: string;
    icon: string;
    category: 'itsm' | 'communication' | 'crm' | 'custom';
    auth_type: string;
    version: string;
    enterprise_only: boolean;
}

export interface ConnectRequest {
    credentials: Record<string, any>;
}

// Mock connectors for when backend is unavailable
const MOCK_CONNECTORS: ConnectorMetadata[] = [
    {
        id: 'servicenow',
        name: 'ServiceNow',
        description: 'ITSM platform for incident management and workflows',
        icon: 'servicenow',
        category: 'itsm',
        auth_type: 'oauth2',
        version: '1.0.0',
        enterprise_only: false,
    },
    {
        id: 'slack',
        name: 'Slack',
        description: 'Team communication and collaboration platform',
        icon: 'slack',
        category: 'communication',
        auth_type: 'oauth2',
        version: '1.0.0',
        enterprise_only: false,
    },
    {
        id: 'salesforce',
        name: 'Salesforce',
        description: 'CRM platform for customer relationship management',
        icon: 'salesforce',
        category: 'crm',
        auth_type: 'oauth2',
        version: '1.0.0',
        enterprise_only: true,
    },
    {
        id: 'jira',
        name: 'Jira',
        description: 'Issue tracking and project management',
        icon: 'jira',
        category: 'itsm',
        auth_type: 'api_key',
        version: '1.0.0',
        enterprise_only: false,
    },
    {
        id: 'github',
        name: 'GitHub',
        description: 'Version control and collaboration platform',
        icon: 'github',
        category: 'custom',
        auth_type: 'oauth2',
        version: '1.0.0',
        enterprise_only: false,
    },
];

export const connectorsApi = {
    list: async (): Promise<ConnectorMetadata[]> => {
        try {
            const connectors = await fetchWithTimeout<ConnectorMetadata[]>(
                `${API_BASE}/api/v1/connectors/`,
                {
                    timeout: 5000, // 5 seconds
                    retries: 1,
                }
            );
            return connectors;
        } catch (error) {
            console.warn('Failed to fetch connectors, using mock data:', error);
            return MOCK_CONNECTORS;
        }
    },

    get: async (id: string): Promise<ConnectorMetadata> => {
        try {
            return await apiClient<ConnectorMetadata>(`/api/v1/connectors/${id}`);
        } catch (error) {
            console.warn(`Failed to fetch connector ${id}, using mock data`);
            const mockConnector = MOCK_CONNECTORS.find(c => c.id === id);
            if (mockConnector) return mockConnector;
            throw error;
        }
    },

    connect: async (id: string, credentials: Record<string, any>) => {
        return apiClient<{ status: string; message: string }>(`/api/v1/connectors/${id}/connect`, {
            method: 'POST',
            body: JSON.stringify({ credentials }),
        });
    },

    getStatus: async (id: string): Promise<{ status: string; message?: string }> => {
        try {
            return await fetchWithTimeout<{ status: string; message?: string }>(
                `${API_BASE}/api/v1/connectors/${id}/status`,
                {
                    timeout: 5000, // 5 seconds for status checks
                    retries: 0, // Don't retry status checks
                }
            );
        } catch (error) {
            // Return disconnected status if backend is unavailable
            return { status: 'disconnected', message: 'Backend unavailable' };
        }
    },

    getTools: async (id: string) => {
        return apiClient<any[]>(`/api/v1/connectors/${id}/tools`);
    },
};
