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
  const handlers = new Map(), frames = [], controls = [];
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
    ctx, frames, controls, emit, command, socket, launch,
    setIdle: value => { idle = value; }, setPending: value => { pending = value; },
    setAuth: value => { configured = value; }, setSession: value => { session = value; },
    closed: () => closed,
    idleState: () => until(() => {
      const state = frames.findLast(frame => frame.type === "state");
      return state?.is_idle ? state : undefined;
    }),
    ack: sequence => until(() => frames.find(frame => frame.type === "ack" && frame.command_seq === sequence)),
    event: kind => until(() => frames.findLast(frame => frame.type === "event" && frame.kind === kind)),
  };
}

test("flat frames reject duplicate escaped keys, nesting and invalid JSON", () => {
  for (const raw of [
    '{"v":1,"v":2}', '{"v":1,"\\u0076":2}', '{"x":{}}', '{"x":[]}',
    '{"x":NaN}', '{"x":01}', '{"x":true,}', '\ufeff{}', '{"x":"\u0000"}', '{} garbage',
  ]) assert.throws(() => parseFrame(raw), raw);
  assert.deepEqual({ ...parseFrame(' {"v":1,"seq":2,"type":"state","is_idle":true} ') }, { v: 1, seq: 2, type: "state", is_idle: true });
});

test("lightweight bridge projects model name, idle state and never exposes credentials", windows, async t => {
  const h = await harness(t, { auth: false, model: false });
  const initial = await h.idleState();
  assert.equal(initial.is_idle, true);
  assert.equal(initial.provider, "");
  assert.equal(initial.model, "");
  h.ctx.model = { provider: "inert-provider", id: "inert-model", apiKey: "PRIVATE-CREDENTIAL" };
  h.setAuth(true);
  const updated = await until(() => h.frames.findLast(frame => frame.type === "state" && frame.model === "inert-model"));
  assert.equal(updated.provider, "inert-provider");
  assert.equal(updated.phase, "idle");
  assert.equal(updated.is_idle, true);
  assert.equal(JSON.stringify(h.frames).includes("PRIVATE"), false);
});

test("continuation, async work and queued messages prevent premature idle/shutdown", windows, async t => {
  const h = await harness(t);
  await h.idleState();
  h.setIdle(false);
  h.emit("agent_start");
  const started = await h.event("run_start");
  assert.ok(started.run_id);
  h.setIdle(true);
  h.emit("agent_end", { willContinue: true, awaitingAsyncWork: true });
  await delay(350);
  assert.equal(h.frames.some(frame => frame.kind === "run_end"), false);
  assert.equal((await h.ack(h.command("shutdown"))).status, "rejected");
  h.emit("agent_start");
  h.setPending(true);
  h.emit("agent_end", { willContinue: false, awaitingAsyncWork: false });
  await delay(350);
  assert.equal(h.frames.some(frame => frame.kind === "run_end"), false);
  h.setPending(false);
  const ended = await h.event("run_end");
  assert.equal(ended.run_id, started.run_id);
  await h.idleState();
  assert.equal((await h.ack(h.command("shutdown"))).status, "requested");
  assert.deepEqual(h.controls, ["shutdown-requested"]);
});

test("abort acknowledges a request without fabricating run_end or shutdown", windows, async t => {
  const h = await harness(t);
  await h.idleState();
  h.setIdle(false);
  h.emit("agent_start");
  await h.event("run_start");
  assert.equal((await h.ack(h.command("abort"))).status, "requested");
  assert.deepEqual(h.controls, ["abort-requested"]);
  assert.equal(h.frames.some(frame => frame.kind === "run_end" || frame.kind === "shutdown"), false);
});

test("removed send command, invalid sequence and unknown fields close connection cleanly", windows, async t => {
  for (const attack of ["send", "duplicate", "replay", "unknown", "oversize", "utf8"]) {
    const h = await harness(t);
    await h.idleState();
    if (attack === "send") h.command("send", { text: "hello" });
    if (attack === "duplicate") h.socket.write('{"v":1,"seq":2,"type":"state","type":"abort","session_id":"isolated-session"}\n');
    if (attack === "replay") h.socket.write('{"v":1,"seq":1,"type":"state","session_id":"isolated-session"}\n');
    if (attack === "unknown") h.command("abort", { extra: "PRIVATE" });
    if (attack === "oversize") h.socket.write(Buffer.alloc(1024 * 1024 + 1, 32));
    if (attack === "utf8") h.socket.write(Buffer.from([0xff, 10]));
    await until(h.closed);
    assert.equal(h.controls.length, 0, attack);
  }
});

test("wrong launch/session/workspace handshake and session rebind close transport cleanly", windows, async t => {
  for (const field of ["launch_id", "session_id", "cwd"]) {
    const h = await harness(t, { welcome: false });
    h.command("welcome", { launch_id: h.launch, cwd: h.ctx.cwd, [field]: "foreign-identity" });
    await until(h.closed);
  }
  const h = await harness(t);
  await h.idleState();
  h.setSession("different-session");
  await until(h.closed);
  assert.equal(h.controls.length, 0);
});
