// Kept here rather than read from the suite, so a scenario dropped from it, or a slow tier that never ran, is red.
export const FAST = ["round-trip.first-repository", "round-trip.second-repository", "host-headers-ignored", "exact-retry",
  "sequence-rules", "writer-fencing", "repository-scope", "errors.400", "errors.401", "errors.404", "errors.405", "errors.409",
  "errors.413", "errors.415", "errors.422", "projection-fixtures", "streamed-body-limit", "refused-requests",
  "no-redirect-listing-or-history", "anonymous-read", "security-headers"];
export const SLOW = ["slow.lease-expiry", "slow.resume", "slow.takeover", "slow.staleness"];
