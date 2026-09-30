# Decisions, August 2026

## Auth redesign
Decided: session cookies instead of JWT. The refresh-token rotation story was
getting worse than the problem it solved, and every client we ship is a browser.
Revisit if we ever add a public API for third parties.

## Billing provider
Staying with the current provider for one more quarter. Migration cost is real,
the invoice-export bug is not blocking anyone.
