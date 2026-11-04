// Repeatable developer gate. Real reports/Native COM are NEVER run here.
// Continuous GUI uses owned synthetic files and test consent only; Native mocked.
import { lint, python, run } from './lint.mjs';

try {
  const checks = [
    [process.execPath, ['--test', 'tests/test_lint.mjs', 'tests/test_review_ready.mjs', 'tests/test_omp_bridge.mjs']],
    [python, ['-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-q'], 300000],
    [process.execPath, ['tests/test_review_ui.mjs']],
    [process.execPath, ['tests/test_fill_ui.mjs']],
    [process.execPath, ['tests/test_gui_workflow.mjs']],
  ];
  let status = await lint();
  for (const [command, args, timeout] of checks) {
    if (status) break;
    status = run(command, args, timeout);
  }
  process.exitCode = status;
} catch (error) { console.error(error.message); process.exitCode = 1; }
