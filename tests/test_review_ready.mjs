import assert from 'node:assert/strict';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { REVIEW_READY } from './review_ready.mjs';

// Only this fixed test-owned literal is evaluated, never document content.
const ready = context => runInNewContext(REVIEW_READY, context);

test('missing or partially installed review globals remain not ready', () => {
  for (const context of [{}, {state: {}}, {state: {}, recoveryRead: true}, {state: {}, busy: false}]) {
    assert.equal(ready(context), false);
  }
});

test('null state, unread recovery and busy review still block readiness', () => {
  for (const context of [
    {state: null, recoveryRead: true, busy: false},
    {state: {}, recoveryRead: false, busy: false},
    {state: {}, recoveryRead: true, busy: true},
  ]) assert.equal(ready(context), false);
});

test('ready requires all original review and recovery conditions', () => {
  assert.equal(ready({state: {manifest: {}}, recoveryRead: true, busy: false}), true);
});

test('real initialization errors propagate instead of becoming readiness retries', () => {
  assert.throws(
    () => runInNewContext(`${REVIEW_READY}; let state = {};`, {recoveryRead: true, busy: false}),
    /before initialization/,
  );
});
