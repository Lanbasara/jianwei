import { readFileSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'jianwei-skills';
export const inject = ['skills'];

const root = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), '..'));

function readBundled(path) {
  const file = realpathSync(path);
  const rel = relative(root, file);
  if (isAbsolute(rel) || rel === '..' || rel.startsWith('..' + sep)) {
    throw new Error('Jianwei resource escapes its installed package.');
  }
  if (statSync(file).size > 262144) throw new Error('Jianwei resource is too large.');
  return readFileSync(file, 'utf8');
}

export function apply(ctx) {
  const catalog = JSON.parse(readBundled(resolve(root, 'host/catalog.json')));
  if (!Array.isArray(catalog) || catalog.length > 32) throw new Error('Invalid Jianwei skill catalog.');
  const definitions = catalog.map(entry => {
    if (!entry || typeof entry.name !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(entry.name)
        || typeof entry.description !== 'string' || !entry.description.trim() || entry.description.length > 4096) {
      throw new Error('Invalid Jianwei skill entry.');
    }
    const path = resolve(root, 'skills', entry.name, 'SKILL.md');
    const text = readBundled(path);
    const body = text.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n([\s\S]*)$/);
    if (!body) throw new Error('Jianwei skill is missing its frontmatter.');
    return {
      name: 'jianwei-' + entry.name,
      description: entry.description,
      source: 'bundled',
      invocation: { modelInvocable: true, userInvocable: true },
      content: body[1].trim(),
      path,
      resourceBase: { kind: 'directory', path: dirname(path) },
    };
  });
  if (new Set(definitions.map(d => d.name)).size !== definitions.length) throw new Error('Duplicate Jianwei skill.');
  // The native registry owns lifecycle/disposal and loads bodies only when requested.
  for (const definition of definitions) ctx.skills.register(definition);
}
