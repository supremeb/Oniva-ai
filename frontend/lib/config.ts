/**
 * Frontend Configuration
 * Feature flags and environment settings
 */

export const config = {
  // Feature Toggles
  features: {
    accuracy: process.env.NEXT_PUBLIC_ENABLE_ACCURACY === 'true',
    compliance: process.env.NEXT_PUBLIC_ENABLE_COMPLIANCE !== 'false',
    langfuse: process.env.NEXT_PUBLIC_ENABLE_LANGFUSE === 'true',
  },

  // API Configuration
  api: {
    baseUrl: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
} as const;
