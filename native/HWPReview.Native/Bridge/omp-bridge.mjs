import net from "node:net";

const LINE_LIMIT = 1024 * 1024;
const TEXT_LIMIT = 256 * 1024;
const PROMPT_LIMIT = 32768;
const OBSERVATION_MS = 15000;
const utf8 = new TextDecoder("utf-8", { fatal: true });
const identifier = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);
const hex = (value, length) => typeof value === "string" && new RegExp(`^[0-9a-f]{${length}}$`).test(value);
const publicText = (value, maximum, empty = true) => typeof value === "string" && (empty || value.length > 0) && Buffer.byteLength(value, "utf8") <= maximum && !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\uD800-\uDFFF]/u.test(value);
const samePath = (a, b) => typeof a === "string" && typeof b === "string" && a.replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase() === b.replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase();

// The wire is deliberately flat. Tokenize before JSON.parse so duplicate keys,
// including differently escaped spellings, cannot be overwritten silently.
export function parseFrame(raw) {
  const token = /"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null/y;
  let at = 0;
  const space = () => { while (/[\x20\t\r\n]/.test(raw[at] ?? "!") && at < raw.length) at++; };
  const scalar = () => {
    space(); token.lastIndex = at;
    const match = token.exec(raw);
    if (!match) throw new Error("frame");
    at = token.lastIndex;
    const value = JSON.parse(match[0]);
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("frame");
    return value;
  };
  space(); if (raw[at++] !== "{") throw new Error("frame");
  const result = Object.create(null);
  space();
  if (raw[at] !== "}") {
    for (let count = 0; ; count++) {
      if (count >= 16) throw new Error("frame");
      const key = scalar();
      if (typeof key !== "string" || Object.hasOwn(result, key)) throw new Error("frame");
      space(); if (raw[at++] !== ":") throw new Error("frame");
      result[key] = scalar();
      space(); if (raw[at] === "}") break;
      if (raw[at++] !== ",") throw new Error("frame");
    }
  }
  at++; space(); if (at !== raw.length) throw new Error("frame");
  return result;
}

function keys(value, names) {
  const actual = Object.keys(value);
  if (actual.length !== names.length || actual.some(key => !names.includes(key))) throw new Error("frame");
}

