// The one Node-named API the core uses. Both the Workers runtime (nodejs_compat) and Node provide it; the core's
// tsconfig loads no Node types, so this is all it may assume of the module.
declare module 'node:crypto' {
  export function timingSafeEqual(a: ArrayBufferView, b: ArrayBufferView): boolean;
}
