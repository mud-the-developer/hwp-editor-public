"""Application assets, not user documents. Source and PyInstaller use one layout."""

from pathlib import Path
import sys

APP_VERSION = "1.0.0"  # HWPX product version; exact qualification/publication separate.
APP_LICENSE = "AGPL-3.0-only"
SOURCE_URL = "https://github.com/mud-the-developer/hwp-editor-public"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if frozen():
        path = getattr(sys, "_MEIPASS", None)
        if not path:
            raise RuntimeError(
                "Packaged application resources are missing; do not use the working directory as a fallback."
            )
        root = Path(path).resolve(strict=True)
        if not root.is_dir():
            raise RuntimeError("Packaged resource root must be a directory")
        return root
    return Path(__file__).resolve().parents[1]


def legal_notice(root: Path) -> str:
    """Read public application terms, never a document/workspace or release URL."""
    return (
        f"HWP Editor {APP_VERSION}\n{APP_LICENSE}\n"
        "보증 없이 제공됩니다. 라이선스 원문은 아래에 있습니다.\n"
        f"프로젝트 저장소: {SOURCE_URL}\n"
        "저장소 링크만으로 수령자의 소스 접근이 보장되지는 않습니다.\n"
        f"예정된 동봉 앱 입력: HWPReview-source-{APP_VERSION}.zip\n"
        "앱 입력 ZIP은 제3자 전체 대응 소스를 포함한다는 보증이 아닙니다.\n"
        "배포자는 모든 수령자에게 정확한 대응 소스와 필요한 자료를 제공해야 합니다.\n"
        "제3자 원문 고지는 배포물의 licenses/에, 범위와 남은 의무는 "
        "앱 소스 ZIP의 docs/licensing.md에 있습니다.\n"
        "이 안내는 현재 후보의 소스 제공·Native 검증·게시 완료를 뜻하지 않습니다.\n\n"
        + (root / "LICENSE").read_text(encoding="utf-8")
    )