export default function ompBridge(pi) {
  const launchId = process.env.HWP_OMP_LAUNCH_ID;
  const token = process.env.HWP_OMP_TOKEN;
  const endpoint = process.env.HWP_OMP_PIPE;
  // Do not retain these in the session's process environment for subprocesses.
  delete process.env.HWP_OMP_TOKEN;
  delete process.env.HWP_OMP_PIPE;
  delete process.env.HWP_OMP_LAUNCH_ID;
  if (!hex(launchId, 32) || !hex(token, 64) || endpoint !== `\\\\.\\pipe\\HwpReview.Omp.${launchId}`) throw new Error("앱 소유 OMP 연결 설정이 잘못되었습니다.");

  let socket, timer, ctx, sessionId = "", cwd = "";
  let attempted = false, connected = false, welcomed = false, closed = false, shuttingDown = false;
  let incoming = 0, outgoing = 0, used = 0;
  const bytes = Buffer.allocUnsafe(LINE_LIMIT + 1);
  let lastState = "", initialObservations = 0, uncertain = false;
  let gui = null, inputPending = false, admitted = false, pendingSince = 0;
  let run = null, nextRun = 0, nextMessage = 0;

  function stopTransport(graceful = false) {
    if (closed) return;
    closed = true; connected = false; welcomed = false;
    clearInterval(timer);
    timer = undefined;
    if (graceful && socket) {
      socket.end();
      const deadline = setTimeout(() => socket.destroy(), 500);
      deadline.unref?.();
      socket.once("close", () => clearTimeout(deadline));
    } else socket?.destroy();
  }
  function send(type, fields = {}) {
    if (closed || !connected || !socket) return false;
    if (outgoing >= Number.MAX_SAFE_INTEGER) { stopTransport(); return false; }
    const frame = Buffer.from(JSON.stringify({ v: 1, seq: ++outgoing, type, ...fields }) + "\n", "utf8");
    if (frame.length > LINE_LIMIT || socket.writableLength + frame.length > LINE_LIMIT) { stopTransport(); return false; }
    socket.write(frame);
    return true;
  }
  function binding() {
    return !uncertain && run?.binding ? run.binding : { request_id: "", context_sha256: "" };
  }
  function event(kind, text = "", stopReason = "", messageId = "") {
    if (!welcomed) return;
    send("event", { session_id: sessionId, kind, ...binding(), run_id: run?.id ?? "", message_id: messageId, text, stop_reason: stopReason });
  }
  function fault() {
    if (!uncertain) { uncertain = true; if (run) run.binding = null; event("fault"); }
    state();
  }
  function eligible(context) { return context?.mode === "tui" && context.agent?.kind === "main"; }
  function identity(context) {
    if (!eligible(context)) return false;
    if (context.sessionManager.getSessionId() !== sessionId || !samePath(context.cwd, cwd) || !samePath(context.sessionManager.getCwd(), cwd)) {
      fault(); stopTransport(); return false;
    }
    ctx = context;
    return true;
  }
  function metadata() {
    const model = ctx?.model;
    if (!model) return { provider: "", model: "", configured: false };
    if (!publicText(model.provider, 128, false) || !publicText(model.id, 256, false)) throw new Error("metadata");
    return { provider: model.provider, model: model.id, configured: ctx.modelRegistry.hasConfiguredAuth(model) === true };
  }
  function snapshot() {
    const model = metadata();
    const idle = ctx.isIdle() === true, pending = ctx.hasPendingMessages() !== false;
    let phase = "starting";
    if (uncertain) phase = "uncertain";
    else if (shuttingDown) phase = "settling";
    else if (run) phase = run.ended ? "settling" : "running";
    else if (gui || inputPending || admitted) phase = "submitting";
    else if (initialObservations >= 2 && idle && !pending) phase = "idle";
    else if (!idle || pending) phase = "settling";
    const can_send = welcomed && phase === "idle" && model.configured;
    const detail = uncertain ? "전송·실행 연결이 불확실합니다. 자동 재시도하지 말고 실제 터미널을 확인하세요."
      : shuttingDown ? "OMP에 정상 종료를 요청했습니다. 실제 프로세스 종료를 기다립니다."
      : !model.model ? "실제 터미널에서 계정과 모델을 직접 설정하세요."
      : !model.configured ? "선택 모델의 인증 설정이 필요합니다. 실제 터미널에서 직접 확인하세요."
      : "모델·인증 설정 메타데이터만 확인했습니다. 실제 인증 성공이나 비용 없는 실행을 보장하지 않습니다.";
    return { session_id: sessionId, phase, can_send, provider: model.provider, model: model.model, detail };
  }
  function state(force = false) {
    if (!welcomed || closed || !ctx) return;
    const value = snapshot(), serialized = JSON.stringify(value);
    if (force || serialized !== lastState) { lastState = serialized; send("state", value); }
  }
  function observe() {
    if (closed || !ctx || !identity(ctx)) return;
    initialObservations++;
    const idle = ctx.isIdle() === true, pending = ctx.hasPendingMessages() !== false;
    if ((gui || inputPending || admitted) && !run && Date.now() - pendingSince > OBSERVATION_MS) fault();
    if (run?.ended && !run.continuation && !run.async && idle && !pending && !inputPending && !admitted) {
      if (run.message) fault();
      event("run_end");
      run = null; gui = null;
    }
    state(initialObservations % 4 === 0);
  }
  function safe(action) {
    try { action(); } catch {
      uncertain = true;
      if (run) run.binding = null;
      event("fault");
      stopTransport();
    }
  }
  function ack(command, status) {
    send("ack", { session_id: sessionId, command_seq: command.seq, request_id: command.type === "send" ? command.request_id : "", context_sha256: command.type === "send" ? command.context_sha256 : "", status });
  }
  function command(value) {
    if (value.v !== 1 || !Number.isSafeInteger(value.seq) || value.seq !== incoming + 1 || value.session_id !== sessionId) throw new Error("sequence");
    if (!welcomed) {
      keys(value, ["v", "seq", "type", "launch_id", "session_id", "cwd"]);
      if (value.type !== "welcome" || value.launch_id !== launchId || !samePath(value.cwd, cwd)) throw new Error("identity");
      incoming = value.seq; welcomed = true;
      state(true);
      return;
    }
    if (value.type === "send") keys(value, ["v", "seq", "type", "session_id", "request_id", "context_sha256", "text"]);
    else if (["state", "abort", "shutdown"].includes(value.type)) keys(value, ["v", "seq", "type", "session_id"]);
    else throw new Error("command");
    incoming = value.seq;
    if (!identity(ctx)) return;
    if (value.type === "state") { state(true); ack(value, "observed"); return; }
    if (value.type === "send") {
      if (!identifier(value.request_id) || !hex(value.context_sha256, 64) || !publicText(value.text, PROMPT_LIMIT, false)) throw new Error("prompt");
      if (!snapshot().can_send || gui || run || inputPending || admitted) { ack(value, "rejected"); state(true); return; }
      gui = { request_id: value.request_id, context_sha256: value.context_sha256, text: value.text };
      pendingSince = Date.now();
      state();
      // Public API returns void: this cannot acknowledge admission or persistence.
      try { pi.sendUserMessage(value.text, { attribution: "user" }); }
      catch { fault(); ack(value, "rejected"); return; }
      ack(value, "dispatched"); return;
    }
    if (value.type === "abort") {
      if (!run && !gui && !inputPending && ctx.isIdle() && !ctx.hasPendingMessages()) { ack(value, "rejected"); return; }
      ctx.abort(); ack(value, "requested"); state(true); return;
    }
    if (uncertain || snapshot().phase !== "idle" || gui || run || inputPending || admitted || !ctx.isIdle() || ctx.hasPendingMessages()) { ack(value, "rejected"); state(true); return; }
    shuttingDown = true; state();
    ctx.shutdown(); ack(value, "requested");
  }
  function receive(block) {
    if (closed) return;
    for (const byte of block) {
      if (used >= LINE_LIMIT) throw new Error("line");
      if (byte === 10) {
        if (used === 0) throw new Error("line");
        const value = parseFrame(utf8.decode(bytes.subarray(0, used)));
        used = 0; command(value);
        if (closed) return;
      } else bytes[used++] = byte;
    }
  }
  function charge(text, final = false) {
    const size = typeof text === "string" ? Buffer.byteLength(text, "utf8") : 0;
    if (!publicText(text, TEXT_LIMIT) || !run?.message || run.publicBytes + (final ? size : run.message.streamBytes + size) > TEXT_LIMIT) { fault(); return false; }
    // Final text replaces this message's deltas, rather than charging it twice.
    if (final) run.publicBytes += size;
    else run.message.streamBytes += size;
    return true;
  }
  function hook(name, action) {
    pi.on(name, (value, context) => {
      if (!eligible(context) || !attempted || closed) return;
      safe(() => { if (identity(context)) action(value); });
    });
  }

  pi.on("session_start", (_value, context) => {
    if (!eligible(context)) return;
    if (attempted) { safe(() => { fault(); stopTransport(); }); return; }
    attempted = true; ctx = context;
    safe(() => {
      sessionId = context.sessionManager.getSessionId(); cwd = context.sessionManager.getCwd();
      if (!identifier(sessionId) || !publicText(cwd, 32760, false) || !samePath(context.cwd, cwd)) throw new Error("identity");
      socket = net.createConnection(endpoint);
      socket.once("connect", () => safe(() => {
        connected = true;
        send("hello", { launch_id: launchId, token, session_id: sessionId, cwd });
      }));
      socket.on("data", data => safe(() => receive(data)));
      socket.on("error", () => stopTransport());
      socket.on("close", () => stopTransport());
      const started = Date.now();
      timer = setInterval(() => safe(() => {
        if (!welcomed && Date.now() - started > OBSERVATION_MS) { stopTransport(); return; }
        if (welcomed) observe();
      }), 250);
      timer.unref?.();
    });
  });
  hook("input", () => {
    if (gui || run?.binding) fault();
    inputPending = true; pendingSince = Date.now(); state();
  });
  hook("before_agent_start", value => {
    if (value.reason !== "prompt") { fault(); return; }
    if (run) {
      // Another prepared prompt while a GUI run exists cannot retain its binding.
      fault();
    }
    if (gui && (inputPending || value.prompt !== gui.text || (value.images?.length ?? 0) !== 0 || admitted)) fault();
    admitted = true; pendingSince = Date.now();
    state();
  });
  hook("agent_start", () => {
    if (run) {
      if (!run.ended || (!run.continuation && !run.async)) fault();
      run.ended = false; run.continuation = false; run.async = false;
    } else {
      if (gui && !admitted) fault();
      run = { id: `run-${++nextRun}`, binding: gui && admitted && !uncertain && !inputPending ? { request_id: gui.request_id, context_sha256: gui.context_sha256 } : null, ended: false, continuation: false, async: false, message: null, publicBytes: 0, messages: 0 };
      event("run_start");
    }
    if (gui) gui.text = "";
    inputPending = false; admitted = false; state();
  });
  hook("message_start", value => {
    if (value.message?.role !== "assistant") return;
    if (!run || run.ended || run.message || ++run.messages > 128) { fault(); return; }
    run.message = { id: `message-${++nextMessage}`, streamBytes: 0 };
    event("assistant_start", "", "", run.message.id);
  });
  hook("message_update", value => {
    if (value.message?.role !== "assistant" || value.assistantMessageEvent?.type !== "text_delta") return;
    if (!run?.message || run.ended) { fault(); return; }
    const text = value.assistantMessageEvent.delta;
    if (!charge(text)) return;
    // Bound every projected delta independently; split only at Unicode boundaries.
    let chunk = "";
    for (const character of text) {
      if (chunk.length + character.length > 4096) { event("assistant_delta", chunk, "", run.message.id); chunk = ""; }
      chunk += character;
    }
    if (chunk) event("assistant_delta", chunk, "", run.message.id);
  });
  hook("message_end", value => {
    if (value.message?.role !== "assistant") return;
    if (!run?.message || run.ended || !Array.isArray(value.message.content)) { fault(); return; }
    const reason = value.message.stopReason;
    if (!["stop", "length", "toolUse", "error", "aborted"].includes(reason)) { fault(); return; }
    if (value.message.content.length > 4096) { fault(); return; }
    const parts = [];
    let size = 0;
    for (const block of value.message.content) {
      if (block?.type !== "text") continue;
      if (!publicText(block.text, TEXT_LIMIT)) { fault(); return; }
      size += Buffer.byteLength(block.text, "utf8");
      if (size > TEXT_LIMIT) { fault(); return; }
      parts.push(block.text);
    }
    const text = parts.join("");
    if (!charge(text, true)) return;
    // No errorMessage/provider payload: even diagnostic strings may contain secrets.
    event("assistant_end", text, reason, run.message.id);
    run.message = null;
  });
  hook("agent_end", value => {
    if (!run || run.ended) { fault(); return; }
    if (value.willContinue !== undefined && typeof value.willContinue !== "boolean" || value.awaitingAsyncWork !== undefined && typeof value.awaitingAsyncWork !== "boolean") { fault(); return; }
    run.ended = true; run.continuation = value.willContinue === true; run.async = value.awaitingAsyncWork === true;
    if (run.message) fault();
    // The next observation checks live idle/queue state after prompt unwinding.
    state();
  });
  pi.on("session_shutdown", (_value, context) => {
    if (!eligible(context) || !attempted || closed) return;
    safe(() => { if (identity(context)) event("shutdown"); });
    // No wait inside the API's two-second shutdown-handler budget.
    stopTransport(true);
  });
}
