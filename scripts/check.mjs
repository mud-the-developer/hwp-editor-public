// Repeatable developer gate. Real reports/Native COM are NEVER run here.
// Continuous GUI uses owned synthetic files and test consent only; Native mocked.
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { lint, python, root, run } from './lint.mjs';

try {
  // Exhaustive, disjoint discovery batches keep the same per-process deadline.
  const modules = readdirSync(join(root, 'tests')).filter(name => /^test_.*\.py$/.test(name));
  if (!modules.length || modules.includes('test_.py')) throw new Error('Python test discovery cannot be partitioned safely.');
  const firstCount = modules.filter(name => name[5] >= 'a' && name[5] <= 'p').length;
  console.log(`Python regression modules: ${firstCount} + ${modules.length - firstCount} = ${modules.length}; none excluded`);
  const pythonChecks = ['test_[a-p]*.py', 'test_[!a-p]*.py'].map(pattern => [
    python, ['-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-q'], 300000,
  ]);
  const checks = [
    [process.execPath, ['--test', 'tests/test_lint.mjs', 'tests/test_review_ready.mjs', 'tests/test_omp_bridge.mjs']],
    ...pythonChecks,
    [process.execPath, ['tests/test_review_ui.mjs']],
    [process.execPath, ['tests/test_fill_ui.mjs']],
    [process.execPath, ['tests/test_gui_workflow.mjs']],
  ];
  let status = await lint();
  for (const [command, args, timeout] of checks) {
    if (status) break;
    if (command === python) console.log('Running bounded Python discovery:', args.join(' '));
    status = run(command, args, timeout);
  }
  process.exitCode = status;
} catch (error) { console.error(error.message); process.exitCode = 1; }
