import assert from 'node:assert/strict';
import test from 'node:test';
import { ESLint } from 'eslint';
import { join } from 'node:path';
import { browserScript, root } from '../scripts/lint.mjs';

const engine = new ESLint({ cwd: root });
async function viewerRules(source) {
  const [result] = await engine.lintText(source, { filePath: join(root, 'native/HWPReview.Native/ViewerAssets/fixture.js') });
  return result.messages.map(message => message.ruleId);
}

test('inline browser code preserves real HTML line/column positions, including Unicode', () => {
  const html = '<p>가😀</p>\r\n<script>\r\nunknownReviewName();\r\n</script>\r\n<p>끝</p>';
  const source = browserScript(html);
  assert.equal(source.length, html.length);
  assert.equal(source.indexOf('unknownReviewName'), html.indexOf('unknownReviewName'));
  assert.equal(source.split('\n').length, html.split('\n').length);
});

test('native WebView scripts and OMP bridge pass correctness and DOM-safety lint', async () => {
  const results = await engine.lintFiles([
    'native/HWPReview.Native/Bridge/omp-bridge.mjs',
    'native/HWPReview.Native/TerminalAssets/terminal.js',
    'native/HWPReview.Native/ViewerAssets/viewer.js',
    'native/HWPReview.Native/ViewerAssets/svg-safety.js',
  ]);
  for (const result of results) {
    assert.deepEqual(result.messages, [], result.filePath);
  }
});

test('all executable inline blocks are covered; JSON document data is not executable code', () => {
  const source = browserScript('<script>const first=1;</script>\n<script type="application/json">{"label":"자료"}</script>\n<script>console.log(first);</script>');
  assert.match(source, /const first=1/);
  assert.match(source, /console.log\(first\)/);
  assert(!source.includes('자료'));
  assert.throws(() => browserScript('<p>no scripts</p>'), /No executable/);
  assert.throws(() => browserScript('<script src="external.js"></script>'), /explicit local/);
  assert.throws(() => browserScript('<script type="module">export {};</script>'), /Unsupported/);
});

test('browser globals are recognized but missing names and unreachable code are blocked', async () => {
  assert.deepEqual(await viewerRules('document.querySelector("p").textContent="safe";'), []);
  assert((await viewerRules('unknownReviewName();')).includes('no-undef'));
  assert((await viewerRules('// eslint-disable-next-line no-undef\nunknownReviewName();')).includes('no-undef'));
  assert((await viewerRules('function bad(){return;document.title="never";}')).includes('no-unreachable'));
});

test('viewer data cannot become parsed HTML; literal empty clearing remains allowed', async () => {
  assert((await viewerRules('document.body.innerHTML=location.hash;')).includes('no-restricted-syntax'));
  assert((await viewerRules('document.body["outerHTML"]=location.hash;')).includes('no-restricted-syntax'));
  assert((await viewerRules('document.body.insertAdjacentHTML("beforeend",location.hash);')).includes('no-restricted-syntax'));
  assert.deepEqual(await viewerRules('document.body.innerHTML="";document.body.textContent=location.hash;'), []);
});

test('dynamic execution of viewer text is blocked', async () => {
  assert((await viewerRules('eval(location.hash);')).includes('no-eval'));
  assert((await viewerRules('new Function("return 1");')).includes('no-new-func'));
  assert((await viewerRules('setTimeout("alert(1)",1);')).includes('no-implied-eval'));
});
