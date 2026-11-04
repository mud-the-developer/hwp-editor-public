import assert from 'node:assert/strict';
import test from 'node:test';
import { ESLint } from 'eslint';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { browserScript, root } from '../scripts/lint.mjs';

const engine = new ESLint({ cwd: root });
async function browserRules(source) {
  const [result] = await engine.lintText(source, { filePath: 'web/fixture.inline.js' });
  return result.messages.map(message => message.ruleId);
}

test('inline browser code preserves real HTML line/column positions, including Unicode', () => {
  const html = '<p>가😀</p>\r\n<script>\r\nunknownReviewName();\r\n</script>\r\n<p>끝</p>';
  const source = browserScript(html);
  assert.equal(source.length, html.length);
  assert.equal(source.indexOf('unknownReviewName'), html.indexOf('unknownReviewName'));
  assert.equal(source.split('\n').length, html.split('\n').length);
});

test('template review inline script receives browser correctness and DOM-safety lint', async () => {
  const html = await readFile(join(root, 'web/template-review.html'), 'utf8');
  const source = browserScript(html);
  const [result] = await engine.lintText(source, { filePath: 'web/template-review.inline.js' });
  assert.deepEqual(result.messages, []);
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
  assert.deepEqual(await browserRules('document.querySelector("p").textContent="safe";'), []);
  assert((await browserRules('unknownReviewName();')).includes('no-undef'));
  assert((await browserRules('// eslint-disable-next-line no-undef\nunknownReviewName();')).includes('no-undef'));
  assert((await browserRules('function bad(){return;document.title="never";}')).includes('no-unreachable'));
});

test('review data cannot become parsed HTML; literal empty clearing remains allowed', async () => {
  assert((await browserRules('document.body.innerHTML=location.hash;')).includes('no-restricted-syntax'));
  assert((await browserRules('document.body["outerHTML"]=location.hash;')).includes('no-restricted-syntax'));
  assert((await browserRules('document.body.insertAdjacentHTML("beforeend",location.hash);')).includes('no-restricted-syntax'));
  assert.deepEqual(await browserRules('document.body.innerHTML="";document.body.textContent=location.hash;'), []);
});

test('dynamic execution of review text is blocked', async () => {
  assert((await browserRules('eval(location.hash);')).includes('no-eval'));
  assert((await browserRules('new Function("return 1");')).includes('no-new-func'));
  assert((await browserRules('setTimeout("alert(1)",1);')).includes('no-implied-eval'));
});
