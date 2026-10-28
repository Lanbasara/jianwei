import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2]);
const plugin = await import(pathToFileURL(resolve(root, 'host/dsh.mjs')).href);
assert.deepEqual(plugin.inject, ['skills']);
const definitions = [];
plugin.apply({ skills: { register: definition => definitions.push(definition) } });
assert.deepEqual(definitions.map(d => d.name), [
  'jianwei-setup',
  'jianwei-tanyuan',
  'jianwei-research',
  'jianwei-portfolio',
  'jianwei-advise',
  'jianwei-reflect',
  'jianwei-report',
  'jianwei-research-then-report',
]);
for (const definition of definitions) {
  const source = readFileSync(definition.path, 'utf8');
  assert.equal(definition.content, source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim());
  assert.equal(definition.resourceBase.path, dirname(definition.path));
  assert.deepEqual(definition.invocation, { modelInvocable: true, userInvocable: true });
  assert.equal(definition.source, 'bundled');
}
console.log('PASS: DSH registration contract, names, original bodies and resource paths (no host/model session run).');
