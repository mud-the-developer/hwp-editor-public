"""Create a NEW, unsigned native HWP fixture. All content is fictional / TEST ONLY.
Never opens, copies, overwrites or derives assets from the real report.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

from hwp_security import register_security_module

MARKER = "TOY ONLY - SYNTHETIC FIXTURE - NOT FOR DELIVERY"
FIELDS = {
    "TITLE": ("TOY_TITLE_PLACEHOLDER________________", "가상 센서 검증 플랫폼"),
    "ORG": ("TOY_ORG_PLACEHOLDER____________", "토이연구팀(가상)"),
    "PERIOD": ("TOY_PERIOD_PLACEHOLDER___________", "가상 2026년 1월~12월"),
    "SUMMARY": ("TOY_SUMMARY_INPUT___________", "모사환경 가상센서 검증"),
    "G01": ("TOY_G01_INPUT", "가상 목표8건"),
    "R01": ("TOY_R01_INPUT", "가상 실적5건"),
    "E01": ("TOY_E01_INPUT", "가상 증빙01"),
    "G02": ("TOY_G02_INPUT", "가상 목표4건"),
    "R02": ("TOY_R02_INPUT", "가상 실적3건"),
    "E02": ("TOY_E02_INPUT", "가상 증빙02"),
    "S01": ("TOY_S01_PLACEHOLDER__________", "가상 1분기 설계"),
    "S02": ("TOY_S02_PLACEHOLDER__________", "가상 2분기 검증"),
    "B01": ("TOY_B01_PLACEHOLDER", "가상 100천원"),
    "B02": ("TOY_B02_PLACEHOLDER", "가상 200천원"),
    "I01": ("TOY_I01_PLACEHOLDER____________", "가상 시험기록01"),
    "I02": ("TOY_I02_PLACEHOLDER____________", "가상 결과자료02"),
    "CONCLUSION": (
        "TOY_CONCLUSION_PLACEHOLDER____________________________________",
        "가상 시험을 완료했으며 추가 재현성 확인을 계획한다.",
    ),
}


def make_chart(path: Path) -> None:
    import fitz

    with fitz.open() as doc:
        page = doc.new_page(width=300, height=90)
        page.insert_text(
            (12, 18), "TOY ONLY - fictitious illustration, not evidence", fontsize=10
        )
        for i, (width, color) in enumerate(
            [(210, (0.2, 0.45, 0.8)), (140, (0.2, 0.65, 0.5)), (80, (0.85, 0.6, 0.2))]
        ):
            page.draw_rect(
                fitz.Rect(12, 28 + i * 17, 12 + width, 39 + i * 17),
                color=color,
                fill=color,
            )
        page.get_pixmap(dpi=144, alpha=False).save(path)


def build(output: Path) -> dict:
    import pythoncom
    import win32com.client
    from win32com.client import gencache

    output = output.resolve()
    if output.exists():
        raise FileExistsError(
            "Use a NEW toy source directory; never overwrite an existing document"
        )
    output.mkdir(parents=True)
    chart = output / "TOY-ONLY-illustration.png"
    make_chart(chart)
    pythoncom.CoInitialize()
    app = None
    try:
        # Bind the installed typelib for methods returning HwpCtrl dispatch objects.
        # DispatchEx still creates an owned instance; never attach to the user's HWP.
        app = gencache.EnsureDispatch(win32com.client.DispatchEx("HWPFrame.HwpObject"))
        app.XHwpWindows.Item(0).Visible = False
        security = register_security_module(app)

        def action(name, parameter):
            if not app.HAction.Execute(name, parameter.HSet):
                raise RuntimeError("Native action failed: " + name)

        def font(size=10, bold=False):
            p = app.HParameterSet.HCharShape
            app.HAction.GetDefault("CharShape", p.HSet)
            for language in [
                "Hangul",
                "Latin",
                "Hanja",
                "Japanese",
                "Other",
                "Symbol",
                "User",
            ]:
                setattr(p, "FaceName" + language, "함초롬바탕")
            p.Height, p.Bold, p.Italic, p.UnderlineType = size * 100, int(bold), 0, 0
            action("CharShape", p)
            p = app.HParameterSet.HParaShape
            app.HAction.GetDefault("ParagraphShape", p.HSet)
            p.AlignType = 0
            action("ParagraphShape", p)

        def text(value):
            p = app.HParameterSet.HInsertText
            app.HAction.GetDefault("InsertText", p.HSet)
            p.Text = value
            action("InsertText", p)

        def line(value="", size=10, bold=False):
            font(size, bold)
            if value:
                text(value)
            app.Run("BreakPara")

        def heading(number, title):
            print(
                f"Creating TOY section {number}: {title}", file=sys.stderr, flush=True
            )
            if number > 1:
                app.Run("MoveDocEnd")
                app.Run("BreakPage")
            line(MARKER, 8)
            line("토이 연구개발 연차보고서 / 가상 자료 / 무날인", 10)
            line(f"{number}. {title}", 17, True)
            line("모든 기관명·기간·수치·실적은 테스트용 가상 값입니다.", 9)
            line()

        def table(rows, widths):
            font(9)
            p = app.HParameterSet.HTableCreation
            app.HAction.GetDefault("TableCreate", p.HSet)
            p.Rows, p.Cols = len(rows), len(widths)
            p.WidthType, p.HeightType = 1, 1
            p.WidthValue = app.MiliToHwpUnit(sum(widths))
            p.HeightValue = app.MiliToHwpUnit(12 * len(rows))
            p.TableProperties.TreatAsChar = 1
            p.CreateItemArray("ColWidth", len(widths))
            for i, width in enumerate(widths):
                p.ColWidth.SetItem(i, app.MiliToHwpUnit(width))
            action("TableCreate", p)
            for row_index, row in enumerate(rows):
                if len(row) != len(widths):
                    raise ValueError("Ragged toy table")
                for col_index, value in enumerate(row):
                    font(9, row_index == 0)
                    text(value)
                    if col_index + 1 < len(row):
                        app.Run("TableRightCell")
                if row_index + 1 < len(rows):
                    for _ in range(len(widths) - 1):
                        app.Run("TableLeftCell")
                    app.Run("TableLowerCell")
            app.Run("MoveDocEnd")
            app.Run("BreakPara")

        def slot(key):
            return FIELDS[key][0]

        heading(1, "표지")
        line("작성·위치 메모·전후 비교를 위한 작은 시험 양식", 12, True)
        line(slot("TITLE"), 10)
        line("실제 보고서나 직인·서명·기관 이미지는 포함하지 않았습니다.")
        line(
            "영문 INPUT / PLACEHOLDER는 수정 주소와 길이 예산을 갖는 입력 슬롯입니다.",
            9,
        )
        line("가상 수행 흐름: 설계 → 시험 → 결과 정리", 11, True)
        line("별도 TOY 그림 파일은 참고용이며 이 HWP에는 삽입하지 않았습니다.", 9)
        line("TEST ONLY: 파일명과 모든 페이지에 시험 문서임을 표시합니다.", 9)
        heading(2, "과제 개요")
        table(
            [
                ["항목", "작성 슬롯"],
                ["수행 기관", slot("ORG")],
                ["연구 기간", slot("PERIOD")],
                ["과제 요약", slot("SUMMARY")],
            ],
            [30, 110],
        )
        line(
            "작성 안내: 가상 자료만 입력하며 자료가 없는 실제 보고서 항목은 추측하지 않습니다.",
            9,
        )
        heading(3, "목표 및 연차 실적")
        table(
            [
                ["시험 지표", "목표", "실적", "근거"],
                ["처리 시험(가상)", slot("G01"), slot("R01"), slot("E01")],
                ["재현 시험(가상)", slot("G02"), slot("R02"), slot("E02")],
            ],
            [35] * 4,
        )
        line("표 셀별 독립 메모와 여러 슬롯의 정확한 치환을 시험합니다.", 9)
        heading(4, "일정 및 예산")
        table(
            [
                ["단계", "일정 / 활동"],
                ["설계 단계(가상)", slot("S01")],
                ["검증 단계(가상)", slot("S02")],
            ],
            [45, 95],
        )
        table(
            [
                ["항목(모두 가상)", "계획액", "집행액"],
                ["가상 시험비", slot("B01"), slot("B02")],
            ],
            [46, 47, 47],
        )
        heading(5, "증빙 목록 및 종합 의견")
        table(
            [
                ["증빙 구분", "가상 자료명"],
                ["시험 기록", slot("I01")],
                ["결과 자료", slot("I02")],
            ],
            [45, 95],
        )
        line("종합 의견 (가상 작성 슬롯)", 11, True)
        line(slot("CONCLUSION"), 9)
        line("서명 / 직인 없음. 이 문서와 모든 작성 결과는 제출용이 아닙니다.", 9)
        source = output / "TOY-ONLY-template.hwp"
        for target, fmt in [
            (source, "HWP"),
            (output / "TOY-ONLY-template.hwpx", "HWPX"),
            (output / "TOY-ONLY-template.pdf", "PDF"),
        ]:
            if (
                target.exists()
                or not app.SaveAs(str(target), fmt, "")
                or not target.is_file()
            ):
                raise RuntimeError("Native toy export failed: " + str(target))
        result = {
            "schema": 1,
            "purpose": "TOY_ONLY_NOT_DELIVERABLE",
            "marker": MARKER,
            "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "native_pages": int(app.PageCount),
            "security_module": security,
            "embedded_illustration": False,
            "slots": FIELDS,
        }
        (output / "toy-fixture.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    finally:
        if app is not None:
            try:
                app.Clear(1)
                app.Quit()
            finally:
                del app
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    configure_stdout = getattr(sys.stdout, "reconfigure", None)
    if callable(configure_stdout):
        configure_stdout(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--create-toy-only", action="store_true", required=True)
    args = parser.parse_args()
    result = build(args.output)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "slots"},
            ensure_ascii=False,
            indent=2,
        )
    )
