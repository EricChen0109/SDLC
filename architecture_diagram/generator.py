"""
architecture_diagram/generator.py

實作 BaseGenerator：把 ArchitectureGraph 排版成 docx。
畫圖本身由 diagram_renderer.py 負責，這裡只管「圖放哪裡、文字怎麼寫、表格怎麼排」。

每張圖後面都附一份文字明細（表格/清單），因為專案很大時圖會被縮得很小、很難讀，
文字版永遠是準確、可以搜尋的備援。
"""

import io
from collections import defaultdict
from typing import Dict, List

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from architecture_diagram.diagram_renderer import (
    RenderedImage,
    render_dependency_diagram,
    render_inheritance_diagram,
)
from architecture_diagram.models import ARCHITECTURE_EXTRA_KEY, ArchitectureGraph
from core.base import BaseGenerator
from core.models import AnalysisResult

# 放進 docx 的圖片，高度最多佔幾英吋（再高會超出一頁，Word 會把圖片裁掉）
MAX_IMAGE_HEIGHT_INCHES = 8.0


class ArchitectureDiagramGenerator(BaseGenerator):
    def generate(self, analysis_result: AnalysisResult, output_path: str) -> str:
        graph = analysis_result.extra.get(ARCHITECTURE_EXTRA_KEY)
        if graph is None:
            raise ValueError(
                "分析結果裡找不到架構圖資料，請確認這個 Generator 搭配的是 ArchitectureAnalyzer"
            )

        document = Document()
        self._add_title_page(document, analysis_result)
        self._add_dependency_section(document, graph)
        self._add_inheritance_section(document, graph)
        document.save(output_path)
        return output_path

    # ---------- 標題頁 ----------

    def _add_title_page(self, document: Document, analysis_result: AnalysisResult) -> None:
        title = document.add_heading("系統架構圖", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        subtitle = document.add_paragraph()
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = subtitle.add_run(f"分析來源：{analysis_result.source_path}")
        run.font.size = Pt(11)
        run.italic = True

        document.add_paragraph(f"共分析 {len(analysis_result.modules)} 個檔案")
        document.add_page_break()

    # ---------- 模組依賴圖 ----------

    def _add_dependency_section(self, document: Document, graph: ArchitectureGraph) -> None:
        document.add_heading("模組依賴圖", level=1)
        document.add_paragraph(
            f"共 {len(graph.modules)} 個檔案，偵測到 {len(graph.dependencies)} 條專案內部的依賴關係。"
            "箭頭由 A 指向 B，代表「A 匯入（依賴）B」；方塊顏色代表所屬的資料夾；"
            "紅色箭頭代表循環依賴。圖中只列出有依賴關係的檔案，完整清單見下方表格。"
        )

        image = render_dependency_diagram(graph.modules, graph.dependencies)
        if image is None:
            document.add_paragraph("（專案內部沒有偵測到檔案之間的依賴關係，因此沒有產生圖片。）")
        else:
            self._add_picture(document, image)

        document.add_heading("循環依賴", level=2)
        if graph.cycles:
            document.add_paragraph(
                "以下檔案彼此直接或間接地互相依賴，修改其中一個容易牽動其他檔案，"
                "通常是架構上需要留意的地方："
            )
            for cycle in graph.cycles:
                document.add_paragraph(" ⇄ ".join(cycle), style="List Bullet")
        else:
            document.add_paragraph("未偵測到循環依賴。")

        document.add_heading("依賴明細", level=2)
        self._add_dependency_table(document, graph)
        document.add_page_break()

    def _add_dependency_table(self, document: Document, graph: ArchitectureGraph) -> None:
        depends_on: Dict[str, List[str]] = defaultdict(list)
        depended_by_count: Dict[str, int] = defaultdict(int)
        for source, target in graph.dependencies:
            depends_on[source].append(target)
            depended_by_count[target] += 1

        table = document.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        header = table.rows[0].cells
        header[0].text = "檔案"
        header[1].text = "依賴的檔案"
        header[2].text = "被幾個檔案依賴"

        for module in graph.modules:
            row = table.add_row().cells
            row[0].text = module
            row[1].text = "\n".join(depends_on.get(module, [])) or "（無）"
            row[2].text = str(depended_by_count.get(module, 0))

    # ---------- 類別繼承圖 ----------

    def _add_inheritance_section(self, document: Document, graph: ArchitectureGraph) -> None:
        document.add_heading("類別繼承圖", level=1)

        if not graph.inheritances:
            document.add_paragraph(
                f"專案共有 {graph.total_classes} 個類別，其中沒有偵測到繼承專案內其他類別的關係，"
                "因此沒有產生圖片。（繼承自 ABC、Exception 這類專案以外的類別不算在內。）"
            )
            return

        document.add_paragraph(
            f"專案共有 {graph.total_classes} 個類別，其中 {len(graph.class_labels)} 個類別參與了"
            f"專案內部的繼承關係（共 {len(graph.inheritances)} 條）。"
            "箭頭由子類別指向父類別，代表「子類別繼承自父類別」；方塊顏色代表類別所在的資料夾。"
        )

        image = render_inheritance_diagram(
            graph.class_labels, graph.class_files, graph.inheritances
        )
        if image is not None:
            self._add_picture(document, image)

        document.add_heading("繼承明細", level=2)
        table = document.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        header = table.rows[0].cells
        header[0].text = "子類別"
        header[1].text = "繼承自"
        header[2].text = "子類別所在檔案"
        for child, parent in graph.inheritances:
            row = table.add_row().cells
            row[0].text = graph.class_labels[child].replace("\n", " ")
            row[1].text = graph.class_labels[parent].replace("\n", " ")
            row[2].text = graph.class_files[child]

    # ---------- 共用 ----------

    def _add_picture(self, document: Document, image: RenderedImage) -> None:
        png_bytes, aspect_ratio = image
        section = document.sections[-1]
        max_width = section.page_width - section.left_margin - section.right_margin
        max_width_by_height = int(Inches(MAX_IMAGE_HEIGHT_INCHES) * aspect_ratio)
        document.add_picture(io.BytesIO(png_bytes), width=min(max_width, max_width_by_height))
        document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
