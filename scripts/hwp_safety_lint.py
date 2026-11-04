"""Bounded Python AST policy lint; not a sandbox or proof of document fidelity.
Rules catch known unsafe reference patterns without importing/executing source.
Runtime approval/source/layout gates remain mandatory. No inline suppressions.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

DANGEROUS_ACTIONS = {"AllReplace", "TableDeleteRow", "TableDeleteColumn"}
AUTO_FORMAT = {"autofit", "fit_rows", "refit_cell", "clear_guidance", "strip_guidance"}
WRITE_METHODS = {
    "write_text",
    "write_bytes",
    "write_json",
    "save_to_path",
    "SaveAs",
    "save_as",
}


def _string(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _string(node.left), _string(node.right)
        return a + b if a is not None and b is not None else None
    return None


def _strings(node) -> list[str]:
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + "." + node.attr
    return ""


def check_source(source: str, filename: str = "scripts/fixture.py") -> list[dict]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [
            {
                "file": filename,
                "line": exc.lineno or 1,
                "column": exc.offset or 1,
                "rule": "HWP000",
                "message": "Python syntax error: " + exc.msg,
            }
        ]
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = (
                    item.name if item.asname else item.name.split(".")[0]
                )
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = (
                    (node.module or "") + "." + item.name
                )
    findings = []
    path = Path(filename.replace("\\", "/"))
    fixture = path.parts[0] == "tests" if path.parts else False

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scopes = []
            self.bindings = [{}]

        def expression(self, node):
            visited = set()
            while isinstance(node, ast.Name) and node.id not in visited:
                visited.add(node.id)
                value = next(
                    (
                        scope[node.id]
                        for scope in reversed(self.bindings)
                        if node.id in scope
                    ),
                    None,
                )
                if value is None:
                    break
                node = value
            return node

        def report(self, node, rule, message):
            findings.append(
                {
                    "file": filename,
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "rule": rule,
                    "message": message,
                }
            )

        def scoped(self, node):
            self.scopes.append(node.name)
            self.bindings.append({})
            self.generic_visit(node)
            self.bindings.pop()
            self.scopes.pop()

        visit_FunctionDef = scoped
        visit_AsyncFunctionDef = scoped
        visit_ClassDef = scoped

        def resolved(self, node):
            name = _name(self.expression(node))
            first, separator, rest = name.partition(".")
            return aliases.get(first, first) + (separator + rest if separator else "")

        def visit_Assign(self, node):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bindings[-1][target.id] = self.expression(node.value)
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "Visible"
                    and not (
                        isinstance(node.value, ast.Constant)
                        and node.value.value is False
                    )
                ):
                    self.report(
                        node,
                        "HWP002",
                        "Owned native windows must explicitly stay hidden (Visible=False).",
                    )
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if (
                isinstance(node.target, ast.Attribute)
                and node.target.attr == "Visible"
                and not (
                    isinstance(node.value, ast.Constant) and node.value.value is False
                )
            ):
                self.report(
                    node,
                    "HWP002",
                    "Owned native windows must explicitly stay hidden (Visible=False).",
                )
            self.generic_visit(node)

        def visit_Call(self, node):
            name = self.resolved(node.func)
            method = name.split(".")[-1]
            if (
                method == "setattr"
                and len(node.args) >= 3
                and _string(node.args[1]) == "Visible"
                and not (
                    isinstance(node.args[2], ast.Constant)
                    and node.args[2].value is False
                )
            ):
                self.report(
                    node,
                    "HWP002",
                    "Owned native windows must explicitly stay hidden (Visible=False).",
                )
            if method in {"GetActiveObject", "GetObject"} or (
                name.endswith(".Hwp") and name.startswith("pyhwpx.")
            ):
                self.report(
                    node,
                    "HWP001",
                    "Do not attach pyhwpx/default COM to user documents; use owned DispatchEx.",
                )
            if (
                method in {"Dispatch", "EnsureDispatch"}
                and node.args
                and (
                    (_string(self.expression(node.args[0])) or "")
                    .lower()
                    .startswith("hwp")
                )
            ):
                self.report(
                    node,
                    "HWP001",
                    "HWP COM creation must use DispatchEx, not Dispatch/EnsureDispatch(progID).",
                )
            if (
                method in {"Run", "Execute", "run", "execute"}
                and node.args
                and _string(self.expression(node.args[0])) in DANGEROUS_ACTIONS
            ):
                self.report(
                    node,
                    "HWP003",
                    "Global replacement/deletion is outside a bounded edit contract.",
                )
            if method in AUTO_FORMAT or any(
                k.arg == "flatten"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in node.keywords
            ):
                self.report(
                    node,
                    "HWP004",
                    "Do not auto-fit/rewrite styles/flatten or delete template guidance.",
                )
            if (
                method in {"replace", "Replace"}
                and node.args
                and _string(self.expression(node.args[0])) == ""
            ):
                self.report(
                    node,
                    "HWP005",
                    "Empty-string replacement cannot identify a blank field; bind an exact target.",
                )
            if method in {"findall", "iter", "iterfind", "xpath"} and node.args:
                selector = "".join(_strings(self.expression(node.args[0])))
                if "linesegarray" in selector and (
                    method == "iter" or "//" in selector
                ):
                    self.report(
                        node,
                        "HWP006",
                        "Do not traverse/clear all line caches; select one approved paragraph.",
                    )
            if method in {"PutFieldText", "put_field_text"}:
                self.report(
                    node,
                    "HWP008",
                    "Direct field writes disabled until a fresh UI-approved field executor/gate exists.",
                )
            # Only exact, inspected UI endpoint may WRITE a real approval. Tests use fake temp contracts.
            if not fixture and method in WRITE_METHODS | {"copyfile", "copy2", "open"}:
                if method == "open":
                    mode_index = 0 if isinstance(node.func, ast.Attribute) else 1
                    mode = next(
                        (k.value for k in node.keywords if k.arg == "mode"),
                        node.args[mode_index] if len(node.args) > mode_index else None,
                    )
                    if not any(flag in (_string(mode) or "") for flag in "wax+"):
                        self.generic_visit(node)
                        return
                    target = (
                        node.func.value
                        if isinstance(node.func, ast.Attribute)
                        else node.args[0]
                        if node.args
                        else None
                    )
                elif method in {"copyfile", "copy2"}:
                    target = (
                        node.args[1]
                        if len(node.args) > 1
                        else next(
                            (k.value for k in node.keywords if k.arg == "dst"), None
                        )
                    )
                else:
                    target = (
                        node.func.value
                        if method in {"write_text", "write_bytes"}
                        and isinstance(node.func, ast.Attribute)
                        else node.args[0]
                        if node.args
                        else None
                    )
                if target is not None:
                    literals = _strings(self.expression(target))
                    ui_writer = (
                        path.as_posix() == "scripts/hwp_review.py"
                        and self.scopes
                        in (
                            ["create_review_server", "Handler", "do_POST"],
                            ["create_review_server", "TemplateHandler", "do_POST"],
                        )
                    )
                    if (
                        any("approved-plan.json" in text for text in literals)
                        and not ui_writer
                    ):
                        self.report(
                            node,
                            "HWP007",
                            "Only the existing exact local review UI may write approval records.",
                        )
                    report_ui_writer = (
                        path.as_posix() == "scripts/hwp_review.py"
                        and self.scopes in (
                            ["create_review_server", "TemplateHandler", "do_POST"],
                            ["create_review_server", "TemplateHandler", "persist_authority"],
                        )
                    )
                    if (
                        any(
                            "draft-permission.json" in text
                            or "acceptance.json" in text
                            or "-acceptance.json" in text
                            for text in literals
                        )
                        and not report_ui_writer
                    ):
                        self.report(
                            node, "HWP007",
                            "Only the existing local review UI may persist draft permission or artifact acceptance.",
                        )
                    if any(
                        text.replace("\\", "/").startswith("example/")
                        or text == "example"
                        or Path(text.replace("\\", "/")).name
                        in {"baseline.hwp", "baseline.hwpx", "baseline.pdf"}
                        for text in literals
                    ):
                        # Baseline creation is allowed ONLY in the existing prepare path (new workspace guarded at runtime).
                        if not (
                            path.as_posix() == "scripts/hwp_review.py"
                            and self.scopes == ["prepare"]
                        ):
                            self.report(
                                node,
                                "HWP007",
                                "Do not overwrite example/original baselines; create a NEW owned output.",
                            )
            self.generic_visit(node)

    Visitor().visit(tree)
    return sorted(
        findings, key=lambda item: (item["line"], item["column"], item["rule"])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    paths = set()
    for path in args.paths:
        if not path.exists():
            parser.error("Missing lint coverage path: " + str(path))
        paths.update(path.rglob("*.py") if path.is_dir() else [path])
    failed = False
    for path in sorted(paths):
        for finding in check_source(path.read_text(encoding="utf-8"), path.as_posix()):
            failed = True
            print(
                f"{finding['file']}:{finding['line']}:{finding['column']}: {finding['rule']} {finding['message']}"
            )
    if failed:
        raise SystemExit(1)
    print(
        f"PASS: HWP AST safety rules ({len(paths)} Python files; static checks, NOT native fidelity proof)"
    )


if __name__ == "__main__":
    main()
