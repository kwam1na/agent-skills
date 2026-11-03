export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@agent-delivery-harness/kernel") return { url: new URL("./kernel.mjs", import.meta.url).href, shortCircuit: true };
  return nextResolve(specifier, context);
}
