// The versions and bounds this suite holds a service to. They move with the spec, hosted/protocol/hosted-delivery-api-1.md,
// and with every server: a change to any of them changes all three in the same change.
export const API_VERSION = "hosted-delivery-api/1";
export const SNAPSHOT_VERSION = "hosted-delivery-snapshot/3";
export const RENDERER_VERSION = "delivery-line-map/3";
export const LEGACY_SNAPSHOT_VERSION = "hosted-delivery-snapshot/2";
export const LEGACY_RENDERER_VERSION = "delivery-line-map/2";
export const LEASE_MS = 180_000;
export const STALE_MS = 120_000;
export const MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024;
export const MAX_SESSION_BYTES = 2048;
export const MAX_CONFIRM_BYTES = 1024;
export const MAX_NESTING = 64;
