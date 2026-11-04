import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { performance } from "node:perf_hooks";
import { StringDecoder } from "node:string_decoder";
import bridgeFactory, { parseFrame } from "../native/HWPReview.Native/Bridge/omp-bridge.mjs";

const windows = { skip: process.platform !== "win32", concurrency: false };
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function until(predicate, milliseconds = 4000) {
  const deadline = performance.now() + milliseconds;
  while (performance.now() < deadline) {
    const result = predicate();
    if (result) return result;
    await delay(10);
  }
  throw new Error("Expected isolated bridge observation did not arrive");
}

// Real local transport, inert public API: no CLI, session SDK, executable,
// credential read, provider request, model, document or terminal is started.
async function harness(t, { welcome = true, auth = true, model = true } = {}) {
  const launch = randomBytes(16).toString("hex"), token = randomBytes(32).toString("hex");
  const endpoint = `\\\\.\\pipe\\HwpReview.Omp.${launch}`;
  const server = net.createServer();
  server.listen(endpoint);
  await once(server, "listening");
  const accepted = once(server, "connection");
  const handlers = new Map(), frames = [], sends = [], controls = [];
  let idle = true, pending = false, configured = auth, session = "isolated-session";
  const ctx = {
    mode: "tui", agent: { kind: "main" }, cwd: "C:\\isolated-owned-workspace",
    model: model ? { provider: "inert-provider", id: "inert-model", apiKey: "PRIVATE-CREDENTIAL" } : undefined,
    modelRegistry: { hasConfiguredAuth: () => configured },
    sessionManager: { getSessionId: () => session, getCwd: () => ctx.cwd },
    isIdle: () => idle, hasPendingMessages: () => pending,
    abort: () => { controls.push("abort-requested"); },
    shutdown: () => { controls.push("shutdown-requested"); },
  };
  const pi = {
    on: (name, handler) => handlers.set(name, handler),
    // Deliberately void, exactly like the official extension API.
    sendUserMessage: (text, options) => { sends.push({ text, options }); },
  };
  const names = ["HWP_OMP_PIPE", "HWP_OMP_TOKEN", "HWP_OMP_LAUNCH_ID"];
  const previous = names.map(name => process.env[name]);
  try {
    process.env.HWP_OMP_PIPE = endpoint; process.env.HWP_OMP_TOKEN = token; process.env.HWP_OMP_LAUNCH_ID = launch;
    bridgeFactory(pi);
  } finally {
    names.forEach((name, index) => { if (previous[index] === undefined) delete process.env[name]; else process.env[name] = previous[index]; });
  }
  const emit = (name, value = {}, context = ctx) => handlers.get(name)?.({ type: name, ...value }, context);
  emit("session_start");
  const [socket] = await accepted;
  let closed = false;
  socket.on("close", () => { closed = true; });
  socket.on("error", () => {});
  const decoder = new StringDecoder("utf8");
  let text = "";
  socket.on("data", chunk => {
    text += decoder.write(chunk);
    for (let newline; (newline = text.indexOf("\n")) >= 0;) {
      frames.push(JSON.parse(text.slice(0, newline)));
      text = text.slice(newline + 1);
    }
  });
  t.after(async () => {
    emit("session_shutdown");
    socket.destroy();
    await new Promise(resolve => server.close(resolve));
  });
  const hello = await until(() => frames.find(frame => frame.type === "hello"));
  assert.equal(hello.token, token);
  assert.equal(hello.launch_id, launch);
  assert.equal(hello.session_id, session);
  assert.equal(hello.cwd, ctx.cwd);
  let sequence = 0;
  const command = (type, fields = {}) => {
    const seq = ++sequence;
    socket.write(JSON.stringify({ v: 1, seq, type, session_id: session, ...fields }) + "\n");
    return seq;
  };
  if (welcome) command("welcome", { launch_id: launch, cwd: ctx.cwd });
  return {
    ctx, frames, sends, controls, emit, command, socket, launch,
    setIdle: value => { idle = value; }, setPending: value => { pending = value; },
    setAuth: value => { configured = value; }, setSession: value => { session = value; },
    closed: () => closed,
    ready: () => until(() => {
      const state = frames.findLast(frame => frame.type === "state");
      return state?.can_send ? state : undefined;
    }),
    ack: sequence => until(() => frames.find(frame => frame.type === "ack" && frame.command_seq === sequence)),
    event: kind => until(() => frames.findLast(frame => frame.type === "event" && frame.kind === kind)),
  };
}

