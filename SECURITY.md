# Security Policy

## Supported versions

Security fixes are applied to the latest released version. Users should upgrade to the newest release before reporting a suspected issue.

## Reporting a vulnerability

Do not disclose security vulnerabilities in a public issue.

Use GitHub's private vulnerability-reporting form when it is available:

https://github.com/P2ppyJack/session-coord/security/advisories/new

If the form is unavailable, open a public issue containing no sensitive details and ask the maintainer to establish a private reporting channel. Include affected versions, impact, and reproduction steps only after a private channel is established.

The maintainer will acknowledge a private report, investigate it, and coordinate disclosure and a fix. Do not include real credentials, personal paths, production databases, private hostnames, or private network addresses in a report or test fixture.

## Scope

Security-sensitive areas include:

- SQLite state and migration handling
- Resource ownership, expiry, stale reaping, and forced release
- Shell quoting and installer path handling
- Managed enrollment updates and backup behavior
- Optional plugin setup/removal and exact receipt validation

The board is advisory and fail-open by design. It does not sandbox processes or enforce operating-system locks; bypass by a non-participating process is not a security boundary failure.
