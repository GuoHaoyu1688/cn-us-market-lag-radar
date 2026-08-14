# Privacy Design

The public repository was extracted through an allowlist. Only source code, tests, static UI files, documentation, and deterministic synthetic demo data are included.

Excluded categories include brokerage exports, account identifiers, positions, transactions, private reports, browser state, connector caches, API values, user names, absolute home-directory paths, logs, historical dashboard snapshots, and real forward ledgers.

## Controls

- `.env` and runtime output are ignored by Git.
- The open-source builder disables personal-account artifact discovery.
- The server binds to localhost.
- `scripts/sanitize_check.py` rejects known sensitive paths, absolute macOS user paths, private keys, common tokens, brokerage account identifiers, and nonempty secret assignments.
- CI runs the same sanitization check on every push and pull request.

No automated scanner can prove the absence of all personal data. Review staged files before every release.
