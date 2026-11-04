// Local source checks only. Never opens a document or calls approval/native apply.
import { ESLint } from 'eslint';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

export function browserScript(html) {
  // Keep original HTML line/column offsets; never execute extracted document code.
  let masked = html.replace(/[^\r\n]/g, ' '), count = 0;
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
    const attributes = match[1];
    const type = /\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(attributes);
    const kind = (type?.[1] ?? type?.[2] ?? type?.[3] ?? '').toLowerCase();
    if (kind === 'application/json' || kind === 'application/ld+json') continue;
    if (kind && kind !== 'text/javascript' && kind !== 'application/javascript') {
      throw new Error('Unsupported review script type; update lint coverage explicitly: ' + kind);
    }
    if (/\bsrc\s*=/i.test(attributes)) throw new Error('External review scripts need explicit local lint coverage.');
    const start = match.index + match[0].indexOf('>') + 1;
    masked = masked.slice(0, start) + match[2] + masked.slice(start + match[2].length);
    count++;
  }
  if (!count) throw new Error('No executable review script found; cannot claim browser lint passed.');
  return masked;
}

export function run(command, args, timeout = 180000) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', windowsHide: true, timeout });
  if (result.error) { console.error(result.error.message); return 1; }
  return result.status ?? 1;
}

export async function lint() {
  if (!existsSync(python)) throw new Error('Create the project .venv and install requirements-dev.txt first.');
  if (run(python, ['-X', 'utf8', '-m', 'ruff', 'check', 'scripts', 'tests'])) return 1;
  if (run(python, ['-X', 'utf8', 'scripts/hwp_safety_lint.py', 'scripts', 'tests'])) return 1;
  const engine = new ESLint({ cwd: root, overrideConfigFile: join(root, 'eslint.config.mjs') });
  const results = await engine.lintFiles(['eslint.config.mjs', 'scripts/**/*.mjs', 'tests/**/*.mjs', 'native/HWPReview.Native/TerminalAssets/terminal.js', 'native/HWPReview.Native/ViewerAssets/*.js']);
  for (const name of ['review', 'fill-review', 'template-review', 'report-workspace']) {
    results.push(...await engine.lintText(browserScript(await readFile(join(root, `web/${name}.html`), 'utf8')),
      { filePath: join(root, `web/${name}.inline.js`) }));
  }
  const formatter = await engine.loadFormatter('stylish');
  const output = formatter.format(results).replaceAll('.inline.js', '.html');
  if (output) console.log(output);
  if (results.some(result => result.errorCount || result.warningCount)) return 1;
  console.log('PASS: Python correctness + Node/browser JavaScript + review DOM safety lint');
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await lint(); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
