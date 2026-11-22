import { readFile, writeFile } from 'node:fs/promises';
import Ajv2020 from 'ajv/dist/2020.js';
import { build } from 'esbuild';
import standaloneCode from 'ajv/dist/standalone/index.js';
async function writeValidator(source, output) {
  const schema = JSON.parse(await readFile(new URL(source, import.meta.url), 'utf8'));
  const ajv = new Ajv2020({ strict: true, allowUnionTypes: true, code: { source: true, esm: true }, allErrors: false });
  const validate = ajv.compile(schema);
  const code = (await build({ stdin: { contents: standaloneCode(ajv, validate), resolveDir: new URL('../', import.meta.url).pathname }, bundle: true, format: 'esm', platform: 'neutral', write: false })).outputFiles[0].text;
  await writeFile(new URL(output, import.meta.url), code);
}
await writeValidator('../../../skills/delivery-views/references/hosted-snapshot.schema.json', '../src/generated-validator.js');
await writeValidator('../compat/v2/hosted-snapshot.schema.json', '../src/generated-validator-v2.js');
const fixtureRoot = new URL('../../../tests/fixtures/delivery-views/hosted/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', fixtureRoot), 'utf8'));
const cases = await Promise.all(manifest.cases.map(async entry => ({ ...entry, body: JSON.parse(await readFile(new URL(entry.file, fixtureRoot), 'utf8')) })));
await writeFile(new URL('../test/fixtures.generated.ts', import.meta.url), `export const cases = ${JSON.stringify(cases)};\n`);
