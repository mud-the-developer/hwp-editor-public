import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const repo = 'mud-the-developer/hwp-editor-public';
const repository = `https://github.com/${repo}`;
const manifestName = 'RELEASE-MANIFEST.json';
const sumsName = 'SHA256SUMS.txt';
const hash = /^[0-9a-f]{64}$/;
const preview = 'historical-rc4';

export function releaseTag(version, commit) {
  assert.match(version, /^\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?$/);
  assert.match(commit, /^[0-9a-f]{40}$/);
  return `v${version}-build.${commit.slice(0, 12)}`;
}

function requireRecord(record) {
  assert.deepEqual(Object.keys(record).sort(), ['bytes', 'sha256']);
  assert.ok(Number.isSafeInteger(record.bytes) && record.bytes > 0, 'Invalid asset size');
  assert.match(record.sha256, hash);
}

export function remoteAssets(release, { tag, commit, version }) {
  assert.equal(tag, releaseTag(version, commit));
  assert.equal(release.tag_name, tag);
  assert.equal(release.target_commitish, commit);
  assert.equal(release.html_url, `${repository}/releases/tag/${tag}`);
  assert.equal(release.draft, false);
  assert.equal(release.prerelease, true);
  assert.ok(typeof release.published_at === 'string' && Number.isFinite(Date.parse(release.published_at)), 'Release not published');
  const names = ['win-x64', 'source', 'corresponding-source'].map(kind => `HWPReview-${kind}-${version}.zip`);
  names.push('SOURCE-MATERIALS.txt', manifestName, sumsName);
  assert.deepEqual(release.assets.map(asset => asset.name).sort(), names.sort(), 'Expected exactly six canonical release assets');
  return Object.fromEntries(release.assets.map(asset => {
    const { name, size, digest, state, browser_download_url } = asset;
    assert.equal(state, 'uploaded');
    assert.equal(browser_download_url, `${repository}/releases/download/${tag}/${name}`);
    assert.match(digest, /^sha256:[0-9a-f]{64}$/);
    const record = { bytes: size, sha256: digest.slice(7) };
    requireRecord(record);
    return [name, record];
  }).sort(([a], [b]) => a < b ? -1 : 1));
}

export function verifyReceipts({ tag, commit, version }, assets, manifest, sums) {
  const inventory = Object.fromEntries(Object.entries(assets).filter(([name]) => ![manifestName, sumsName].includes(name)));
  requireRecord(manifest.executable);
  assert.deepEqual(manifest, {
    contract: 'hwp-public-release-v1', repository, version, commit,
    executable: manifest.executable, assets: inventory,
    qualification: {
      native_evidence_schemas: [1, 3, 4], other_pc_tested: false, code_signed: false,
      redistribution_ready: false, hancom_layout_certified: false,
    },
  }, 'Manifest must bind the four uploaded payloads and this commit');
  const expected = Object.keys(assets).filter(name => name !== sumsName).sort()
    .map(name => `${assets[name].sha256}  ${name}\n`).join('');
  assert.equal(sums, expected, 'SHA256SUMS must cover exactly the other five uploads');
  return { historical: false, tag, commit, version, assets, executable: manifest.executable };
}

async function download(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(300_000), headers: { 'User-Agent': 'hwp-editor-docs' } });
  assert.equal(response.status, 200, `Anonymous download failed: ${url}`);
  return response;
}

// Hash uploads in flight; retain only the two small public receipt files.
export async function inspectBytes(body, { bytes, sha256 }, capture) {
  if (capture) assert.ok(bytes <= 65536, 'Oversized release receipt');
  const digest = createHash('sha256');
  const chunks = [];
  let size = 0;
  for await (const chunk of body) {
    size += chunk.length;
    assert.ok(size <= bytes, 'Oversized release asset');
    digest.update(chunk);
    if (capture) chunks.push(chunk);
  }
  assert.equal(size, bytes, 'Truncated release asset');
  assert.equal(digest.digest('hex'), sha256, 'Release asset digest mismatch');
  if (capture) return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
}

function git(...args) {
  return execFileSync('git', args, { cwd: fileURLToPath(root), encoding: 'utf8' }).trim();
}

