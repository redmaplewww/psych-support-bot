# Identity Foundation Requirements

## Problem and scope

The current authentication flow uses the username as both the login handle and
the primary key for all user data. This makes account continuity depend on one
login method and prevents safe addition of Apple, Google, Huawei, and Passkey
identities. This phase introduces a provider-neutral account core while keeping
the existing username/password and guest flows operational.

Provider SDK UI and production Passkey ceremonies are outside this phase because
their domains, bundle/package identifiers, and provider credentials are not yet
available.

## User stories

1. As a user, I can keep using the current product without losing existing data.
2. As a user, my product account remains the same when another login method is
   linked later.
3. As a user, I can refresh or end a web session without storing a long-lived
   bearer token in browser storage.
4. As an operator, I can add provider-specific verifiers without changing data
   ownership throughout the product.
5. As a user, account export and deletion cover authentication identities and
   sessions as well as product content.

## Acceptance criteria

1. When a new username account is registered, the system shall create an opaque
   internal account ID that is independent of the username.
2. While a legacy account exists, when its owner logs in, the system shall keep
   its existing immutable user ID and all associated data.
3. When a login or registration succeeds, the system shall issue a short-lived
   access token and create a rotating, server-revocable refresh session.
4. When a refresh token is used successfully, the system shall revoke the old
   refresh credential and issue a replacement.
5. When a revoked, expired, malformed, or replayed refresh token is submitted,
   the system shall reject it without exposing whether an account exists.
6. When the browser flow receives a refresh credential, the system shall store
   it in an HttpOnly cookie and shall not return it in the JSON response.
7. When account deletion runs, the system shall delete provider identities,
   Passkey credentials, challenges, refresh sessions, password credentials, and
   all existing product data owned by the account.
8. When account export runs, the system shall include non-secret identity
   metadata and shall exclude password hashes, token hashes, public keys,
   challenges, and provider tokens.
9. When a future identity provider is implemented, the provider adapter shall
   return a verified issuer and subject; business data shall never use the
   provider subject, username, or email as its owner key.
10. While the RP domain is not configured, the system shall keep Passkey
    authentication disabled rather than registering credentials against an
    unstable domain.
