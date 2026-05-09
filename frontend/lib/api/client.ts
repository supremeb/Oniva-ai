const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface FetchOptions extends RequestInit {
    params?: Record<string, string>;
}

export async function apiClient<T>(
    endpoint: string,
    options: FetchOptions = {}
): Promise<T> {
    const { params, ...fetchOptions } = options;

    let url = `${API_BASE_URL}${endpoint}`;
    if (params) {
        url += '?' + new URLSearchParams(params).toString();
    }

    const response = await fetch(url, {
        ...fetchOptions,
        headers: {
            'Content-Type': 'application/json',
            ...fetchOptions.headers,
        },
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
}

export function createSSEConnection(
    endpoint: string,
    onMessage: (data: unknown) => void,
    onError?: (error: Error) => void
): () => void {
    const url = `${API_BASE_URL}${endpoint}`;
    const eventSource = new EventSource(url);

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            onMessage(data);
        } catch {
            onMessage(event.data);
        }
    };

    eventSource.onerror = () => {
        onError?.(new Error('SSE connection error'));
        eventSource.close();
    };

    return () => eventSource.close();
}
