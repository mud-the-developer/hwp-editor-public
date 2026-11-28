import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { inspectBytes, prepare, releaseTag, remoteAssets, verifyReceipts, verifyRefs } from './release.mjs';

const repository = 'https://github.com/mud-the-developer/hwp-editor-public';
const commit = 'b'.repeat(40);
const version = '1.2.3-rc.5';
const tag = `v${version}-build.${commit.slice(0, 12)}`;
const identity = { tag, version, commit };
const sha256 = body => createHash('sha256').update(body).digest('hex');
const record = text => ({ bytes: Buffer.byteLength(text), sha256: sha256(text) });

function releaseData() {
  const names = ['win-x64', 'source', 'corresponding-source'].map(kind => `HWPReview-${kind}-${version}.zip`);
  names.push('SOURCE-MATERIALS.txt');
  const payloads = Object.fromEntries(names.map(name => [name, record(name)]));
  const manifest = {
    contract: 'hwp-public-release-v1', repository, version, commit, executable: record('exe'), assets: payloads,
    qualification: { native_evidence_schemas: [1, 3, 4], other_pc_tested: false, code_signed: false,
      redistribution_ready: false, hancom_layout_certified: false },
  };
  const assets = { ...payloads, 'RELEASE-MANIFEST.json': record(JSON.stringify(manifest)) };
  const sums = Object.keys(assets).sort().map(name => `${assets[name].sha256}  ${name}\n`).join('');
  assets['SHA256SUMS.txt'] = record(sums);
  const release = { html_url: `${repository}/releases/tag/${tag}`, draft: false, prerelease: true,
    tag_name: tag, target_commitish: commit, published_at: '2026-10-01T01:00:00Z',
    assets: Object.entries(assets).map(([name, { bytes, sha256 }]) => ({ name, state: 'uploaded', size: bytes,
      digest: `sha256:${sha256}`, browser_download_url: `${repository}/releases/download/${tag}/${name}` })),
  };
  return { release, manifest, assets, sums };
}

test('continuous tags use the product version and exactly twelve commit digits, not the ZIP version', () => {
  assert.equal(releaseTag(version, commit), tag);
  assert.throws(() => releaseTag(version, commit.slice(0, 12)));
  assert.throws(() => releaseTag('../other', commit));
  const { release, assets, manifest, sums } = releaseData();
  assert.deepEqual(remoteAssets(release, identity), assets);
  assert.deepEqual(verifyReceipts(identity, assets, manifest, sums), {
    historical: false, ...identity, assets, executable: manifest.executable,
  });
  assert.throws(() => remoteAssets(release, { ...identity, tag: `v${version}` }));
});

test('both remote refs must bind, including the peeled commit for an annotated tag', () => {
  const row = (id, ref) => `${id}\trefs/${ref}`;
  const branch = row(commit, 'heads/main');
  const ref = `tags/${tag}`;
  const other = 'a'.repeat(40);
  verifyRefs(tag, commit, [branch, row(commit, ref)].join('\n'));
  verifyRefs(tag, commit, [branch, row(other, ref), row(commit, `${ref}^{}`)].join('\n'));
  for (const rows of [[row(other, 'heads/main'), row(commit, ref)], [branch], [branch, row(other, ref)],
    [branch, row(commit, ref), row(other, `${ref}^{}`)]]) {
    assert.throws(() => verifyRefs(tag, commit, rows.join('\n')));
  }
});

test('drafts, stale identity and any deviation from the six named uploads are rejected', () => {
  for (const change of [r => { r.draft = true; }, r => { r.prerelease = false; }, r => { r.published_at = null; },
    r => { r.target_commitish = 'main'; }, r => { r.tag_name = `v${version}`; }, r => { r.html_url += 'x'; },
    r => r.assets.pop(), r => r.assets.push(r.assets[0]), r => { r.assets[0].name = 'BUILD.json'; },
    r => { r.assets[0].digest = null; }, r => { r.assets[0].state = 'open'; }, r => { r.assets[0].size = 0; },
    r => { r.assets[0].browser_download_url = 'https://example.org/file'; }]) {
    const { release } = releaseData();
    change(release);
    assert.throws(() => remoteAssets(release, identity));
  }
});

test('manifest and exact checksum inventory cannot substitute a receipt, product or asset', () => {
  for (const change of [m => { m.version += '.1'; }, m => { m.commit = 'a'.repeat(40); },
    m => { m.repository += '/fork'; }, m => { m.private_path = 'unexpected'; },
    m => { m.assets['SOURCE-MATERIALS.txt'].bytes++; }, m => { m.executable.sha256 = 'missing'; },
    m => { m.qualification.native_evidence_schemas = [1, 3]; }, m => { m.qualification.code_signed = true; }]) {
    const { manifest, assets, sums } = releaseData();
    const changed = structuredClone(manifest);
    change(changed);
    assert.throws(() => verifyReceipts(identity, assets, changed, sums));
  }
  const { manifest, assets, sums } = releaseData();
  for (const changed of [sums.trimEnd(), sums + sums, sums.replace('SOURCE-MATERIALS', 'source-materials'), sums.slice(1)]) {
    assert.throws(() => verifyReceipts(identity, assets, manifest, changed));
  }
});

test('downloaded bytes must match reported digest and size, including both receipt files', async () => {
  const chunks = [Buffer.from('ab'), Buffer.from('c')];
  assert.equal(await inspectBytes(chunks, record('abc'), true), 'abc');
  assert.equal(await inspectBytes(chunks, record('abc'), false), undefined);
  for (const text of ['abcd', 'ab', 'acb']) await assert.rejects(inspectBytes(chunks, record(text), false));
  await assert.rejects(inspectBytes([], { ...record('x'), bytes: 65537 }, true));
});

test('offline historical data requires explicit preview and cannot satisfy a release dispatch', async () => {
  const env = { HWP_DOCS_PREVIEW: 'historical-rc4' };
  const fixture = JSON.parse(await prepare(env));
  assert.equal(fixture.historical, true);
  assert.equal(fixture.tag, 'v1.0.0-rc.4');
  assert.equal(Object.keys(fixture.assets).length, 6);
  assert.equal(fixture.assets['HWPReview-win-x64-1.0.0-rc.4.zip'].sha256,
    '7bfd3ac56fa5a2c5e314c8ae656f126ab253a39f8705e2217db50bd5f5512f6e');
  for (const event of ['push', 'pull_request']) {
    assert.deepEqual(JSON.parse(await prepare({ ...env, CI: 'true', GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: event })), fixture);
  }
  for (const extra of [{ CI: 'true' }, { HWP_RELEASE_TAG: tag }, { HWP_RELEASE_COMMIT: commit },
    { GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: 'workflow_dispatch' }]) await assert.rejects(prepare({ ...env, ...extra }));
  await assert.rejects(prepare(env, true));
  await assert.rejects(prepare({ GITHUB_EVENT_NAME: 'workflow_dispatch' }));
});
