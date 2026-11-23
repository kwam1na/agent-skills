// The portable server's entry point, bundled to build/node/server.mjs.
// Node 22 marks node:sqlite experimental and says so once on stderr; this server takes that as read and says nothing.
const emitWarning = process.emitWarning;
process.emitWarning = ((warning: string | Error, ...rest: unknown[]) => {
  const text = typeof warning === 'string' ? warning : warning.message;
  const type = typeof rest[0] === 'string' ? rest[0] : (rest[0] as { type?: string } | undefined)?.type;
  if (type === 'ExperimentalWarning' && /SQLite/.test(text)) return;
  return (emitWarning as (...args: unknown[]) => void).call(process, warning, ...rest);
}) as typeof process.emitWarning;

const { main } = await import('./main');
process.exitCode = await main(process.argv.slice(2), process.env);
export {};
