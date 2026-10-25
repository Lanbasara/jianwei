// Test-only: use an official DSH validator supplied by the verifier; never ship it.
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

const [indexArg, validatorArg, repoArg] = process.argv.slice(2);
assert.ok(indexArg && validatorArg && repoArg, 'Usage: check_market.mjs INDEX OFFICIAL_VALIDATOR RELEASE_REPO');
const indexPath = resolve(indexArg);
assert.ok(statSync(indexPath).size < 1024 * 1024);
const original = JSON.parse(readFileSync(indexPath, 'utf8'));
const { parseMarketIndex, marketInstallTarget } = await import(pathToFileURL(resolve(validatorArg)).href);
const url = 'https://raw.githubusercontent.com/Lanbasara/jianwei-marketplace/main/dsh/index.json';
const git = (...args) => execFileSync('git', ['-C', resolve(repoArg), ...args],
  { encoding: 'utf8', timeout: 15000, maxBuffer: 1024 * 1024 }).trim();

function validate(index) {
  const parsed = parseMarketIndex(index, url);
  assert.equal(parsed.plugins.length, 1);
  const plugin = parsed.plugins[0];
  assert.equal(plugin.name, 'jianwei');
  assert.equal(new Set(plugin.versions.map((version) => version.version)).size, plugin.versions.length);
  const rawPlugin = index.plugins.find((entry) => entry.name === plugin.name);
  assert.ok(rawPlugin && rawPlugin['dist-tags']);
  const latest = plugin.versions.find((version) => version.version === rawPlugin['dist-tags'].latest);
  assert.ok(latest, 'latest must identify one version');
  assert.equal(latest.source.path, 'plugin', 'latest must install from the canonical plugin subtree');
  for (const version of plugin.versions) {
    assert.equal(version.source.type, 'git');
    assert.equal(version.source.url, 'https://github.com/Lanbasara/jianwei.git');
    assert.match(version.source.commit, /^[0-9a-f]{40}$/);
    assert.ok(version.source.path === undefined || version.source.path === 'plugin');
    const packagePath = version.source.path === 'plugin' ? 'plugin/package.json' : 'package.json';
    const manifest = JSON.parse(git('show', version.source.commit + ':' + packagePath));
    assert.equal(version.version, manifest.version);
    assert.equal(plugin.name, manifest.name);
    assert.deepEqual(version.engines, manifest.engines);
    assert.equal(version.publishTimestamp, Number(git('show', '-s', '--format=%ct', version.source.commit)));
    assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
    const target = marketInstallTarget(plugin.name, version);
    const suffix = version.source.path === 'plugin' ? '&path:/plugin' : '';
    assert.equal(target.spec, 'git+https://github.com/Lanbasara/jianwei.git#'
      + version.source.commit + suffix);
  }
  return plugin;
}

const plugin = validate(original);
for (const version of plugin.versions) {
  const target = marketInstallTarget(plugin.name, version);
  console.log(JSON.stringify({ version: version.version, target: target.spec, commit: version.source.commit }));
}
// A tag is not an immutable Git commit in the DSH market contract.
const badPin = structuredClone(original);
badPin.plugins[0].versions[0].source.commit = 'v0.1.1';
assert.throws(() => validate(badPin));
const badPath = structuredClone(original);
badPath.plugins[0].versions[0].source.path = '../plugin';
assert.throws(() => validate(badPath));
const duplicateVersion = structuredClone(original);
duplicateVersion.plugins[0].versions.push(structuredClone(duplicateVersion.plugins[0].versions[0]));
assert.throws(() => validate(duplicateVersion));
const duplicate = structuredClone(original);
duplicate.plugins.push(structuredClone(duplicate.plugins[0]));
assert.throws(() => validate(duplicate));
console.log('PASS: official DSH index validation, mixed root/plugin history, immutable source identity, publish time and generated install target. No Desktop UI session exercised.');
