/**
 * Enhanced fetch utilities with better timeout and error handling
 */

export class FetchError extends Error {
    constructor(
        message: string,
        public status?: number,
        public statusText?: string,
        public isTimeout = false,
        public isNetworkError = false
    ) {
        super(message);
        this.name = 'FetchError';
    }
}

interface FetchWithTimeoutOptions extends RequestInit {
    timeout?: number; // in milliseconds
    retries?: number;
    retryDelay?: number; // in milliseconds
}

/**
 * Fetch with timeout, retry logic, and better error handling
 */
export async function fetchWithTimeout<T>(
    url: string,
    options: FetchWithTimeoutOptions = {}
): Promise<T> {
    const {
        timeout = 10000, // Default 10 seconds (was 3 seconds)
        retries = 1,
        retryDelay = 1000,
        ...fetchOptions
    } = options;

    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);

        try {
            const response = await fetch(url, {
                ...fetchOptions,
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            // Check if response is ok
            if (!response.ok) {
                // Don't retry on 4xx errors
                if (response.status >= 400 && response.status < 500) {
                    throw new FetchError(
                        `HTTP ${response.status}: ${response.statusText}`,
                        response.status,
                        response.statusText
                    );
                }
                // Retry on 5xx errors
                throw new FetchError(
                    `Server error: ${response.status}`,
                    response.status,
                    response.statusText
                );
            }

            // Parse JSON response
            const data = await response.json();
            return data as T;

        } catch (error) {
            clearTimeout(timeoutId);

            // Check if it's an abort error (timeout)
            if (error instanceof Error) {
                if (error.name === 'AbortError') {
                    lastError = new FetchError(
                        `Request timeout after ${timeout}ms`,
                        undefined,
                        undefined,
                        true
                    );
                } else if (error instanceof FetchError) {
                    // Don't retry on 4xx errors
                    if (error.status && error.status >= 400 && error.status < 500) {
                        throw error;
                    }
                    lastError = error;
                } else if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
                    lastError = new FetchError(
                        'Network error: Unable to connect to server',
                        undefined,
                        undefined,
                        false,
                        true
                    );
                } else {
                    lastError = error;
                }
            } else {
                lastError = new Error('Unknown error occurred');
            }

            // If we have retries left, wait and try again
            if (attempt < retries) {
                await new Promise(resolve => setTimeout(resolve, retryDelay * (attempt + 1)));
                continue;
            }
        }
    }

    // All retries exhausted
    throw lastError || new FetchError('Failed to fetch after retries');
}

/**
 * Batch fetch multiple URLs in parallel with error handling
 */
export async function batchFetch<T>(
    urls: string[],
    options: FetchWithTimeoutOptions = {}
): Promise<{ url: string; data?: T; error?: Error }[]> {
    const promises = urls.map(async (url) => {
        try {
            const data = await fetchWithTimeout<T>(url, options);
            return { url, data };
        } catch (error) {
            return { url, error: error as Error };
        }
    });

    return Promise.all(promises);
}

/**
 * Create a debounced fetch function
 */
export function createDebouncedFetch<T>(
    fetchFn: (url: string, options?: FetchWithTimeoutOptions) => Promise<T>,
    delay = 300
) {
    let timeoutId: NodeJS.Timeout | null = null;
    let lastAbortController: AbortController | null = null;

    return (url: string, options?: FetchWithTimeoutOptions): Promise<T> => {
        return new Promise((resolve, reject) => {
            // Cancel previous timeout if exists
            if (timeoutId) {
                clearTimeout(timeoutId);
            }

            // Abort previous request if exists
            if (lastAbortController) {
                lastAbortController.abort();
            }

            lastAbortController = new AbortController();

            timeoutId = setTimeout(async () => {
                try {
                    const result = await fetchFn(url, {
                        ...options,
                        signal: lastAbortController!.signal,
                    });
                    resolve(result);
                } catch (error) {
                    if ((error as Error).name !== 'AbortError') {
                        reject(error);
                    }
                } finally {
                    timeoutId = null;
                    lastAbortController = null;
                }
            }, delay);
        });
    };
}