function send(h, text = "공유 문맥과 명시적 요청") {
  return h.command("send", { request_id: "gui-request-1", context_sha256: "a".repeat(64), text });
}
function start(h, prompt = "공유 문맥과 명시적 요청") {
  h.emit("before_agent_start", { reason: "prompt", prompt });
  h.setIdle(false); h.emit("agent_start");
}
function final(h, text = "공개 최종 답변", stopReason = "stop") {
  h.emit("message_start", { message: { role: "assistant" } });
  h.emit("message_end", { message: { role: "assistant", content: [{ type: "text", text }], stopReason } });
}

test("flat frames reject duplicate escaped keys, nesting and invalid JSON", () => {
  for (const raw of [
    '{"v":1,"v":2}', '{"v":1,"\\u0076":2}', '{"x":{}}', '{"x":[]}',
    '{"x":NaN}', '{"x":01}', '{"x":true,}', '\ufeff{}', '{"x":"\u0000"}', '{} garbage',
  ]) assert.throws(() => parseFrame(raw), raw);
  assert.deepEqual({ ...parseFrame(' {"v":1,"seq":2,"type":"state","enabled":false} ') }, { v: 1, seq: 2, type: "state", enabled: false });
});

test("configured model/auth metadata, not the handshake, gates explicit sends", windows, async t => {
  const h = await harness(t, { auth: false, model: false });
  await until(() => h.frames.find(frame => frame.type === "state"));
  assert.equal(h.frames.some(frame => frame.can_send), false);
  assert.equal((await h.ack(send(h))).status, "rejected");
  h.ctx.model = { provider: "inert-provider", id: "inert-model" };
  await delay(550);
  assert.equal(h.frames.some(frame => frame.can_send), false);
  h.setAuth(true);
  const state = await h.ready();
  assert.equal(state.phase, "idle");
  assert.equal(h.sends.length, 0);
});

test("void dispatch ACK is not admission; final public rewrite wins and private blocks never leave", windows, async t => {
  const h = await harness(t);
  await h.ready();
  assert.equal((await h.ack(send(h))).status, "dispatched");
  assert.equal(h.frames.some(frame => frame.kind === "run_start"), false);
  start(h);
  h.emit("message_start", { message: { role: "assistant", thinking: "PRIVATE-THINKING" } });
  h.emit("message_update", { message: { role: "assistant" }, assistantMessageEvent: { type: "thinking_delta", delta: "PRIVATE-THINKING" } });
  h.emit("message_update", { message: { role: "toolResult", content: "PRIVATE-TOOL" }, assistantMessageEvent: { type: "text_delta", delta: "PRIVATE-TOOL" } });
  h.emit("message_update", { message: { role: "assistant" }, assistantMessageEvent: { type: "text_delta", delta: "수정 전" } });
  h.emit("message_end", { message: {
    role: "assistant", content: [{ type: "thinking", thinking: "PRIVATE-THINKING" }, { type: "toolCall", arguments: "PRIVATE-TOOL" }, { type: "text", text: "수정된 최종 😀" }],
    stopReason: "stop", errorMessage: "PRIVATE-CREDENTIAL", providerPayload: { apiKey: "PRIVATE-CREDENTIAL" },
  } });
  const answer = await h.event("assistant_end");
  assert.equal(answer.text, "수정된 최종 😀");
  assert.equal(answer.request_id, "gui-request-1");
  assert.equal(answer.context_sha256, "a".repeat(64));
  assert.equal(JSON.stringify(h.frames).includes("PRIVATE"), false);
  h.emit("agent_end", { messages: [{ content: "PRIVATE" }], willContinue: false, awaitingAsyncWork: false });
  await delay(300);
  assert.equal(h.frames.some(frame => frame.kind === "run_end"), false);
  h.setIdle(true);
  const ended = await h.event("run_end");
  assert.equal(ended.run_id, answer.run_id);
  assert.equal(ended.request_id, answer.request_id);
  await h.ready();
});

test("mixed TUI input revokes GUI binding and never retries the dispatched prompt", windows, async t => {
  const h = await harness(t); await h.ready();
  await h.ack(send(h)); start(h);
  h.emit("input", { source: "interactive", text: "사용자의 동시 터미널 요청" });
  final(h);
  const answer = await h.event("assistant_end");
  assert.equal(answer.request_id, ""); assert.equal(answer.context_sha256, "");
  assert.equal((await h.ack(send(h))).status, "rejected");
  assert.equal(h.sends.length, 1);
  assert.equal(h.frames.findLast(frame => frame.type === "state").phase, "uncertain");
});

