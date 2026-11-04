// Never insert package-generated markup in the active HTML document. Rebuild a
// narrow SVG vocabulary, then decode it only as an inert image (no SVG browsing).
const svgNs = "http://www.w3.org/2000/svg";
const elements = new Set(["svg", "g", "defs", "clipPath", "mask", "pattern", "linearGradient", "radialGradient", "stop", "path", "rect", "line", "polyline", "polygon", "circle", "ellipse", "text", "tspan", "image", "use", "title", "desc"]);
const attributes = new Set(["id", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "width", "height", "viewBox", "preserveAspectRatio", "transform", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "opacity", "clip-path", "clip-rule", "clipPathUnits", "mask", "maskUnits", "maskContentUnits", "patternUnits", "patternContentUnits", "patternTransform", "gradientUnits", "gradientTransform", "offset", "stop-color", "stop-opacity", "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "alignment-baseline", "letter-spacing", "word-spacing", "text-decoration", "textLength", "lengthAdjust", "dx", "dy", "rotate", "writing-mode", "direction", "unicode-bidi", "color"]);
const styleProperties = new Set(["fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "opacity", "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing", "word-spacing", "text-decoration", "writing-mode", "direction", "unicode-bidi", "color", "white-space"]);
const localReference = /^#[A-Za-z_][\w.:-]*$/;
function safeValue(value) {
    // Only local fragment paint references; no CSS escapes, @ rules, external
    // URLs, custom properties, or active/exotic function syntax.
    return value.length <= 1000000 && !/[\\<>@\u0000-\u0008]/u.test(value) && !/(?:javascript|data|https?|file|blob|expression|var)\s*:/iu.test(value) && !/var\s*\(/iu.test(value) && [...value.matchAll(/url\s*\(([^)]*)\)/giu)].every(match => /^['"]?#[A-Za-z_][\w.:-]*['"]?$/u.test(match[1].trim())) && !/url\s*\(/iu.test(value.replace(/url\s*\([^)]*\)/giu, ""));
}
export function sanitizeSvg(markup) {
    if (typeof markup !== "string" || markup.length > 32 * 1024 * 1024 || /<!DOCTYPE|<!ENTITY/iu.test(markup)) throw new Error("안전하게 표시할 수 없는 SVG입니다.");
    const parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
    const source = parsed.documentElement;
    if (source.localName !== "svg" || source.namespaceURI !== svgNs || parsed.getElementsByTagName("parsererror").length) throw new Error("SVG 쪽 데이터가 올바르지 않습니다.");
    const target = document.implementation.createDocument(svgNs, "svg", null);
    let nodes = 0;
    let omitted = false;
    function copy(input, depth = 0) {
        if (++nodes > 150000 || depth > 128) throw new Error("SVG 쪽 표시 예산을 넘었습니다.");
        if (input.nodeType === Node.TEXT_NODE) return target.createTextNode(input.nodeValue);
        if (input.nodeType !== Node.ELEMENT_NODE) return null;
        if (input.namespaceURI !== svgNs || !elements.has(input.localName)) { omitted = true; return null; }
        const output = target.createElementNS(svgNs, input.localName);
        for (const attr of input.attributes) {
            const name = attr.localName;
            const value = attr.value;
            if (attr.name === "xmlns" || attr.prefix === "xmlns") continue;
            if (name === "href" && (attr.namespaceURI === null || attr.namespaceURI === "http://www.w3.org/1999/xlink")) {
                const raster = input.localName === "image" && /^data:image\/(?:png|jpeg|gif|webp|bmp);base64,[A-Za-z0-9+/=\r\n]+$/u.test(value) && value.length <= 24 * 1024 * 1024;
                if (localReference.test(value) || raster) output.setAttribute("href", value); else omitted = true;
            } else if (attr.namespaceURI === null && name === "style") {
                const declarations = document.createElement("span").style;
                declarations.cssText = value;
                const safe = [];
                for (const key of declarations) {
                    const v = declarations.getPropertyValue(key);
                    if (styleProperties.has(key) && safeValue(v)) safe.push(`${key}:${v}`); else omitted = true;
                }
                if (safe.length) output.setAttribute("style", safe.join(";"));
            } else if (attr.namespaceURI === null && attributes.has(name) && safeValue(value)) {
                output.setAttribute(name, value);
            } else if (!name.startsWith("data-")) omitted = true;
        }
        for (const child of input.childNodes) {
            const safe = copy(child, depth + 1);
            if (safe) output.append(safe);
        }
        return output;
    }
    const clean = copy(source);
    target.replaceChild(clean, target.documentElement);
    const viewBox = clean.getAttribute("viewBox")?.trim().split(/[\s,]+/u).map(Number);
    const width = viewBox?.length === 4 ? viewBox[2] : Number.parseFloat(clean.getAttribute("width"));
    const height = viewBox?.length === 4 ? viewBox[3] : Number.parseFloat(clean.getAttribute("height"));
    if (!(width > 0 && height > 0 && width <= 50000 && height <= 50000 && Number.isFinite(width + height))) throw new Error("SVG 쪽 크기가 올바르지 않습니다.");
    return { markup: new XMLSerializer().serializeToString(target), width, height, omitted };
}
