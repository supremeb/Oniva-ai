'use client';

import { Suspense, useState, useEffect, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuthStore } from '@/lib/stores/auth';
import {
  checkEmail as checkEmailApi,
  register,
  checkSlug as checkSlugApi,
  type EmailCheckResponse,
  type SlugCheckResponse,
} from '@/lib/api/onboarding';
import { Check } from '@phosphor-icons/react';

type Step = 'email' | 'profile' | 'organization';

function SignupContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setAuth, isAuthenticated } = useAuthStore();

  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [name, setName] = useState('');
  const [orgName, setOrgName] = useState('');
  const [orgSlug, setOrgSlug] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // Email check state
  const [emailCheck, setEmailCheck] = useState<EmailCheckResponse | null>(null);

  // Slug check state
  const [slugCheck, setSlugCheck] = useState<SlugCheckResponse | null>(null);
  const [slugCheckLoading, setSlugCheckLoading] = useState(false);

  // Pre-fill email from query param
  useEffect(() => {
    const emailParam = searchParams.get('email');
    if (emailParam) {
      setEmail(emailParam);
    }
  }, [searchParams]);

  // Redirect if already authenticated
  useEffect(() => {
    if (isAuthenticated) {
      router.replace('/');
    }
  }, [isAuthenticated, router]);

  // Debounced slug check
  const checkSlugDebounced = useCallback(async (slug: string) => {
    if (!slug || slug.length < 2) {
      setSlugCheck(null);
      return;
    }

    setSlugCheckLoading(true);
    try {
      const response = await checkSlugApi(slug);
      setSlugCheck(response);
    } catch {
      setSlugCheck(null);
    } finally {
      setSlugCheckLoading(false);
    }
  }, []);

  // Auto-generate slug from org name
  useEffect(() => {
    if (orgName && !orgSlug) {
      const generatedSlug = orgName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
      setOrgSlug(generatedSlug);
    }
  }, [orgName, orgSlug]);

  // Check slug availability when slug changes
  useEffect(() => {
    const timer = setTimeout(() => {
      if (orgSlug) {
        checkSlugDebounced(orgSlug);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [orgSlug, checkSlugDebounced]);

  // Validate password requirements
  const validatePassword = (pwd: string): string | null => {
    if (pwd.length < 8) {
      return 'Password must be at least 8 characters';
    }
    if (!/[A-Z]/.test(pwd)) {
      return 'Password must contain at least one uppercase letter';
    }
    if (!/[a-z]/.test(pwd)) {
      return 'Password must contain at least one lowercase letter';
    }
    if (!/[0-9]/.test(pwd)) {
      return 'Password must contain at least one number';
    }
    if (!/[@$!%*?&]/.test(pwd)) {
      return 'Password must contain at least one special character (@$!%*?&)';
    }
    return null;
  };

  const handleCheckEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const response = await checkEmailApi(email);
      setEmailCheck(response);

      if (!response.available) {
        setError('This email is already registered. Please sign in instead.');
        return;
      }

      // Move to profile step
      setStep('profile');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check email');
    } finally {
      setIsLoading(false);
    }
  };

  const handleProfileStep = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    // Validate password
    const passwordError = validatePassword(password);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    // Check passwords match
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    // If invited, complete registration immediately
    if (emailCheck?.has_pending_invitation) {
      await handleCompleteRegistration();
    } else {
      // Self-service: need organization details
      setStep('organization');
    }
  };

  const handleCompleteRegistration = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const response = await register({
        email,
        password,
        name,
        org_name: emailCheck?.has_pending_invitation ? undefined : orgName,
        org_slug: emailCheck?.has_pending_invitation ? undefined : orgSlug,
      });

      // Save auth state (tokens are in httpOnly cookies set by backend)
      setAuth({
        id: response.user.id,
        email: response.user.email,
        name: response.user.name || '',
        role: response.user.role,
        tenant_id: response.tenant.id,
        tenant_slug: response.tenant.slug,
        groups: [],
        is_platform_operator: false,
        features: {},
      });

      // Redirect to dashboard
      router.replace('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setIsLoading(false);
    }
  };

  const handleBackToEmail = () => {
    setStep('email');
    setPassword('');
    setConfirmPassword('');
    setName('');
    setOrgName('');
    setOrgSlug('');
    setError('');
    setEmailCheck(null);
    setSlugCheck(null);
  };

  const handleBackToProfile = () => {
    setStep('profile');
    setOrgName('');
    setOrgSlug('');
    setError('');
    setSlugCheck(null);
  };

  // Progress indicator
  const totalSteps = emailCheck?.has_pending_invitation ? 2 : 3;
  const currentStep = step === 'email' ? 1 : step === 'profile' ? 2 : 3;

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-muted/20 p-4">
      <div className="w-full max-w-md space-y-8">
        {/* Logo and Title */}
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-4">
            <svg
              className="w-8 h-8 text-primary"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 10V3L4 14h7v7l9-11h-7z"
              />
            </svg>
          </div>
          <h1 className="text-2xl font-bold">Create Account</h1>
          <p className="text-muted-foreground">
            {step === 'email' && 'Enter your email to get started'}
            {step === 'profile' && (emailCheck?.has_pending_invitation
              ? `Join ${emailCheck.org_name}`
              : 'Create your profile'
            )}
            {step === 'organization' && 'Set up your organization'}
          </p>
        </div>

        {/* Progress Bar */}
        <div
          role="progressbar"
          aria-valuenow={currentStep}
          aria-valuemin={1}
          aria-valuemax={totalSteps}
          aria-label="Signup progress"
          className="flex gap-2"
        >
          {Array.from({ length: totalSteps }).map((_, i) => (
            <div
              key={i}
              className={`h-1 flex-1 rounded-full transition-colors ${
                i < currentStep ? 'bg-primary' : 'bg-muted'
              }`}
            />
          ))}
        </div>

        {/* Form Card */}
        <div className="bg-card border rounded-xl p-6 shadow-sm space-y-6">
          {step === 'email' && (
            // Step 1: Check Email
            <form onSubmit={handleCheckEmail} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email Address</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  disabled={isLoading}
                  autoFocus
                />
                <p className="text-xs text-muted-foreground">
                  Create a new organization or join one you&apos;ve been invited to.
                </p>
              </div>

              {error && (
                <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-lg">
                  {error}
                </div>
              )}

              <Button type="submit" className="w-full" disabled={isLoading || !email}>
                {isLoading ? 'Checking...' : 'Continue'}
              </Button>
            </form>
          )}

          {step === 'profile' && (
            // Step 2: Profile Info
            <form onSubmit={handleProfileStep} className="space-y-4">
              {/* Invitation Banner */}
              {emailCheck?.has_pending_invitation && (
                <div className="p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg">
                  <p className="text-sm text-green-700 dark:text-green-300">
                    You&apos;ve been invited to join <strong>{emailCheck.org_name}</strong>
                  </p>
                </div>
              )}

              {/* Email (read-only) */}
              <div className="space-y-2">
                <Label htmlFor="signup-email">Email</Label>
                <div className="flex items-center gap-2">
                  <Input id="signup-email" type="email" value={email} disabled className="bg-muted" />
                  <Button type="button" variant="ghost" size="sm" onClick={handleBackToEmail}>
                    Change
                  </Button>
                </div>
              </div>

              {/* Name */}
              <div className="space-y-2">
                <Label htmlFor="name">Full Name</Label>
                <Input
                  id="name"
                  type="text"
                  placeholder="John Doe"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  disabled={isLoading}
                  autoFocus
                />
              </div>

              {/* Password */}
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="Create a strong password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  disabled={isLoading}
                />
                <ul className="text-xs space-y-1" aria-label="Password requirements">
                  {[
                    { met: password.length >= 8, label: 'At least 8 characters' },
                    { met: /[A-Z]/.test(password), label: 'One uppercase letter' },
                    { met: /[a-z]/.test(password), label: 'One lowercase letter' },
                    { met: /[0-9]/.test(password), label: 'One number' },
                    { met: /[@$!%*?&]/.test(password), label: 'One special character (@$!%*?&)' },
                  ].map(({ met, label }) => (
                    <li key={label} className={`flex items-center gap-1.5 ${met ? 'text-green-600' : 'text-muted-foreground'}`}>
                      <Check className={`h-3 w-3 shrink-0 ${met ? 'opacity-100' : 'opacity-0'}`} aria-hidden="true" />
                      <span>{label}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Confirm Password */}
              <div className="space-y-2">
                <Label htmlFor="confirm-password">Confirm Password</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  placeholder="Confirm your password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  disabled={isLoading}
                />
                {confirmPassword && password !== confirmPassword && (
                  <p className="text-xs text-destructive">Passwords do not match</p>
                )}
              </div>

              {error && (
                <div role="alert" className="text-sm text-destructive bg-destructive/10 p-3 rounded-lg">{error}</div>
              )}

              <Button
                type="submit"
                className="w-full"
                disabled={isLoading || !name || !password || !confirmPassword}
              >
                {isLoading
                  ? 'Creating Account...'
                  : emailCheck?.has_pending_invitation
                  ? 'Join Organization'
                  : 'Continue'
                }
              </Button>
            </form>
          )}

          {step === 'organization' && (
            // Step 3: Organization Setup (self-service only)
            <form onSubmit={handleCompleteRegistration} className="space-y-4">
              <p className="text-sm text-muted-foreground">
                You&apos;ll be the owner of this organization and can invite others.
              </p>

              {/* Organization Name */}
              <div className="space-y-2">
                <Label htmlFor="org-name">Organization Name</Label>
                <Input
                  id="org-name"
                  type="text"
                  placeholder="Acme Inc."
                  value={orgName}
                  onChange={(e) => {
                    setOrgName(e.target.value);
                    // Reset slug to regenerate
                    if (!orgSlug || orgSlug === orgName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')) {
                      setOrgSlug('');
                    }
                  }}
                  required
                  disabled={isLoading}
                  autoFocus
                />
              </div>

              {/* Organization Slug */}
              <div className="space-y-2">
                <Label htmlFor="org-slug">Organization URL</Label>
                <div className="flex items-center">
                  <span className="px-3 py-2 bg-muted border border-r-0 rounded-l-md text-sm text-muted-foreground">
                    oniva.ai/
                  </span>
                  <Input
                    id="org-slug"
                    type="text"
                    placeholder="acme"
                    value={orgSlug}
                    onChange={(e) => setOrgSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                    required
                    disabled={isLoading}
                    className="rounded-l-none"
                  />
                </div>
                {slugCheckLoading && (
                  <p className="text-xs text-muted-foreground">Checking availability...</p>
                )}
                {slugCheck && !slugCheckLoading && (
                  slugCheck.available ? (
                    <p className="text-xs text-green-600">✓ Available</p>
                  ) : (
                    <div className="text-xs text-destructive">
                      <p>✗ Not available</p>
                      {slugCheck.suggestions.length > 0 && (
                        <p className="mt-1">
                          Try:{' '}
                          {slugCheck.suggestions.map((s, i) => (
                            <button
                              key={s}
                              type="button"
                              onClick={() => setOrgSlug(s)}
                              className="text-primary hover:underline"
                            >
                              {s}
                              {i < slugCheck.suggestions.length - 1 ? ', ' : ''}
                            </button>
                          ))}
                        </p>
                      )}
                    </div>
                  )
                )}
              </div>

              {error && (
                <div role="alert" className="text-sm text-destructive bg-destructive/10 p-3 rounded-lg">{error}</div>
              )}

              <div className="flex gap-2">
                <Button type="button" variant="outline" onClick={handleBackToProfile} disabled={isLoading}>
                  Back
                </Button>
                <Button
                  type="submit"
                  className="flex-1"
                  disabled={isLoading || !orgName || !orgSlug || !!(slugCheck && !slugCheck.available)}
                >
                  {isLoading ? 'Creating...' : 'Create Organization'}
                </Button>
              </div>
            </form>
          )}

          {/* Divider */}
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-card px-2 text-muted-foreground">Already have an account?</span>
            </div>
          </div>

          {/* Login Link */}
          <Link href="/login" className="block">
            <Button variant="outline" className="w-full">
              Sign In
            </Button>
          </Link>
        </div>

        {/* Footer */}
        <p className="text-center text-sm text-muted-foreground">
          {emailCheck?.has_pending_invitation
            ? 'Contact your organization admin if you have questions'
            : 'Create your own organization or ask your admin for an invite'
          }
        </p>
      </div>
    </div>
  );
}

export default function SignupPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center">
          <div className="animate-pulse">Loading...</div>
        </div>
      }
    >
      <SignupContent />
    </Suspense>
  );
}
