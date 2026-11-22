// Snapshot object keys start with a 64-hex delivery ID.
export const REVOKED = 'revoked';
export function publisherKey(repository) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(repository)) {
    throw new Error('Invalid publisher repository ID');
  }
  return '_publishers/v1/' + repository;
}
