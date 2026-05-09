# Security Policy

## Reporting a Vulnerability

If you believe you've found a security vulnerability in Oniva, please report it privately rather than opening a public GitHub issue.

**Email:** balachander.ral@gmail.com

Please include:

- A description of the issue and the impact you believe it has
- Steps to reproduce (proof-of-concept, exploit script, or curl reproduction)
- The affected version, commit SHA, or deployment configuration
- Any suggested mitigation, if you have one

I aim to acknowledge reports within 72 hours and to provide a status update within 7 days.

## Scope

In scope:

- The Oniva backend (`backend/`) — auth, RLS, signed audit log, connector sandbox
- The Oniva frontend (`frontend/`) — XSS, CSRF, auth state handling
- Database migrations (`backend/migrations/`) — RLS bypass, privilege escalation
- Supplied Docker / Kubernetes / Terraform manifests

Out of scope:

- Third-party dependencies (please report upstream)
- Self-hosted deployments where the operator has modified the code or configuration
- Findings that require local-only access already granted to the user

## Disclosure

I follow coordinated disclosure. Once a fix is available, I'll credit reporters who request acknowledgement.
