"""User-selected folder + one HWP + a NEW _review subfolder. No hidden config.

Import is a byte-for-byte exclusive COPY. Native preparation is a separate human
UI action, using existing engine gates and a preparation-only subprocess worker.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import re
import uuid
import shutil
import subprocess
import sys

from hwp_resources import frozen

REVIEW_DIR = "_review"


@dataclass(frozen=True)
class ProjectFolder:
    root: Path
    state: str
    source: Path | None
    workspace: Path


def scan_folder(path: Path) -> ProjectFolder:
    path = path.expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("작업 폴더를 선택하세요.")
    if (path / "manifest.json").is_file():
        return ProjectFolder(path, "ready", None, path)  # older prepared folders
    workspace = path / REVIEW_DIR
    if workspace.is_symlink():
        raise ValueError("검토 자료 폴더는 바로가기/심볼릭 링크일 수 없습니다.")
    if (workspace / "manifest.json").is_file():
        return ProjectFolder(path, "ready", None, workspace)
    if workspace.exists():
        raise ValueError(
            "이 폴더에는 미완료/실패한 준비 자료(_review)가 있습니다. 자동 삭제·재시도하지 않습니다. 상태를 확인하거나 별도의 새 작업 폴더에서 시작하세요."
        )
    documents = sorted(
        p for p in path.iterdir() if p.suffix.lower() == ".hwp" and not p.is_dir()
    )
    if len(documents) > 1:
        raise ValueError(
            "한 작업 폴더에는 HWP 한 개를 넣어주세요. 여러 문서는 별도 폴더로 나눕니다."
        )
    if documents and (
        documents[0].is_symlink() or documents[0].resolve().parent != path
    ):
        raise ValueError("HWP 원본은 작업 폴더 안의 실제 파일이어야 합니다.")
    return ProjectFolder(
        path,
        "staged" if documents else "empty",
        documents[0] if documents else None,
        workspace,
    )


def import_hwp(root: Path, source: Path) -> ProjectFolder:
    import hwp_review as review

    project = scan_folder(root)
    if project.state != "empty":
        raise ValueError(
            "HWP가 없는 작업 폴더에서만 추가할 수 있습니다. 기존 문서는 교체하지 않습니다."
        )
    if source.is_symlink():
        raise ValueError("바로가기 대신 실제 HWP 파일을 선택하세요.")
    source = source.expanduser().resolve(strict=True)
    if source.suffix.lower() != ".hwp" or not source.is_file():
        raise ValueError(
            "HWP5 .hwp 파일을 선택하세요. HWPX/암호/배포용/전자서명 문서는 이 준비 흐름에서 지원하지 않습니다."
        )
    audit = review.hwp_audit(source)
    target = project.root / source.name
    with source.open("rb") as original, target.open("xb") as copied:
        shutil.copyfileobj(original, copied, length=1024 * 1024)
    if (
        review.file_hash(target) != audit["sha256"]
        or review.file_hash(source) != audit["sha256"]
    ):
        # Keep failed input for explicit owner inspection; never remove/replace it.
        raise ValueError(
            "복사 중 원본이 바뀌었거나 해시가 다릅니다. 준비하지 않고 중단했습니다. 작업 폴더의 복사본을 직접 확인하세요."
        )
    return scan_folder(project.root)


def new_project(source: Path, *, base: Path | None = None) -> ProjectFolder:
    """Explicit document picker action; new local copy, no Native/auto preparation."""
    import hwp_review as review

    if source.is_symlink() or source.suffix.lower() != ".hwp":
        raise ValueError("실제 .hwp 파일을 선택하세요.")
    source = source.expanduser().resolve(strict=True)
    review.hwp_audit(source)  # unsupported input MUST NOT leave a new project
    if base is None:
        location = os.environ.get("LOCALAPPDATA")
        if not location:
            raise RuntimeError(
                "PC의 사용자 저장 위치를 확인할 수 없습니다. 작업 폴더를 직접 선택하세요."
            )
        base = Path(location) / "HwpReview" / "Projects"
    if not base.is_absolute() or any(
        p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
        for p in (base, *base.parents)
    ):
        raise ValueError(
            "새 작업은 실제 사용자 로컬 폴더에만 저장합니다. 작업 폴더를 직접 선택하세요."
        )
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source.stem).strip(" .")[:60] or "문서"
    project = base / (name + "-" + uuid.uuid4().hex[:10])
    project.mkdir(parents=True, exist_ok=False)
    return import_hwp(project, source)


class PreparationCancelled(RuntimeError):
    """Cooperative STOP between phases, never a forced COM/PID interruption."""


def step_paths(workspace: Path, phase: int):
    """Paths are derived, not supplied by document text or arbitrary CLI outputs."""
    import hwp_review as review

    if phase not in (0, 1, 2):
        raise ValueError("Unknown baseline preparation phase")
    if workspace.is_symlink():
        raise ValueError("Preparation folder must not be a link")
    workspace = workspace.resolve(strict=True)
    if workspace.name != REVIEW_DIR:
        raise ValueError("Preparation requires the owned _review folder")
    preparing = review.read_json(workspace / "preparing.json")
    source = Path(preparing["source"])
    if (
        not source.is_absolute()
        or source.is_symlink()
        or source.resolve(strict=True).parent != workspace.parent
        or source.suffix.lower() != ".hwp"
    ):
        raise ValueError(
            "Preparation source must be the HWP inside its selected project folder"
        )
    if review.file_hash(source) != preparing["source_audit"]["sha256"]:
        raise ValueError("Preparation source changed")
    if (workspace / "manifest.json").exists():
        raise ValueError("Already prepared baselines are immutable")
    inputs = (source, workspace / "baseline.hwpx", workspace / "roundtrip.hwp")
    outputs = (
        (workspace / "baseline.pdf", workspace / "baseline.hwpx", None),
        (workspace / "roundtrip.pdf", None, workspace / "roundtrip.hwp"),
        (workspace / "roundtrip-reopened.pdf", None, None),
    )
    reply = workspace / f"native-prepare-step-{phase + 1}.json"
    if not inputs[phase].is_file() or inputs[phase].is_symlink():
        raise ValueError("Preparation input is missing or linked")
    if reply.exists() or any(path and path.exists() for path in outputs[phase]):
        raise FileExistsError(
            "Preparation phase output already exists; no overwrite or retry"
        )
    return inputs[phase], outputs[phase], reply


class BaselineExporter:
    def __init__(self, workspace: Path):
        self.workspace, self.phase = workspace, 0

    def __call__(self, source: Path, pdf: Path, *, hwpx=None, hwp=None):
        import hwp_review as review

        expected, outputs, reply = step_paths(self.workspace, self.phase)
        if source.resolve() != expected.resolve() or (pdf, hwpx, hwp) != outputs:
            raise ValueError(
                "Only the exact three no-edit preparation exports are supported"
            )
        if frozen():
            command = [
                sys.executable,
                "--baseline-export",
                str(self.workspace),
                "--baseline-phase",
                str(self.phase),
            ]
        else:
            command = [
                sys.executable,
                str(Path(__file__).with_name("hwp_desktop.py")),
                "--baseline-export",
                str(self.workspace),
                "--baseline-phase",
                str(self.phase),
            ]
        try:
            result = subprocess.run(
                command,
                # Frozen GUI workers never read stdin. Inheriting a pipe being
                # consumed by the parent's reader can block bootloader startup.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=240,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "한글 준비 시간이 초과됐습니다. 소유자가 한글/접근 대화상자를 확인하세요. 한글을 강제 종료하거나 자동 재시도하지 않았습니다."
            ) from error
        if reply.is_symlink() or not reply.is_file():
            raise RuntimeError(
                "한글 준비 단계가 완료 응답 없이 중단됐습니다. 실패한 준비 폴더를 유지합니다."
            )
        payload = review.read_json(reply)
        if result.returncode or payload.get("ok") is not True:
            raise RuntimeError(
                "한글 준비 실패: " + str(payload.get("error", "unknown"))
            )
        if payload.get("phase") != self.phase or not isinstance(
            payload.get("native"), dict
        ):
            raise ValueError(
                "Preparation reply does not match the exact requested phase"
            )
        self.phase += 1
        return payload["native"]


def prepare_project(root: Path, *, progress=None, cancelled=None):
    import hwp_review as review
    from hwp_security import registered_security_module

    project = scan_folder(root)
    if project.state != "staged" or project.source is None:
        raise ValueError(
            "HWP 한 개를 먼저 추가하세요. 기존 준비 자료는 다시 만들지 않습니다."
        )
    review.hwp_audit(project.source)
    registered_security_module()  # read-only preflight BEFORE creating any output
    exporter = BaselineExporter(project.workspace)
    options = {}
    if progress is not None:
        options["progress"] = progress
    if cancelled is not None:
        options["cancelled"] = cancelled
    review.prepare(project.source, project.workspace, exporter=exporter, **options)
    if exporter.phase != 3:
        raise RuntimeError("Incomplete baseline preparation")
    return scan_folder(project.root)
