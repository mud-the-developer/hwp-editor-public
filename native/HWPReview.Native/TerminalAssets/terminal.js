"use strict";
(() => {
  const notice = document.getElementById("notice");
  const container = document.getElementById("terminal");
  if (location.href !== "https://hwp-omp.invalid/index.html" || window.top !== window || !window.chrome?.webview) {
    notice.textContent = "허용된 로컬 터미널 연결이 아닙니다.";
    return;
  }
  const bridge = window.chrome.webview;
  const encoder = new TextEncoder();
  const term = new Terminal({
    fontFamily: '"Cascadia Mono", Consolas, "Malgun Gothic", monospace',
    fontSize: 14,
    cursorBlink: false,
    cursorStyle: "block",
    screenReaderMode: true,
    minimumContrastRatio: 4.5,
    scrollback: 5000,
    allowProposedApi: true,
    allowTransparency: false,
    convertEol: false,
    ignoreBracketedPasteMode: false,
    disableStdin: true,
    windowsPty: { backend: "conpty" },
    linkHandler: { activate() {}, hover() {}, leave() {}, allowNonHttpProtocols: false },
    windowOptions: {}
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.loadAddon(new UnicodeGraphemesAddon.UnicodeGraphemesAddon());
  term.open(container);
  if (term.textarea) {
    term.textarea.setAttribute("aria-label", "OMP 입력란: 한글·이모지·IME 지원. 초안 붙여넣기는 전송하지 않습니다.");
    term.textarea.setAttribute("autocomplete", "off");
    term.textarea.setAttribute("autocapitalize", "off");
    term.textarea.setAttribute("spellcheck", "false");
  }
  // Public xterm parser hooks consume these OSCs, rather than rewriting the VT stream.
  // No clipboard provider, web-links addon, browser shell, or URL activation exists.
  term.parser.registerOscHandler(52, () => true);
  term.parser.registerOscHandler(8, () => true);
  let sent = 0;
  let received = 0;
  let inputId = 0;
  let running = false;
  let blocked = false;
  let initialized = false;
  let fitting = false;
  let outputBusy = false;
  let capturePaste = 0;
  let pasteCaptured = false;
  let queuedBytes = 0;
  let activeInput = null;
  const inputs = [];
  const send = (type, payload = {}) => bridge.postMessage({ v: 1, seq: ++sent, type, ...payload });
  const tell = text => { notice.textContent = text; };
  const fail = code => {
    if (!blocked) send("diagnostic", { code });
    blocked = true;
    term.options.disableStdin = true;
    tell("터미널 경계를 확인하지 못해 입력을 중단했습니다. OMP를 강제 종료하지 않았습니다. 앱의 진단을 확인하세요.");
  };
  const exact = (message, fields) => {
    if (!message || typeof message !== "object" || Array.isArray(message)) return false;
    const keys = Object.keys(message);
    return keys.length === fields.length && fields.every(key => Object.hasOwn(message, key));
  };
  const positive = value => Number.isSafeInteger(value) && value > 0;
  const normalText = text => typeof text === "string" && encoder.encode(text).length <= 65536 && text.length > 0 && !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/u.test(text) && !/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u.test(text);
  function drainInput() {
    if (activeInput || inputs.length === 0 || blocked || !running) return;
    activeInput = inputs.shift();
    send("input", { id: activeInput.id, text: activeInput.text, encoding: activeInput.encoding });
  }
  function enqueueInput(text, encoding) {
    if (!running || blocked || text.length === 0) return;
    const bytes = encoding === "binary" ? text.length : encoder.encode(text).length;
    if (bytes > 131072 || queuedBytes + bytes > 262144 || inputs.length >= 256) {
      if (capturePaste) { pasteCaptured = true; send("paste-result", { id: capturePaste, ok: false, reason: "blocked" }); }
      fail("input-overflow");
      return;
    }
    queuedBytes += bytes;
    inputs.push({ id: ++inputId, text, bytes, encoding, paste: capturePaste });
    if (capturePaste) pasteCaptured = true;
    drainInput();
  }
  term.onData(text => enqueueInput(text, "utf8"));
  term.onBinary(text => enqueueInput(text, "binary"));
  function paste(text, id) {
    if (!running || blocked) { if (id) send("paste-result", { id, ok: false, reason: "blocked" }); return; }
    if (!normalText(text)) { if (id) send("paste-result", { id, ok: false, reason: "invalid" }); return; }
    if (!term.modes.bracketedPasteMode) {
      tell("OMP 입력란의 bracketed-paste 모드가 준비되지 않았습니다. Enter를 보내지 않았습니다.");
      if (id) send("paste-result", { id, ok: false, reason: "not-ready" });
      else send("diagnostic", { code: "paste-not-ready" });
      return;
    }
    capturePaste = id;
    pasteCaptured = false;
    try { term.paste(text); }
    finally { capturePaste = 0; }
    if (id && !pasteCaptured) send("paste-result", { id, ok: false, reason: "invalid" });
    term.focus();
  }
  // Keyboard clipboard paste goes through the same safe gate, not xterm's raw fallback.
  container.addEventListener("paste", event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    paste(event.clipboardData?.getData("text/plain") ?? "", 0);
  }, true);
  container.addEventListener("drop", event => { event.preventDefault(); event.stopImmediatePropagation(); }, true);
  container.addEventListener("dragover", event => event.preventDefault(), true);
  document.addEventListener("click", event => {
    if (event.target instanceof Element && event.target.closest("a")) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener("contextmenu", event => event.preventDefault());
  function size() {
    fitting = false;
    const proposed = fit.proposeDimensions();
    if (!proposed || container.clientWidth < 10 || container.clientHeight < 10) return;
    const cols = Math.max(2, Math.min(500, proposed.cols));
    const rows = Math.max(1, Math.min(300, proposed.rows));
    const changed = term.cols !== cols || term.rows !== rows;
    if (changed) term.resize(cols, rows);
    if (!initialized) { initialized = true; send("ready", { cols, rows }); tell("실제 OMP를 시작할 준비가 되었습니다."); }
    else if (changed) send("resize", { cols, rows });
  }
  function scheduleFit() { if (!fitting) { fitting = true; requestAnimationFrame(size); } }
  new ResizeObserver(scheduleFit).observe(container);
  function theme() {
    const style = getComputedStyle(container);
    const highContrast = matchMedia("(forced-colors: active)").matches;
    const probe = document.createElement("span");
    probe.style.color = "Highlight";
    container.append(probe);
    const highlight = getComputedStyle(probe).color;
    probe.style.color = "HighlightText";
    const highlightText = getComputedStyle(probe).color;
    probe.remove();
    term.options.theme = {
      background: style.backgroundColor, foreground: style.color, cursor: style.color, cursorAccent: style.backgroundColor,
      selectionBackground: highContrast ? highlight : "#496687",
      selectionInactiveBackground: highContrast ? highlight : "#3a4b60",
      selectionForeground: highContrast ? highlightText : undefined
    };
    scheduleFit();
  }
  matchMedia("(forced-colors: active)").addEventListener("change", theme);
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", () => { term.options.cursorBlink = false; });
  bridge.addEventListener("message", event => {
    try {
      const message = event.data;
      if (!message || message.v !== 1 || !positive(message.seq) || message.seq !== received + 1 || typeof message.type !== "string") throw new Error("protocol");
      switch (message.type) {
        case "output":
          if (!exact(message, ["v", "seq", "type", "id", "text"]) || !positive(message.id) || typeof message.text !== "string" || message.text.length > 8193 || outputBusy) throw new Error("protocol");
          outputBusy = true;
          // Only the terminal's write API receives untrusted output, never HTML/script.
          term.write(message.text, () => { outputBusy = false; send("output-ack", { id: message.id }); });
          break;
        case "input-ack":
          if (!exact(message, ["v", "seq", "type", "id"]) || !activeInput || message.id !== activeInput.id) throw new Error("protocol");
          queuedBytes -= activeInput.bytes;
          if (activeInput.paste) { send("paste-result", { id: activeInput.paste, ok: true, reason: "" }); tell("초안을 입력란에 붙여넣었습니다. Enter는 직접 눌러야 전송됩니다."); }
          activeInput = null;
          drainInput();
          break;
        case "paste":
          if (!exact(message, ["v", "seq", "type", "id", "text"]) || !positive(message.id) || typeof message.text !== "string") throw new Error("protocol");
          paste(message.text, message.id);
          break;
        case "focus":
          if (!exact(message, ["v", "seq", "type"])) throw new Error("protocol");
          term.focus();
          break;
        case "state":
          if (!exact(message, ["v", "seq", "type", "running"]) || typeof message.running !== "boolean") throw new Error("protocol");
          running = message.running;
          term.options.disableStdin = blocked || !running;
          if (!running) tell("소유한 OMP가 종료되었습니다. 화면과 준비 파일은 문서 생성·승인의 증거가 아닙니다.");
          else tell("실제 OMP 입력란입니다. 문서 생성·승인은 앱의 별도 검증 절차에서만 가능합니다.");
          break;
        case "blocked":
          if (!exact(message, ["v", "seq", "type"])) throw new Error("protocol");
          blocked = true; term.options.disableStdin = true;
          tell("앱이 입력을 차단했습니다. 소유한 OMP는 강제 종료되지 않았습니다. 앱 진단을 확인하세요.");
          break;
        default: throw new Error("protocol");
      }
      received = message.seq;
    } catch { fail("protocol"); }
  });
  theme();
  scheduleFit();
})();
