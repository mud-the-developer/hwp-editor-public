// Repeatable developer gate. Real reports/Native COM are NEVER run here.
// GUI and packaging tests use owned synthetic fixtures, not Native qualification.
import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { lint, python, root, run } from './lint.mjs';

try {
  const options = process.argv.slice(2);
  if (options.length > 1 || (options.length && !/^--shard=[01]$/.test(options[0]))) {
    throw new Error('Use no arguments for the full gate, or --shard=0 / --shard=1 for both CI jobs.');
  }
  const shard = options.length ? Number(options[0].at(-1)) : null;
  const primary = shard !== 1;
  const entries = readdirSync(join(root, 'tests'), { withFileTypes: true });
  if (entries.some(entry => entry.isDirectory() && existsSync(join(root, 'tests', entry.name, '__init__.py')))) {
    throw new Error('Nested test packages require an explicit complete-discovery update.');
  }
  const tests = entries.filter(entry => /^test_.*\.py$/.test(entry.name));
  if (!tests.length || tests.some(entry => !entry.isFile() || !/^test_[A-Za-z0-9_]*\.py$/.test(entry.name))) {
    throw new Error('Python test modules must be regular, importable files; no tests may be silently omitted.');
  }
  const modules = tests.map(entry => entry.name).sort();
  const selected = modules.filter((_, index) => shard === null || index % 2 === shard);
  console.log(`Python modules: ${selected.length}/${modules.length}; ${shard === null ? 'full gate' : `CI shard ${shard}/2; both shards required`}`);
  const checks = [
    ...(primary ? [[process.execPath, ['--test', 'tests/test_lint.mjs', 'tests/test_omp_bridge.mjs']]] : []),
    ...selected.map(name => [
      python, ['-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-p', name, '-q'], 300000,
    ]),
  ];
  let status = primary ? await lint() : 0;
  for (const [command, args, timeout] of checks) {
    if (status) break;
    if (command === python) console.log('Running bounded Python discovery:', args.join(' '));
    status = run(command, args, timeout);
  }
  process.exitCode = status;
} catch (error) { console.error(error.message); process.exitCode = 1; }
