import init, { HwpDocument } from "./vendor/rhwp/rhwp.js";
import { sanitizeSvg } from "./svg-safety.js";

const byId = id => document.getElementById(id);
const sheet = byId("sheet");
const status = byId("status");
const viewport = byId("viewport");
const controls = ["previous", "next", "zoom-out", "zoom-in", "fit"].map(byId);
const maximumExcerpt = 2048;
const maximumSlots = 12;
const maximumRunway = 8000000; // Stay below Chromium's layout/scroll extent ceiling.
const gap = 16;
let binding = null;
let documentHandle = null;
let pages = [];
let page = 0;
let scale = 1;
let fitting = true;
let failed = false;
let settled = false;
let pumping = false;
let scheduled = false;
let totalHeight = 0;
let runway = 0;
let logicalTop = 0;
let lastReported = -1;
let selected = null;
const mounted = new Map();
const measurement = document.createElement("canvas").getContext("2d");
if (!measurement) throw new Error("글꼴 폭 측정 기능을 사용할 수 없습니다.");
globalThis.measureTextWidth = (font, text) => {
    measurement.font = font;
    return measurement.measureText(text).width;
};
const engine = init({ module_or_path: new URL("./vendor/rhwp/rhwp_bg.wasm", import.meta.url) });
// Data-only renderer observations; never document/process/model/approval actions.
function notify(type, details = {}) {
    if (binding) window.chrome.webview.postMessage({ type, ...binding, ...details });
}
const indexValue = value => Number.isInteger(value) && value >= 0 && value <= 1000000;
const finite = value => typeof value === "number" && Number.isFinite(value);
function closed(value, keys) {
    return value && typeof value === "object" && !Array.isArray(value) &&
        Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function parseBounded(json, maximum) {
    if (typeof json !== "string" || json.length > maximum) throw new Error("쪽 위치 데이터가 표시 한도를 넘었습니다.");
    return JSON.parse(json);
}
function updateControls() {
    controls.forEach(control => { control.disabled = failed || !settled; });
    byId("previous").disabled ||= page === 0;
    byId("next").disabled ||= page === pages.length - 1;
    byId("page-number").textContent = pages.length ? `${page + 1} / ${pages.length}쪽` : "쪽 준비 중";
    byId("zoom").textContent = `${Math.round((pages[page]?.scale ?? scale) * 100)}%`;
}
function release(index) {
    const slot = mounted.get(index);
    if (!slot) return;
    slot.image?.removeAttribute("src");
    if (slot.url) URL.revokeObjectURL(slot.url);
    slot.element.remove();
    mounted.delete(index);
}
function fail(error) {
    if (failed) return;
    failed = true;
    for (const index of mounted.keys()) release(index);
    if (documentHandle) { documentHandle.free(); documentHandle = null; }
    status.textContent = "로컬 쪽 미리보기에 실패했습니다. 원본은 변경되지 않았습니다.";
    status.hidden = false;
    status.dataset.kind = "error";
    updateControls();
    notify("error", { message: String(error?.message ?? error).slice(0, 1000) });
}
function pageAt(y) {
    let low = 0;
    let high = pages.length - 1;
    while (low < high) {
        const middle = Math.floor((low + high + 1) / 2);
        if (pages[middle].top <= y) low = middle; else high = middle - 1;
    }
    return low;
}
function scrollRatio() {
    return Math.max(0, totalHeight + 32 - viewport.clientHeight) / Math.max(1, runway + 32 - viewport.clientHeight);
}
function readScroll() {
    logicalTop = viewport.scrollTop * scrollRatio();
}
function scrollLogical(top, left = viewport.scrollLeft) {
    logicalTop = Math.max(0, Math.min(Math.max(0, totalHeight + 32 - viewport.clientHeight), top));
    viewport.scrollTo(left, logicalTop / (scrollRatio() || 1));
    refresh();
}
function position(slot, info) {
    slot.element.style.top = `${info.top - logicalTop + viewport.scrollTop}px`;
    slot.element.style.width = `${info.displayWidth}px`;
    slot.element.style.height = `${info.displayHeight}px`;
    slot.element.style.left = `${Math.max(0, (sheet.clientWidth - info.displayWidth) / 2)}px`;
    const target = slot.element.querySelector(".passage-target");
    if (target && selected?.page === info.index) {
        const factor = Math.min(info.displayWidth / slot.svgWidth, info.displayHeight / slot.svgHeight);
        target.style.left = `${(info.displayWidth - slot.svgWidth * factor) / 2 + selected.x * factor}px`;
        target.style.top = `${(info.displayHeight - slot.svgHeight * factor) / 2 + selected.y * factor}px`;
        target.style.width = `${selected.w * factor}px`;
        target.style.height = `${selected.h * factor}px`;
    }
}
function resizePages() {
    if (!pages.length || failed) return;
    readScroll();
    const anchorIndex = settled ? pageAt(logicalTop) : 0;
    const anchor = pages[anchorIndex];
    const fraction = (logicalTop - anchor.top) / Math.max(1, anchor.displayHeight);
    const horizontal = viewport.scrollLeft;
    let top = 0;
    let width = Math.max(1, viewport.clientWidth - 32);
    for (const info of pages) {
        info.scale = fitting ? Math.max(.1, Math.min(3, (viewport.clientWidth - 32) / info.width)) : scale;
        info.displayWidth = info.width * info.scale;
        info.displayHeight = info.height * info.scale;
        info.top = top;
        top += info.displayHeight + gap;
        width = Math.max(width, info.displayWidth);
    }
    totalHeight = Math.max(0, top - gap);
    runway = Math.min(maximumRunway, totalHeight);
    sheet.style.height = `${runway}px`;
    sheet.style.width = `${width}px`;
    scrollLogical(anchor.top + fraction * anchor.displayHeight, horizontal);
}
function desiredPages() {
    const first = pageAt(Math.max(0, logicalTop - viewport.clientHeight));
    const last = pageAt(logicalTop + viewport.clientHeight * 2);
    const indices = [];
    for (let distance = 0; indices.length < maximumSlots && (page - distance >= first || page + distance <= last); distance++) {
        if (page - distance >= first) indices.push(page - distance);
        if (distance && page + distance <= last && indices.length < maximumSlots) indices.push(page + distance);
    }
    return indices;
}
function makeSlot(index) {
    const element = document.createElement("section");
    element.className = "page-slot";
    element.setAttribute("role", "region");
    element.setAttribute("aria-label", `${index + 1}/${pages.length}쪽 읽기 전용 미리보기`);
    element.setAttribute("aria-busy", "true");
    element.dataset.page = String(index + 1);
    sheet.append(element);
    const slot = { element, image: null, url: null, omitted: false };
    mounted.set(index, slot);
    position(slot, pages[index]);
    return slot;
}
function reportCurrent() {
    const slot = mounted.get(page);
    if (!settled || !slot?.image) return;
    status.textContent = slot.omitted ? "미지원 그리기 요소를 제외했습니다. 한컴에서 원본 배치를 확인하세요." : "";
    status.dataset.kind = slot.omitted ? "warning" : "ready";
    status.hidden = !slot.omitted;
    if (lastReported !== page) {
        lastReported = page;
        notify("rendered", { page: page + 1, pageCount: pages.length });
    }
}
function refresh() {
    if (!pages.length || failed) return;
    readScroll();
    page = pageAt(logicalTop + Math.min(128, viewport.clientHeight / 4));
    const desired = desiredPages();
    for (const index of mounted.keys()) if (!desired.includes(index)) release(index);
    for (const index of desired) position(mounted.get(index) ?? makeSlot(index), pages[index]);
    updateControls();
    reportCurrent();
    if (settled) pump().catch(fail);
}
async function render(index) {
    const slot = mounted.get(index) ?? makeSlot(index);
    // Real source bytes -> bounded sanitized SVG -> decoded inert image only.
    const clean = sanitizeSvg(documentHandle.renderPageSvg(index));
    const info = pages[index];
    if (Math.abs(clean.width - info.width) > 1 || Math.abs(clean.height - info.height) > 1) throw new Error("쪽 표시와 위치 좌표가 일치하지 않습니다.");
    const url = URL.createObjectURL(new Blob([clean.markup], { type: "image/svg+xml" }));
    const image = new Image();
    image.alt = `HWPX ${index + 1}/${pages.length}쪽 로컬 미리보기. 본문을 두 번 클릭하면 수정 메모를 추가합니다.`;
    image.draggable = false;
    image.src = url;
    try {
        await image.decode();
        if (failed || mounted.get(index) !== slot) { image.removeAttribute("src"); URL.revokeObjectURL(url); return; }
        slot.url = url;
        slot.image = image;
        slot.omitted = clean.omitted;
        slot.svgWidth = clean.width;
        slot.svgHeight = clean.height;
        slot.element.prepend(image);
        slot.element.setAttribute("aria-busy", "false");
        image.addEventListener("click", event => selectPassage(index, event, false));
        image.addEventListener("dblclick", event => selectPassage(index, event, true));
        // Background WebViews can suspend animation frames; mount/decode plus a
        // task boundary remains the acknowledgement, not a claim of painted pixels.
        await new Promise(resolve => setTimeout(resolve, 0));
        if (!failed && mounted.get(index) === slot) reportCurrent();
    } catch (error) { image.removeAttribute("src"); URL.revokeObjectURL(url); throw error; }
}
async function pump() {
    if (pumping || failed) return;
    pumping = true;
    try {
        while (!failed) {
            const index = desiredPages().find(candidate => mounted.has(candidate) && !mounted.get(candidate).image);
            if (index === undefined) break;
            await render(index);
        }
    } finally { pumping = false; }
}
function clearTarget() {
    for (const slot of mounted.values()) slot.element.querySelector(".passage-target")?.remove();
    selected = null;
}
function resolvePassage(index, x, y) {
    const info = pages[index];
    // hitTest snaps blank areas to nearby lines. Therefore it is NOT alone a
    // passage hit: require an actual source-addressed engine TextRun bbox too.
    // Pinned source: 1a76570e8, queries/cursor_rect.rs::hit_test_native and
    // queries/rendering.rs::get_page_text_layout_native (no text matching).
    const hit = parseBounded(documentHandle.hitTest(index, x, y), 16384);
    if (!closed(hit, ["sectionIndex", "paragraphIndex", "charOffset", "cursorRect"]) ||
        !indexValue(hit.sectionIndex) || hit.sectionIndex !== info.sectionIndex ||
        !indexValue(hit.paragraphIndex) || !indexValue(hit.charOffset)) return null;
    const rect = hit.cursorRect;
    if (!closed(rect, ["pageIndex", "x", "y", "height"]) || rect.pageIndex !== index ||
        ![rect.x, rect.y, rect.height].every(finite) || rect.height <= 0) return null;
    // These domains do not have the top-level section/paragraph address promised
    // to the host. All cells/textboxes are also rejected by the closed hit schema.
    if (parseBounded(documentHandle.hitTestHeaderFooter(index, x, y), 4096).hit !== false ||
        parseBounded(documentHandle.hitTestFootnote(index, x, y), 4096).hit !== false) return null;
    const layout = parseBounded(documentHandle.getPageTextLayout(index), 8 * 1024 * 1024);
    if (!closed(layout, ["runs"]) || !Array.isArray(layout.runs) || layout.runs.length > 100000) return null;
    const candidates = layout.runs.filter(run => run && run.secIdx === hit.sectionIndex && run.paraIdx === hit.paragraphIndex &&
        !["parentParaIdx", "controlIdx", "cellIdx", "cellParaIdx", "cellPath"].some(key => Object.hasOwn(run, key)) &&
        indexValue(run.charStart) && typeof run.text === "string" && run.text.trim().length &&
        [run.x, run.y, run.w, run.h].every(finite) && run.w > 0 && run.h > 0 &&
        x >= run.x && x <= run.x + run.w && y >= run.y && y <= run.y + run.h &&
        hit.charOffset >= run.charStart && hit.charOffset <= run.charStart + Array.from(run.text).length);
    if (candidates.length !== 1) return null;
    const run = candidates[0];
    // getTextRange indexes Rust Unicode scalar values, returns raw paragraph
    // text, and clamps count to its end (commands/text_editing.rs). Fetch one
    // extra scalar, then bound UTF-16 without splitting a surrogate pair.
    const raw = documentHandle.getTextRange(hit.sectionIndex, hit.paragraphIndex, 0, maximumExcerpt + 1);
    if (typeof raw !== "string" || raw.length > (maximumExcerpt + 1) * 2 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(raw)) return null;
    let text = "";
    for (const character of raw) {
        if (text.length + character.length > maximumExcerpt) break;
        text += character;
    }
    if (!text.trim()) return null;
    // Engine charX contains N+1 run-relative scalar boundaries. A whitespace
    // advance is not a passage hit even when enclosed by the whole run bbox.
    const characters = Array.from(run.text);
    if (!Array.isArray(run.charX) || run.charX.length !== characters.length + 1 ||
        !run.charX.every((value, offset) => finite(value) && (offset === 0 || value >= run.charX[offset - 1]))) return null;
    const localX = x - run.x;
    const characterIndex = characters.findIndex((_, offset) => localX >= run.charX[offset] && localX < run.charX[offset + 1]);
    if (characterIndex < 0 || /[\s\u0000-\u001f\u007f\ufffc]/u.test(characters[characterIndex])) return null;
    return { sectionIndex: hit.sectionIndex, paragraphIndex: hit.paragraphIndex, text, truncated: text.length !== raw.length,
        x: run.x, y: run.y, w: run.w, h: run.h };
}
function selectPassage(index, event, emit) {
    if (!event.isTrusted || failed || !settled) return;
    clearTarget();
    const slot = mounted.get(index);
    if (!slot?.image) return;
    const bounds = slot.image.getBoundingClientRect();
    if (!(bounds.width > 0 && bounds.height > 0)) return;
    const info = pages[index];
    // SVG's unrounded zero-origin viewBox is the engine coordinate space;
    // getPageInfo rounds metadata to one decimal and is only slot geometry.
    const factor = Math.min(bounds.width / slot.svgWidth, bounds.height / slot.svgHeight);
    const x = (event.clientX - bounds.left - (bounds.width - slot.svgWidth * factor) / 2) / factor;
    const y = (event.clientY - bounds.top - (bounds.height - slot.svgHeight * factor) / 2) / factor;
    if (!(x >= 0 && y >= 0 && x <= slot.svgWidth && y <= slot.svgHeight)) return;
    try {
        const target = resolvePassage(index, x, y);
        if (!target) return;
        selected = { page: index, ...target };
        const indicator = document.createElement("span");
        indicator.className = "passage-target";
        indicator.setAttribute("role", "img");
        indicator.setAttribute("aria-label", "선택한 본문. 두 번 클릭하면 오른쪽에 수정 메모를 추가합니다.");
        slot.element.append(indicator);
        position(slot, info);
        if (emit) notify("passage", { page: index + 1, sectionIndex: target.sectionIndex, paragraphIndex: target.paragraphIndex,
            text: target.text, truncated: target.truncated });
    } catch (error) { fail(error); }
}
window.chrome.webview.addEventListener("message", async event => {
    const message = event.data;
    if (binding || message?.type !== "show" || !Number.isInteger(message.generation) || typeof message.identity !== "string" || message.identity.length < 1 || message.identity.length > 1024 || !/^[a-f0-9]{64}$/u.test(message.sha256)) return;
    binding = Object.freeze({ generation: message.generation, identity: message.identity, sha256: message.sha256 });
    try {
        await engine;
        const response = await fetch("/document.hwpx", { cache: "no-store", credentials: "omit", redirect: "error" });
        if (!response.ok) throw new Error("현재 HWPX 바이트를 읽지 못했습니다.");
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.byteLength < 1 || bytes.byteLength > 64 * 1024 * 1024) throw new Error("HWPX 표시 크기 한도를 넘었습니다.");
        const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
        const hash = Array.from(digest, value => value.toString(16).padStart(2, "0")).join("");
        if (hash !== binding.sha256) throw new Error("현재 HWPX 해시가 다릅니다.");
        documentHandle = new HwpDocument(bytes);
        if (documentHandle.getSourceFormat() !== "hwpx") throw new Error("이 미리보기는 검증된 HWPX만 표시합니다.");
        const count = documentHandle.pageCount();
        if (!Number.isInteger(count) || count < 1 || count > 10000) throw new Error("HWPX 쪽 수가 표시 한도를 벗어났습니다.");
        // Metadata only: no SVG generation/image decoding for distant pages.
        for (let index = 0; index < count; index++) {
            const info = parseBounded(documentHandle.getPageInfo(index), 65536);
            if (info.pageIndex !== index || !indexValue(info.sectionIndex) || ![info.width, info.height].every(finite) ||
                info.width <= 0 || info.height <= 0 || info.width > 50000 || info.height > 50000) throw new Error("쪽 크기·구역 정보가 올바르지 않습니다.");
            pages.push({ index, width: info.width, height: info.height, sectionIndex: info.sectionIndex, top: 0, displayHeight: 1, scale: 1 });
            if (index % 256 === 255) await new Promise(resolve => setTimeout(resolve, 0));
        }
        resizePages();
        await render(0);
        if (failed || !mounted.get(0)?.image) throw new Error("첫 쪽 표시가 확인되지 않았습니다.");
        settled = true;
        // First mounted/decoded page MUST be reported before scroll observations.
        lastReported = 0;
        notify("rendered", { page: 1, pageCount: pages.length });
        viewport.setAttribute("aria-busy", "false");
        refresh();
    } catch (error) { fail(error); }
});
byId("previous").addEventListener("click", () => { if (settled && page > 0) scrollLogical(pages[page - 1].top); });
byId("next").addEventListener("click", () => { if (settled && page < pages.length - 1) scrollLogical(pages[page + 1].top); });
byId("zoom-in").addEventListener("click", () => { scale = Math.min(3, (pages[page]?.scale ?? scale) + .1); fitting = false; resizePages(); });
byId("zoom-out").addEventListener("click", () => { scale = Math.max(.1, (pages[page]?.scale ?? scale) - .1); fitting = false; resizePages(); });
byId("fit").addEventListener("click", () => { fitting = true; resizePages(); });
viewport.addEventListener("scroll", () => {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; refresh(); }, 16);
}, { passive: true });
// Only oversized documents compress the native scrollbar. Preserve ordinary
// wheel/key distances in logical page pixels while thumb dragging reaches all pages.
viewport.addEventListener("wheel", event => {
    if (!settled || scrollRatio() <= 1 || event.ctrlKey || event.deltaX || event.shiftKey) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 18 : event.deltaMode === 2 ? viewport.clientHeight : 1;
    readScroll();
    scrollLogical(logicalTop + event.deltaY * unit);
}, { passive: false });
viewport.addEventListener("keydown", event => {
    if (event.target !== viewport || !settled || scrollRatio() <= 1 || event.ctrlKey || event.altKey || event.metaKey) return;
    const distances = { ArrowDown: 40, ArrowUp: -40, PageDown: viewport.clientHeight * .9, PageUp: -viewport.clientHeight * .9,
        " ": viewport.clientHeight * (event.shiftKey ? -.9 : .9), Home: -totalHeight, End: totalHeight };
    if (!Object.hasOwn(distances, event.key)) return;
    event.preventDefault();
    readScroll();
    scrollLogical(logicalTop + distances[event.key]);
});
new ResizeObserver(resizePages).observe(viewport);
window.addEventListener("pagehide", () => {
    failed = true;
    for (const index of mounted.keys()) release(index);
    if (documentHandle) { documentHandle.free(); documentHandle = null; }
});
window.chrome.webview.postMessage({ type: "ready" });
engine.catch(error => {
    if (binding) fail(error);
    else {
        status.textContent = "로컬 WASM 렌더러를 불러오지 못했습니다.";
        status.hidden = false;
        status.dataset.kind = "error";
    }
});
