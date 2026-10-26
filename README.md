# Discovery snapshot contract fix

This package aligns the persistence test and repository with the established
`DiscoverySnapshot` constructor (`provider_name`, derived `returned_count`).
It also persists `effective_requested_limit`, so snapshots created by providers
that omit an explicit requested limit remain valid.
