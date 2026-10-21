
"""
architecture_diagram/analyzer.py

重用既有的 PythonAstAnalyzer 分析程式碼，
另外解析 import 關係，提供架構圖產生器使用。

目前範圍：
- 專案內 Python 模組的依賴關係
- import xxx
- from xxx import yyy
- from .xxx import yyy 等相對匯入

不修改既有的 core.models 與 requirements_spec。
"""

import ast
import os
from typing import List

from core.base import BaseAnalyzer
from core.models import AnalysisResult
from requirements_spec.analyzer import PythonAstAnalyzer


class ArchitectureAnalyzer(BaseAnalyzer):
    """分析 Python 專案的模組依賴關係。"""

    def __init__(self):
        self.base_analyzer = PythonAstAnalyzer()

    def analyze(self, file_paths: List[str]) -> AnalysisResult:
        # 重用現有分析器，不重寫原本的 AST 分析邏輯。
        result = self.base_analyzer.analyze(file_paths)

        # 依照絕對路徑找到分析結果對應的模組。
        modules_by_path = {
            os.path.normcase(os.path.abspath(module.file_path)): module
            for module in result.modules
        }

        for file_path in file_paths:
            normalized_path = os.path.normcase(
                os.path.abspath(file_path)
            )
            module_info = modules_by_path.get(normalized_path)

            # 原本分析器若因語法錯誤跳過檔案，就略過。
            if module_info is None:
                continue

            try:
                with open(
                    file_path, "r", encoding="utf-8"
                ) as source_file:
                    source = source_file.read()

                tree = ast.parse(source, filename=file_path)

            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                print(
                    f"[警告] 架構分析略過檔案：{file_path} "
                    f"（{exc}）"
                )
                continue

            imports = []

            # 與既有 PythonAstAnalyzer 一樣，
            # 第一版只分析模組頂層的 import。
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append({
                            "kind": "import",
                            "module": alias.name,
                            "names": [],
                            "level": 0,
                        })

                elif isinstance(node, ast.ImportFrom):
                    imports.append({
                        "kind": "from",
                        "module": node.module or "",
                        "names": [
                            alias.name
                            for alias in node.names
                            if alias.name != "*"
                        ],
                        "level": node.level,
                    })

            # 使用 extra 保存新功能的專屬資料，
            # 不必修改既有的 ModuleInfo 定義。
            module_info.extra["architecture_imports"] = imports

        return result
