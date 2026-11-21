"""
test_doc/generator.py

實作 BaseGenerator：把測試對象與（可能有的）LLM 測試案例排版成 docx 測試文件。

文件結構：
  1. 測試範圍概述   —— 數字總覽、風險怎麼算、這份文件的限制
  2. 風險排序       —— 建議優先測試哪些，以及為什麼
  3. 重點測試對象   —— 前 N 名的詳細說明：邊界條件、會拋的例外、（有 LLM 時）測試案例表格
  4. 測試對象清單   —— 全部對象的完整清單
  5. 既有測試檔

沒啟用 LLM 時，第 3 節仍然有「邊界條件」與「例外」可看，只是沒有具體的測試案例表格。
"""

from typing import List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from docx.document import Document as DocumentType
from core.base import BaseGenerator
from core.models import AnalysisResult
from test_doc.models import (
    FOCUS_TARGET_COUNT,
    TEST_DOC_EXTRA_KEY,
    CaseSpec,
    TargetCollection,
    TargetInfo,
)

RANKING_TABLE_ROWS = 20  # 風險排序表最多列出幾名

VISIBILITY_LABELS = {
    "public": "公開",
    "private": "私有",
    "constructor": "建構子",
    "dunder": "特殊方法",
}
CATEGORY_LABELS = {"normal": "正常", "boundary": "邊界", "error": "錯誤處理"}
TABLE_STYLE = "Light Grid Accent 1"


