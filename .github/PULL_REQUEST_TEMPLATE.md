<!-- Thanks for contributing! Please fill in the sections below. -->

## What

Briefly describe the change in 1-3 sentences.

## Why

What problem does this solve, or what capability does it add? Link any relevant issue (`Fixes #123`).

## How

Highlight the approach. Call out anything non-obvious — a tricky migration, a new dependency, a behavior change behind a feature flag.

## Testing

- [ ] Backend: `cd backend && pytest` passes
- [ ] Frontend: `cd frontend && npm test` passes
- [ ] Manual verification (describe what you exercised in the UI)
- [ ] New tests added for new behavior

## Screenshots / recording

(For UI changes — drop a screenshot or short clip.)

## Checklist

- [ ] No new TypeScript / Python warnings
- [ ] Schema changes use `alembic revision --autogenerate`
- [ ] Documentation updated if behavior is user-visible
- [ ] No secrets, API keys, or personal data committed