export function verifyRefs(tag, commit, listing) {
  const head = 'refs/heads/main';
  const ref = `refs/tags/${tag}`;
  const refs = new Map(listing.split('\n').map(line => line.trim().split('\t').reverse()));
  assert.equal(refs.get(head), commit, 'Remote main advanced');
  assert.ok(refs.has(ref), 'Release tag missing');
  assert.equal(refs.get(`${ref}^{}`) ?? refs.get(ref), commit, 'Release tag points elsewhere');
}

function requireRefs(tag, commit) {
  const ref = `refs/tags/${tag}`;
  verifyRefs(tag, commit, git('ls-remote', '--exit-code', `${repository}.git`, 'refs/heads/main', ref, `${ref}^{}`));
}

async function published(env, recheck) {
  const { HWP_RELEASE_TAG: tag, HWP_RELEASE_COMMIT: commit } = env;
  assert.ok(tag && commit, 'Release tag and full commit required. For a local historical preview only, set HWP_DOCS_PREVIEW=historical-rc4.');
  const source = await readFile(new URL('scripts/hwp_resources.py', root), 'utf8');
  const matches = [...source.matchAll(/^APP_VERSION\s*=\s*"([^"]+)"\s*(?:#[^\r\n]*)?$/gm)];
  assert.equal(matches.length, 1, 'Expected one literal product APP_VERSION');
  const version = matches[0][1];
  assert.equal(tag, releaseTag(version, commit));
  assert.equal(git('rev-parse', 'HEAD'), commit, 'Checkout must be the release commit');
  assert.equal(env.GITHUB_REPOSITORY, repo);
  assert.equal(env.GITHUB_EVENT_NAME, 'workflow_dispatch');
  assert.equal(env.GITHUB_REF, 'refs/heads/main');
  assert.equal(env.GITHUB_SHA, commit);
  requireRefs(tag, commit);
  const identity = { tag, commit, version };
  const release = await (await download(`https://api.github.com/repos/${repo}/releases/tags/${tag}`)).json();
  const assets = remoteAssets(release, identity);
  const receipts = {};
  for (const name of Object.keys(assets).sort()) {
    const capture = name === manifestName || name === sumsName;
    if (recheck && !capture) continue; // The build streamed these; we rebind their reported digests below.
    const response = await download(`${repository}/releases/download/${tag}/${name}`);
    const text = await inspectBytes(response.body, assets[name], capture);
    if (capture) receipts[name] = text;
  }
  const metadata = verifyReceipts(identity, assets, JSON.parse(receipts[manifestName]), receipts[sumsName]);
  requireRefs(tag, commit);
  return metadata;
}

export async function prepare(env, recheck = false) {
  const serialize = data => JSON.stringify(data, null, 2) + '\n';
  if (env.HWP_DOCS_PREVIEW !== undefined) {
    const local = !env.CI && !env.GITHUB_ACTIONS && !env.GITHUB_EVENT_NAME;
    const previewCI = env.GITHUB_ACTIONS === 'true' && ['push', 'pull_request'].includes(env.GITHUB_EVENT_NAME);
    assert.ok((local || previewCI) && env.HWP_RELEASE_TAG === undefined && env.HWP_RELEASE_COMMIT === undefined && !recheck,
      'Historical preview cannot be used by the release workflow');
    assert.equal(env.HWP_DOCS_PREVIEW, preview);
    return readFile(new URL('../fixtures/historical-rc4.json', import.meta.url), 'utf8');
  }
  return serialize(await published(env, recheck));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const recheck = process.argv[2] === '--recheck';
  assert.equal(process.argv.length, recheck ? 3 : 2);
  const json = await prepare(process.env, recheck);
  if (recheck) {
    assert.match(process.env.HWP_PAGES_METADATA_SHA256, hash);
    assert.equal(createHash('sha256').update(json).digest('hex'), process.env.HWP_PAGES_METADATA_SHA256,
      'Release changed since the Pages artifact was built');
  } else {
    const dir = new URL('docs-site/public/', root);
    await mkdir(dir, { recursive: true });
    await writeFile(new URL('release.json', dir), json);
  }
}
