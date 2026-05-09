'use client';

import { useEffect, useState } from 'react';
import { CircleNotch, CheckCircle, XCircle } from '@phosphor-icons/react';

/**
 * OAuth Callback Page
 *
 * This page handles OAuth redirects from providers.
 * It extracts the authorization code and state, then sends
 * them back to the parent window (popup opener).
 *
 * Route: /oauth/callback
 */
export default function OAuthCallbackPage() {
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [message, setMessage] = useState('Processing authorization...');

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');
    const error = params.get('error');
    const errorDescription = params.get('error_description');

    // Check for errors from OAuth provider
    if (error) {
      setStatus('error');
      setMessage(errorDescription || error || 'Authorization failed');

      // Still send message to parent so it can clean up
      if (window.opener) {
        window.opener.postMessage(
          {
            type: 'oauth_callback',
            error: errorDescription || error,
            state,
          },
          window.location.origin
        );
      }

      // Auto-close after showing error
      setTimeout(() => {
        window.close();
      }, 3000);

      return;
    }

    // Check for required params
    if (!code || !state) {
      setStatus('error');
      setMessage('Missing authorization code or state');

      if (window.opener) {
        window.opener.postMessage(
          {
            type: 'oauth_callback',
            error: 'Missing authorization code or state',
            state,
          },
          window.location.origin
        );
      }

      setTimeout(() => {
        window.close();
      }, 3000);

      return;
    }

    // Send success message to parent window
    if (window.opener) {
      window.opener.postMessage(
        {
          type: 'oauth_callback',
          code,
          state,
        },
        window.location.origin
      );

      setStatus('success');
      setMessage('Authorization successful! This window will close automatically.');

      // Close window after brief success message
      setTimeout(() => {
        window.close();
      }, 1500);
    } else {
      // No parent window - show manual close message
      setStatus('error');
      setMessage('No parent window found. Please close this window and try again.');
    }
  }, []);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background p-8">
      <div className="text-center max-w-md">
        {status === 'processing' && (
          <>
            <CircleNotch className="h-12 w-12 text-primary mx-auto animate-spin" />
            <h1 className="text-xl font-semibold mt-4">Processing</h1>
          </>
        )}

        {status === 'success' && (
          <>
            <CheckCircle className="h-12 w-12 text-green-500 mx-auto" weight="fill" />
            <h1 className="text-xl font-semibold mt-4 text-green-700">Success</h1>
          </>
        )}

        {status === 'error' && (
          <>
            <XCircle className="h-12 w-12 text-red-500 mx-auto" weight="fill" />
            <h1 className="text-xl font-semibold mt-4 text-red-700">Error</h1>
          </>
        )}

        <p className="text-muted-foreground mt-2">{message}</p>

        {status !== 'processing' && (
          <button
            onClick={() => window.close()}
            className="mt-4 text-sm text-primary hover:underline"
          >
            Close this window
          </button>
        )}
      </div>
    </div>
  );
}