class PlanDocxGenerator(BaseGenerator):
    def generate(self, analysis_result: AnalysisResult, output_path: str) -> str:
        data = analysis_result.extra.get(TEST_DOC_EXTRA_KEY)
        if data is None:
            raise ValueError(
                "分析結果裡找不到測試文件資料，請確認這個 Generator 搭配的是 TargetAnalyzer"
            )

        document = Document()
        self._add_title_page(document, analysis_result, data)
        self._add_overview(document, data)
        self._add_risk_ranking(document, data)
        self._add_focus_targets(document, data)
        self._add_inventory(document, data)
        self._add_existing_tests(document, data)
        document.save(output_path)
        return output_path

    # ---------- 標題頁 ----------

    def _add_title_page(
        self, document: DocumentType, analysis_result: AnalysisResult, data: TargetCollection
    ) -> None:
        title = document.add_heading("測試文件", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        subtitle = document.add_paragraph()
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = subtitle.add_run(f"分析來源：{analysis_result.source_path}")
        run.font.size = Pt(11)
        run.italic = True

        document.add_paragraph(f"共找出 {len(data.targets)} 個測試對象（函式與方法）")
        document.add_page_break()

    # ---------- 1. 概述 ----------

    def _add_overview(self, document: DocumentType, data: TargetCollection) -> None:
        document.add_heading("1. 測試範圍概述", level=1)

        total = len(data.targets)
        covered = sum(1 for t in data.targets if t.covered_by)
        counts = {v: sum(1 for t in data.targets if t.visibility == v) for v in VISIBILITY_LABELS}
        breakdown = "、".join(f"{VISIBILITY_LABELS[v]} {n} 個" for v, n in counts.items() if n)

        document.add_paragraph(f"測試對象共 {total} 個（{breakdown or '無'}）。", style="List Bullet")
        document.add_paragraph(
            f"專案內偵測到 {len(data.test_files)} 個既有測試檔；"
            f"其中 {covered} 個對象有被測試檔引用，{total - covered} 個沒有。",
            style="List Bullet",
        )
        generated = sum(1 for t in data.targets if t.test_cases)
        if data.extra.get("llm_applied"):
            document.add_paragraph(
                f"已使用 LLM 替風險最高的前 {FOCUS_TARGET_COUNT} 個對象設計測試案例，"
                f"實際產生案例的有 {generated} 個。",
                style="List Bullet",
            )
        else:
            document.add_paragraph(
                "本次沒有啟用 LLM，因此沒有具體的測試案例；"
                "第 3 節只列出依型別與程式碼推得的邊界條件與例外，供設計案例時參考。",
                style="List Bullet",
            )

        document.add_heading("風險分數怎麼算", level=2)
        document.add_paragraph(
            "風險分數用來決定「先測哪些」，由下列因素累加而成：分支越多（圈複雜度）、"
            "會拋出的例外越多、參數越多、函式越長、沒有 docstring、沒有既有測試引用，分數越高。"
            "私有函式與特殊方法通常透過公開介面間接被測到，分數會打五折。"
            "每個對象被排在前面的具體原因，都列在第 2、3 節。"
        )

        document.add_heading("這份文件的限制", level=2)
        document.add_paragraph(
            "測試案例是從程式碼「現在的行為」反推出來的，反映的是程式目前做什麼，"
            "不保證是它應該做什麼。如果程式本身有錯，反推出的案例可能把錯誤當成正確行為，"
            "請對照需求規格確認。預期結果標示「需人工確認」的，代表無法單靠閱讀程式碼確定。",
            style="List Bullet",
        )
        document.add_paragraph(
            "「有沒有既有測試」是比對名稱的粗略判斷，不是真的執行測試量出來的覆蓋率；"
            "名稱很常見的函式（例如 run、get）可能被誤判成「有測試」。",
            style="List Bullet",
        )
        document.add_page_break()

    # ---------- 2. 風險排序 ----------

    def _add_risk_ranking(self, document: DocumentType, data: TargetCollection) -> None:
        document.add_heading("2. 風險排序", level=1)
        if not data.targets:
            document.add_paragraph("沒有找到可測試的對象。")
            document.add_page_break()
            return

        shown = data.targets[:RANKING_TABLE_ROWS]
        document.add_paragraph(
            f"依風險分數由高到低排列（共 {len(data.targets)} 個，此處列出前 {len(shown)} 個），"
            "建議照這個順序安排測試。"
        )
        rows = [
            [
                str(rank),
                t.qualified_name,
                str(t.risk_score),
                "\n".join(t.risk_reasons) or "（無特別風險）",
                "有" if t.covered_by else "無",
            ]
            for rank, t in enumerate(shown, 1)
        ]
        self._add_table(
            document, ["排名", "對象", "分數", "風險原因", "既有測試"], rows,
            widths=[0.45, 1.4, 0.5, 2.9, 0.75],
        )
        document.add_page_break()

    # ---------- 3. 重點測試對象 ----------

    def _add_focus_targets(self, document:DocumentType, data: TargetCollection) -> None:
        document.add_heading("3. 重點測試對象", level=1)
        focus = data.focus_targets()
        if not focus:
            document.add_paragraph("沒有找到可測試的對象。")
            document.add_page_break()
            return

        document.add_paragraph(f"以下是風險最高的前 {len(focus)} 個對象的詳細說明。")
        for rank, target in enumerate(focus, 1):
            self._add_focus_target(document, rank, target, bool(data.extra.get("llm_applied")))
        document.add_page_break()

    def _add_focus_target(
        self, document: DocumentType, rank: int, target: TargetInfo, llm_applied: bool
    ) -> None:
        document.add_heading(f"3.{rank} {target.qualified_name}", level=2)
        document.add_paragraph(
            f"位置：{target.file_path}　｜　可見性：{VISIBILITY_LABELS[target.visibility]}"
            f"　｜　風險分數：{target.risk_score}　｜　圈複雜度：{target.complexity}"
        )
        signature = document.add_paragraph()
        run = signature.add_run(target.signature)
        run.font.name = "Courier New"
        run.font.size = Pt(10)

        if target.docstring:
            document.add_paragraph(f"說明：{target.docstring}")

        self._add_bullets(document, "風險原因", target.risk_reasons)
        self._add_bullets(document, "會拋出的例外", target.raises)
        self._add_bullets(document, "建議測試的邊界條件", target.boundary_hints)

        if target.test_cases:
            document.add_paragraph().add_run("測試案例").bold = True
            self._add_cases_table(document, target.test_cases)
        elif llm_applied:
            document.add_paragraph("（LLM 沒有產生可用的測試案例：呼叫失敗或回應無法解析。）")

    def _add_bullets(self, document: DocumentType, heading: str, items: List[str]) -> None:
        if not items:
            return
        document.add_paragraph().add_run(heading).bold = True
        for item in items:
            document.add_paragraph(item, style="List Bullet")

    def _add_cases_table(self, document: DocumentType, cases: List[CaseSpec]) -> None:
        rows = []
        for number, case in enumerate(cases, 1):
            case_text = case.title + (f"\n{case.description}" if case.description else "")
            rows.append(
                [
                    str(number),
                    CATEGORY_LABELS.get(case.category, case.category),
                    case_text,
                    _format_inputs(case),
                    _format_expected(case),
                ]
            )
        self._add_table(
            document, ["#", "類型", "案例", "輸入", "預期結果"], rows,
            widths=[0.3, 0.75, 1.9, 1.65, 1.4],
        )

    # ---------- 4. 完整清單 ----------

    def _add_inventory(self, document: DocumentType, data: TargetCollection) -> None:
        document.add_heading("4. 測試對象清單", level=1)
        if not data.targets:
            document.add_paragraph("沒有找到可測試的對象。")
            document.add_page_break()
            return

        document.add_paragraph("所有被納入的函式與方法，依檔案排列。")
        ordered = sorted(data.targets, key=lambda t: (t.file_path, t.qualified_name))
        rows = [
            [
                t.file_path,
                t.qualified_name,
                VISIBILITY_LABELS[t.visibility],
                str(t.complexity),
                str(t.risk_score),
                "有" if t.covered_by else "無",
            ]
            for t in ordered
        ]
        self._add_table(
            document, ["檔案", "對象", "可見性", "複雜度", "風險", "既有測試"], rows,
            widths=[1.5, 1.9, 0.7, 0.6, 0.6, 0.7],
        )
        document.add_page_break()

    # ---------- 5. 既有測試檔 ----------

    def _add_existing_tests(self, document: DocumentType, data: TargetCollection) -> None:
        document.add_heading("5. 既有測試檔", level=1)
        if not data.test_files:
            document.add_paragraph(
                "專案內沒有偵測到測試檔（檔名為 test_*.py、*_test.py，或放在 tests/ 資料夾底下）。"
            )
            return
        for test_file in data.test_files:
            document.add_paragraph(test_file, style="List Bullet")

    # ---------- 共用 ----------

    def _add_table(
        self,
        document: DocumentType,
        headers: List[str],
        rows: List[List[str]],
        widths: List[float],
        font_size: int = 9,
    ) -> None:
        """widths 是每一欄的寬度（英吋）。不指定的話每欄等寬，窄欄位會浪費空間、長文字欄位被擠成一條縫"""
        table = document.add_table(rows=1, cols=len(headers))
        table.style = TABLE_STYLE
        table.autofit = False  # 關掉自動調整，Word 才會照我們設定的寬度排
        for cell, header in zip(table.rows[0].cells, headers):
            _fill_cell(cell, header, font_size)
        for row in rows:
            for cell, text in zip(table.add_row().cells, row):
                _fill_cell(cell, text, font_size)
        # 寬度要設兩個地方：Word 看「儲存格」的寬度，LibreOffice 等其他軟體看「欄」的寬度，
        # 只設其中一個的話，換一個軟體開就會跑版
        for column, width in zip(table.columns, widths):
            column.width = Inches(width)
        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = Inches(width)


def _fill_cell(cell, text: str, font_size: int) -> None:
    cell.text = ""
    run = cell.paragraphs[0].add_run(text)
    run.font.size = Pt(font_size)


def _format_inputs(case: CaseSpec) -> str:
    lines = []
    if case.setup:
        lines.append(f"建立物件：{case.setup}")
    lines.extend(f"{name} = {value}" for name, value in case.inputs.items())
    return "\n".join(lines) or "（無輸入）"


def _format_expected(case: CaseSpec) -> str:
    if case.expected_exception:
        return f"拋出 {case.expected_exception}"
    if case.expected is None:
        return "（需人工確認）"
    return case.expected
