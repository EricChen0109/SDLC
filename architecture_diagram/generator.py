
"""
architecture_diagram/generator.py

將分析結果輸出成 Markdown + Mermaid。
"""

import os
from pathlib import Path
from typing import Dict, List, Set, Tuple

from core.base import BaseGenerator
from core.models import AnalysisResult, ModuleInfo


class ArchitectureGenerator(BaseGenerator):
    """產生 Python 專案的模組依賴架構圖。"""

    def generate(
        self,
        analysis_result: AnalysisResult,
        output_path: str,
    ) -> str:
        modules = analysis_result.modules

        if not modules:
            raise ValueError("沒有可用的 Python 模組分析結果。")

        root_path = self._get_root_path(analysis_result)
        module_names = self._build_module_names(
            modules, root_path
        )

        # 使用模組名稱尋找對應的分析結果。
        name_to_module: Dict[str, ModuleInfo] = {}
        for module in modules:
            name = module_names[id(module)]
            # 避免重複模組名稱覆蓋而無聲失敗。
            if name in name_to_module:
                raise ValueError(
                    f"偵測到重複的模組名稱：{name}。"
                    "請確認來源目錄與套件結構。"
                )
            name_to_module[name] = module

        module_ids = {
            id(module): f"module_{index}"
            for index, module in enumerate(modules)
        }

        edges: Set[Tuple[str, str]] = set()
        unresolved: Set[str] = set()

        for source_module in modules:
            source_name = module_names[id(source_module)]
            imports = source_module.extra.get(
                "architecture_imports", []
            )

            for import_info in imports:
                targets = self._resolve_import_targets(
                    source_name,
                    import_info,
                    name_to_module,
                    is_package_init=self._is_package_init(
                        source_module.file_path
                    ),
                )

                if targets:
                    for target_name in targets:
                        target_module = name_to_module[target_name]

                        if target_module is source_module:
                            continue

                        edges.add((
                            module_ids[id(source_module)],
                            module_ids[id(target_module)],
                        ))
                else:
                    description = self._describe_import(
                        import_info
                    )
                    if description:
                        unresolved.add(
                            f"{source_module.file_path}: {description}"
                        )

        markdown = self._build_markdown(
            analysis_result=analysis_result,
            modules=modules,
            module_names=module_names,
            module_ids=module_ids,
            edges=sorted(edges),
            unresolved=sorted(unresolved),
        )

        # main.py 傳入的副檔名是 .docx；
        # 本功能實際輸出 Markdown 文件。
        output_file = str(
            Path(output_path).with_suffix(".md")
        )

        os.makedirs(
            os.path.dirname(os.path.abspath(output_file)),
            exist_ok=True,
        )

        with open(
            output_file, "w", encoding="utf-8"
        ) as file:
            file.write(markdown)

        return output_file

    @staticmethod
    def _get_root_path(
        analysis_result: AnalysisResult,
    ) -> str:
        source = os.path.abspath(
            analysis_result.source_path
        )

        if os.path.isfile(source):
            return os.path.dirname(source)

        return source

    @staticmethod
    def _is_package_init(file_path: str) -> bool:
        return Path(file_path).name == "__init__.py"

    @staticmethod
    def _build_module_names(
        modules: List[ModuleInfo],
        root_path: str,
    ) -> Dict[int, str]:
        """將檔案路徑轉為 Python 模組名稱。"""
        result: Dict[int, str] = {}

        for module in modules:
            file_path = os.path.abspath(module.file_path)

            try:
                relative_path = os.path.relpath(
                    file_path, root_path
                )
            except ValueError:
                relative_path = os.path.basename(file_path)

            path = Path(relative_path)
            parts = list(path.with_suffix("").parts)

            if parts and parts[-1] == "__init__":
                parts.pop()

            parts = [part for part in parts if part]
            module_name = ".".join(parts)

            if not module_name:
                module_name = Path(file_path).parent.name or "root"

            result[id(module)] = module_name

        return result

    @staticmethod
    def _resolve_import_targets(
        source_name: str,
        import_info: dict,
        name_to_module: Dict[str, ModuleInfo],
        is_package_init: bool = False,
    ) -> List[str]:
        """
        將 import 解析成專案內部模組。

        例如：
          from core.base import Feature
          from .utils import helper
          from ..models import Item
        """
        kind = import_info.get("kind")
        module = import_info.get("module", "")
        names = import_info.get("names", [])
        level = import_info.get("level", 0)

        if level:
            source_parts = source_name.split(".")

            # __init__.py 的模組名稱本身就是 package 名稱；
            # 其他檔案則要去掉最後一段才是 package。
            if is_package_init:
                package_parts = source_parts
            else:
                package_parts = source_parts[:-1]

            remove_count = level - 1
            if remove_count > len(package_parts):
                return []

            if remove_count:
                package_parts = package_parts[:-remove_count]

            base_parts = list(package_parts)

            if module:
                base_parts.extend(module.split("."))

            base_name = ".".join(
                part for part in base_parts if part
            )
        else:
            base_name = module

        candidates = []

        if kind == "import":
            if base_name:
                candidates.append(base_name)

        elif kind == "from":
            if base_name:
                # from package import Class 可能是類別，
                # 也可能是 package 下的子模組。
                for name in names:
                    candidates.append(
                        f"{base_name}.{name}"
                    )

                # 如果 base 本身就是專案模組，也要保留。
                candidates.append(base_name)
            else:
                # from . import sibling
                for name in names:
                    candidates.append(name)

        resolved = []
        for candidate in candidates:
            if candidate in name_to_module:
                if candidate not in resolved:
                    resolved.append(candidate)

        return resolved

    @staticmethod
    def _describe_import(import_info: dict) -> str:
        kind = import_info.get("kind")
        module = import_info.get("module", "")
        names = import_info.get("names", [])
        level = import_info.get("level", 0)

        prefix = "." * level + module

        if kind == "from":
            imported = ", ".join(names) if names else "*"
            return f"from {prefix} import {imported}"

        return f"import {prefix}"

    @staticmethod
    def _escape_mermaid_label(value: str) -> str:
        value = value.replace("&", "&amp;")
        value = value.replace('"', "&quot;")
        value = value.replace("<", "&lt;")
        value = value.replace(">", "&gt;")
        value = value.replace("[", "&#91;")
        value = value.replace("]", "&#93;")
        value = value.replace("\n", " ")
        return value

    def _build_markdown(
        self,
        analysis_result: AnalysisResult,
        modules: List[ModuleInfo],
        module_names: Dict[int, str],
        module_ids: Dict[int, str],
        edges: List[Tuple[str, str]],
        unresolved: List[str],
    ) -> str:
        lines = [
            "# Python 專案模組依賴架構圖",
            "",
            f"- 分析來源：`{analysis_result.source_path}`",
            f"- 模組數量：{len(modules)}",
            f"- 專案內依賴關係：{len(edges)}",
            "",
            "箭頭由使用其他模組的一方指向被依賴的模組。",
            "",
            "```mermaid",
            "flowchart TD",
        ]

        for module in modules:
            node_id = module_ids[id(module)]
            label = self._escape_mermaid_label(
                module_names[id(module)]
            )
            lines.append(f'    {node_id}["{label}"]')

        for source_id, target_id in edges:
            lines.append(f"    {source_id} --> {target_id}")

        lines.extend([
            "```",
            "",
            "## 模組清單",
            "",
        ])

        for module in modules:
            lines.append(
                f"- `{module_names[id(module)]}` — "
                f"`{module.file_path}`"
            )

        lines.extend([
            "",
            "## 未解析的匯入",
            "",
            "以下匯入沒有對應到目前分析結果中的專案模組。"
            "可能是第三方套件、標準函式庫、動態匯入，"
            "或尚未納入分析的專案模組。",
            "",
        ])

        if unresolved:
            for item in unresolved:
                lines.append(f"- `{item}`")
        else:
            lines.append("- 無")

        lines.extend([
            "",
            "## 分析限制",
            "",
            "- 目前只分析模組頂層的 import 敘述。",
            "- 不會解析函式內部的區域 import。",
            "- 不會推斷執行期動態載入的模組。",
            "- 圖表反映靜態依賴，不代表實際部署架構。",
            "",
        ])

        return "\n".join(lines)