test("different prepared prompt cannot borrow a GUI request/context identity", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h));
  start(h, "외부 또는 혼합된 다른 문맥"); final(h);
  const answer = await h.event("assistant_end");
  assert.equal(answer.request_id, ""); assert.equal(answer.context_sha256, "");
  assert.equal(h.frames.findLast(frame => frame.type === "state").can_send, false);
});

test("missing lifecycle after void dispatch becomes uncertain without a second send", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h));
  const now = Date.now;
  try {
    const advanced = now() + 16000;
    Date.now = () => advanced;
    await h.event("fault");
  } finally { Date.now = now; }
  assert.equal(h.sends.length, 1);
  assert.equal(h.frames.some(frame => frame.kind === "run_end"), false);
  assert.equal((await h.ack(send(h))).status, "rejected");
});

test("continuation, async work and queued messages prevent premature idle/shutdown", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h)); start(h); final(h);
  h.setIdle(true);
  h.emit("agent_end", { willContinue: true, awaitingAsyncWork: true });
  await delay(350);
  assert.equal(h.frames.some(frame => frame.kind === "run_end"), false);
  assert.equal((await h.ack(h.command("shutdown"))).status, "rejected");
  h.emit("agent_start"); final(h, "같은 실행의 후속 답변");
  h.setPending(true);
  h.emit("agent_end", { willContinue: false, awaitingAsyncWork: false });
  await delay(350);
  assert.equal(h.frames.some(frame => frame.kind === "run_end"), false);
  h.setPending(false);
  const ended = await h.event("run_end");
  assert.equal(h.frames.filter(frame => frame.kind === "run_start").length, 1);
  assert.equal(ended.run_id, h.frames.find(frame => frame.kind === "run_start").run_id);
  await until(() => h.frames.findLast(frame => frame.type === "state")?.phase === "idle");
  assert.equal((await h.ack(h.command("shutdown"))).status, "requested");
  assert.deepEqual(h.controls, ["shutdown-requested"]);
  assert.equal(h.frames.some(frame => frame.kind === "shutdown"), false);
});

test("abort acknowledges a request, not observed cancellation or process exit", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h)); start(h);
  assert.equal((await h.ack(h.command("abort"))).status, "requested");
  assert.deepEqual(h.controls, ["abort-requested"]);
  assert.equal(h.frames.some(frame => frame.kind === "run_end" || frame.kind === "shutdown"), false);
  assert.equal(h.frames.findLast(frame => frame.type === "state").can_send, false);
});

test("invalid sequence and unknown command fields close the single connection without dispatch", windows, async t => {
  for (const attack of ["duplicate", "replay", "unknown", "oversize", "utf8", "prompt-size", "context-hash"]) {
    const h = await harness(t); await h.ready();
    if (attack === "duplicate") h.socket.write('{"v":1,"seq":2,"type":"state","type":"send","session_id":"isolated-session"}\n');
    if (attack === "replay") h.socket.write('{"v":1,"seq":1,"type":"state","session_id":"isolated-session"}\n');
    if (attack === "unknown") h.command("send", { request_id: "request", context_sha256: "a".repeat(64), text: "hello", execute: "PRIVATE" });
    if (attack === "oversize") h.socket.write(Buffer.alloc(1024 * 1024 + 1, 32));
    if (attack === "utf8") h.socket.write(Buffer.from([0xff, 10]));
    if (attack === "prompt-size") send(h, "x".repeat(32769));
    if (attack === "context-hash") h.command("send", { request_id: "request", context_sha256: "A".repeat(64), text: "hello" });
    await until(h.closed);
    assert.equal(h.sends.length, 0, attack);
  }
});

test("wrong launch/session/workspace handshake and later session rebind cannot acquire GUI authority", windows, async t => {
  for (const field of ["launch_id", "session_id", "cwd"]) {
    const h = await harness(t, { welcome: false });
    h.command("welcome", { launch_id: h.launch, cwd: h.ctx.cwd, [field]: "foreign-identity" });
    await until(h.closed);
    assert.equal(h.sends.length, 0);
  }
  const h = await harness(t); await h.ready();
  h.setSession("different-session");
  await until(h.closed);
  assert.equal(h.sends.length, 0);
});

test("oversized final public text cannot appear as a complete GUI proposal", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h)); start(h);
  final(h, "x".repeat(262145));
  await h.event("fault");
  assert.equal(h.frames.some(frame => frame.kind === "assistant_end"), false);
  assert.equal(h.frames.findLast(frame => frame.type === "state").can_send, false);
});

