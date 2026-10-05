"""
requirements_spec/generator.py

實作 BaseGenerator：把 AnalysisResult（結構化的分析結果）
轉成一份 .docx 技術規格文件。

用 python-docx 這個套件（純 Python，之後打包成獨立程式也不需要額外裝 Node.js）。

設計備註（為未來接 LLM預留）：
目前每個函式/類別的「說明」欄位，直接讀 docstring（沒有就顯示「無說明」）。
之後如果 LLM 幫忙補了說明，會放在 FunctionInfo.extra["llm_explanation"]
（或 ClassInfo.extra[...]）裡，到時候只要在 `_get_description()`
這個小函式裡，改成「優先讀 llm_explanation、沒有才 fallback 讀 docstring」，
其他排版邏輯完全不用動。
"""

import os
from typing import List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from core.base import BaseGenerator
from core.models import AnalysisResult, ClassInfo, CodeBlockInfo,FunctionInfo, ModuleInfo


class RequirementsSpecGenerator(BaseGenerator):
    """把分析結果排版成 docx 技術規格文件"""

    def generate(self, analysis_result: AnalysisResult, output_path: str) -> str:
        document = Document()

        self._add_title_page(document, analysis_result)

        for module in analysis_result.modules:
            self._add_module_section(document, module)

        document.save(output_path)
        return output_path

    # ---------- 以下為內部排版邏輯 ----------

    def _add_title_page(self, document: Document, analysis_result: AnalysisResult) -> None:
        title = document.add_heading("技術規格文件", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        subtitle = document.add_paragraph()
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = subtitle.add_run(f"分析來源：{analysis_result.source_path}")
        run.font.size = Pt(11)
        run.italic = True

        document.add_paragraph(f"共分析 {len(analysis_result.modules)} 個檔案")
        document.add_page_break()

    def _add_module_section(self, document: Document, module: ModuleInfo) -> None:
        document.add_heading(module.file_path, level=1)
        document.add_paragraph(f"行數：{module.line_count}")

        if module.module_docstring:
            document.add_heading("模組說明", level=2)
            document.add_paragraph(module.module_docstring)

        if module.imports:
            document.add_heading("Imports", level=2)
            for imp in module.imports:
                if imp.names:
                    text = f"from {imp.module} import {', '.join(imp.names)}"
                else:
                    text = f"import {imp.module}"
                    if imp.alias:
                        text += f" as {imp.alias}"
                document.add_paragraph(text, style="List Bullet")

        if module.functions:
            document.add_heading("函式", level=2)
            for func in module.functions:
                self._add_function_block(document, func, heading_level=3)

        if module.classes:
            document.add_heading("類別", level=2)
            for cls in module.classes:
                self._add_class_block(document, cls)
        if module.code_blocks:
                    document.add_heading("其他程式邏輯", level=2)
                    document.add_paragraph(
                        "以下為模組中沒有封裝成函式/類別的程式碼（例如執行入口、模組層級設定）。"
                    )
                    for i, block in enumerate(module.code_blocks, 1):
                        self._add_code_block(document, block, index=i)
        
        
        document.add_page_break()

    def _add_code_block(self, document: Document, block: CodeBlockInfo, index: int) -> None:
            document.add_heading(
                f"區塊 {index}（第 {block.line_start}-{block.line_end} 行）", level=3
            )
            description = self._get_description(None, block.extra)
            document.add_paragraph(description)
            #self._add_code_paragraph(document, block.source_code)
    
    def _add_code_paragraph(self, document: Document, code: str) -> None:
        """用等寬字型呈現一段原始碼，跟一般說明文字做出區隔"""
        paragraph = document.add_paragraph()
        run = paragraph.add_run(code)
        run.font.name = "Courier New"
        run.font.size = Pt(9)

    def _add_class_block(self, document: Document, cls: ClassInfo) -> None:
        heading_text = cls.name
        if cls.bases:
            heading_text += f"（繼承自 {', '.join(cls.bases)}）"
        document.add_heading(heading_text, level=3)

        description = self._get_description(cls.docstring, cls.extra)
        document.add_paragraph(description)

        if cls.methods:
            for method in cls.methods:
                self._add_function_block(document, method, heading_level=4)
        else:
            document.add_paragraph("（此類別沒有方法）")

    def _add_function_block(
        self, document: Document, func: FunctionInfo, heading_level: int
    ) -> None:
        prefix = "async " if func.is_async else ""
        signature = f"{prefix}{func.name}({self._format_parameters(func)})"
        if func.return_type:
            signature += f" -> {func.return_type}"
        document.add_heading(signature, level=heading_level)

        if func.decorators:
            document.add_paragraph(
                f"裝饰器：{', '.join('@' + d for d in func.decorators)}"
            )

        description = self._get_description(func.docstring, func.extra)
        document.add_paragraph(description)

        if func.parameters:
            self._add_parameters_table(document, func)

    def _add_parameters_table(self, document: Document, func: FunctionInfo) -> None:
        table = document.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        header_cells = table.rows[0].cells
        header_cells[0].text = "參數名稱"
        header_cells[1].text = "型別"
        header_cells[2].text = "預設值"

        for param in func.parameters:
            row_cells = table.add_row().cells
            row_cells[0].text = param.name
            row_cells[1].text = param.type_annotation or "（未標註）"
            row_cells[2].text = param.default_value if param.default_value is not None else "（必填）"

    def _format_parameters(self, func: FunctionInfo) -> str:
        return ", ".join(p.name for p in func.parameters)

    def _get_description(self, docstring: Optional[str], extra: dict) -> str:
        """
        取得要顯示的說明文字。
        優先權：LLM 生成的說明 > 原始 docstring > 「無說明」。
        LLM 的說明放在 extra["llm_explanation"]（由 core/llm_enhancer.py 寫入）。
        """
        if extra and "llm_explanation" in extra:
            return extra["llm_explanation"]
        if docstring:
            return docstring
        return "（無說明）"
    