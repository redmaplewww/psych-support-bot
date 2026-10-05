# Implementation Plan

- [x] 1. Define account and authentication boundaries
  - Specify stable account ownership and compatibility behavior.
  - Define identity, Passkey, challenge, and session records.
  - _Requirements: 1, 2, 9, 10_

- [x] 2. Add identity persistence and migration
  - Add ORM models, indexes, uniqueness constraints, and lifecycle fields.
  - Add an Alembic migration that preserves existing account IDs.
  - _Requirements: 1, 2, 7, 9, 10_

- [x] 3. Replace long-lived bearer sessions
  - Add short-lived structured access tokens.
  - Add rotating refresh sessions, refresh, logout, and logout-all operations.
  - _Requirements: 3, 4, 5, 6_

- [x] 4. Decouple username registration from account ownership
  - Generate opaque account IDs for new registrations.
  - Preserve login compatibility for existing credential rows.
  - Add a provider verifier interface for later adapters.
  - _Requirements: 1, 2, 9_

- [x] 5. Update browser authentication lifecycle
  - Bootstrap access through the refresh cookie.
  - Remove persisted access tokens after migration.
  - _Requirements: 3, 6_

- [x] 6. Complete privacy lifecycle coverage
  - Include non-secret identity metadata in export.
  - Delete all new authentication records during account deletion.
  - _Requirements: 7, 8_

- [x] 7. Verify compatibility and security behavior
  - Add focused tests and run relevant authentication/privacy suites.
  - Run lint for changed Python modules.
  - _Requirements: 1-10_