test("fragmented UTF8 prompt dispatches once and unbound TUI answers stay advisory", windows, async t => {
  const h = await harness(t); await h.ready();
  const prompt = "한글과 😀 경계";
  const frame = Buffer.from(JSON.stringify({ v: 1, seq: 2, type: "send", session_id: "isolated-session", request_id: "fragmented-request", context_sha256: "b".repeat(64), text: prompt }) + "\n");
  for (const byte of frame) h.socket.write(Buffer.from([byte]));
  assert.equal((await h.ack(2)).status, "dispatched");
  assert.equal(h.sends.length, 1); assert.equal(h.sends[0].text, prompt);
  start(h, prompt); final(h);
  h.setIdle(true); h.emit("agent_end", { willContinue: false, awaitingAsyncWork: false });
  await h.event("run_end"); await h.ready();
  const count = h.frames.filter(value => value.kind === "assistant_end").length;
  h.emit("input", { source: "interactive", text: "터미널 자체 요청" });
  start(h, "터미널 자체 요청"); final(h, "터미널의 공개 참고 답변");
  await until(() => h.frames.filter(value => value.kind === "assistant_end").length > count);
  const answer = h.frames.findLast(value => value.kind === "assistant_end");
  assert.equal(answer.request_id, ""); assert.equal(answer.context_sha256, "");
  assert.equal(answer.text, "터미널의 공개 참고 답변");
  assert.equal(h.sends.length, 1);
});

test("a long streamed Korean answer is charged once when the final text replaces its deltas", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h)); start(h);
  const text = "한".repeat(50000);
  h.emit("message_start", { message: { role: "assistant" } });
  h.emit("message_update", { message: { role: "assistant" }, assistantMessageEvent: { type: "text_delta", delta: text } });
  h.emit("message_end", { message: { role: "assistant", content: [{ type: "text", text }], stopReason: "stop" } });
  const answer = await h.event("assistant_end");
  assert.equal(answer.text, text);
  assert.equal(answer.request_id, "gui-request-1");
  h.setIdle(true); h.emit("agent_end", { willContinue: false, awaitingAsyncWork: false });
  await h.event("run_end");
  assert.equal(h.frames.some(frame => frame.kind === "fault"), false);
});

test("builtin read write and bash toolUse messages remain intermediate within one GUI-bound run", windows, async t => {
  const h = await harness(t); await h.ready(); await h.ack(send(h)); start(h);
  for (const [index, name] of ["read", "write", "bash"].entries()) {
    h.emit("message_start", { message: { role: "assistant" } });
    h.emit("message_end", { message: { role: "assistant", content: [
      { type: "text", text: `${name} 작업 중` },
      { type: "toolCall", id: `call-${index}`, name, arguments: { private: "PRIVATE-TOOL-ARGUMENT" } },
    ], stopReason: "toolUse" } });
    h.emit("message_start", { message: { role: "toolResult", toolCallId: `call-${index}`, toolName: name } });
    h.emit("message_end", { message: { role: "toolResult", toolCallId: `call-${index}`, toolName: name,
      content: [{ type: "text", text: "PRIVATE-TOOL-RESULT" }] } });
  }
  final(h, "작업을 완료한 답변");
  h.setIdle(true); h.emit("agent_end", { willContinue: false, awaitingAsyncWork: false });
  await h.event("run_end"); await h.ready();
  const messages = h.frames.filter(frame => frame.kind === "assistant_end");
  assert.deepEqual(messages.map(frame => frame.stop_reason), ["toolUse", "toolUse", "toolUse", "stop"]);
  assert.deepEqual(messages.map(frame => frame.text), ["read 작업 중", "write 작업 중", "bash 작업 중", "작업을 완료한 답변"]);
  assert.equal(new Set(messages.map(frame => frame.message_id)).size, 4);
  for (const message of messages) {
    assert.equal(message.request_id, "gui-request-1");
    assert.equal(message.context_sha256, "a".repeat(64));
    assert.equal(message.run_id, "run-1");
  }
  assert.equal(h.frames.filter(frame => frame.kind === "run_start").length, 1);
  assert.equal(h.frames.filter(frame => frame.kind === "run_end").length, 1);
  assert.equal(h.frames.some(frame => frame.kind === "fault"), false);
  assert.doesNotMatch(JSON.stringify(h.frames), /PRIVATE-TOOL/);
});
