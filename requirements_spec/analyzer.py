"""
requirements_spec/analyzer.py

實作 BaseAnalyzer：用 Python 標準庫的 ast 模組，把 .py 檔案
解析成 core.models 定義好的結構化資料（ModuleInfo / ClassInfo / FunctionInfo...）。

只處理「模組層級」的東西（top-level 的 import / function / class），
class 裡面的 method 會另外處理、放進 ClassInfo.methods。
這樣做是為了對應真實的程式碼閱讀習慣 —— 看一個檔案時，
通常先看「這個檔案頂層長什麼樣子」，再往類別內部深入。
"""

import ast
from typing import List, Optional, Union

from core.base import BaseAnalyzer
from core.models import (
    AnalysisResult,
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    ModuleInfo,
    ParameterInfo,
)

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


class PythonAstAnalyzer(BaseAnalyzer):
    """針對 Python 原始碼的靜態分析器，核心是標準庫的 ast 模組"""

    def analyze(self, file_paths: List[str]) -> AnalysisResult:
        result = AnalysisResult(source_path="")  # source_path 由 main.py 在呼叫後補上
        for file_path in file_paths:
            module_info = self._analyze_single_file(file_path)
            if module_info is not None:
                result.modules.append(module_info)
        return result

    # ---------- 以下為內部實作，不對外暴露 ----------

    def _analyze_single_file(self, file_path: str) -> Optional[ModuleInfo]:
        """
        分析單一檔案。如果檔案有語法錯誤（例如是壞掉的程式碼、
        或不小心掃到非 Python 檔案），不讓整個分析任務中斷，
        而是記錄錯誤訊息、跳過這個檔案繼續處理下一個。
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"[警告] 無法解析檔案，已略過：{file_path}（原因：{e}）")
            return None

        module_info = ModuleInfo(
            file_path=file_path,
            module_docstring=ast.get_docstring(tree),
            line_count=len(source.splitlines()),
        )

        # 只走訪 tree.body（頂層節點），不用 ast.walk，
        # 這樣才不會把「寫在函式內部的 import」誤判成模組層級的東西
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_info.imports.append(
                        ImportInfo(module=alias.name, alias=alias.asname)
                    )
            elif isinstance(node, ast.ImportFrom):
                module_info.imports.append(
                    ImportInfo(
                        module=node.module or "",
                        names=[alias.name for alias in node.names],
                    )
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_info.functions.append(self._parse_function(node))
            elif isinstance(node, ast.ClassDef):
                module_info.classes.append(self._parse_class(node))

        return module_info

    def _parse_class(self, node: ast.ClassDef) -> ClassInfo:
        methods = [
            self._parse_function(item)
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return ClassInfo(
            name=node.name,
            bases=[ast.unparse(base) for base in node.bases],
            docstring=ast.get_docstring(node),
            methods=methods,
            decorators=[ast.unparse(d) for d in node.decorator_list],
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
        )

    def _parse_function(self, node: FunctionNode) -> FunctionInfo:
        return FunctionInfo(
            name=node.name,
            parameters=self._parse_parameters(node.args),
            return_type=ast.unparse(node.returns) if node.returns else None,
            docstring=ast.get_docstring(node),
            decorators=[ast.unparse(d) for d in node.decorator_list],
            is_async=isinstance(node, ast.AsyncFunctionDef),
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
        )

    def _parse_parameters(self, args: ast.arguments) -> List[ParameterInfo]:
        """
        把 ast.arguments 轉成 ParameterInfo 清單。
        比較麻煩的地方是「預設值」：ast 只會把有給預設值的參數的
        預設值存起來，而且是從清單尾端開始對齊（因為 Python
        規定沒有預設值的參數一定要放在有預設值的參數前面）。
        """
        parameters: List[ParameterInfo] = []

        positional = args.posonlyargs + args.args
        num_no_default = len(positional) - len(args.defaults)
        for i, arg in enumerate(positional):
            default_value = None
            if i >= num_no_default:
                default_node = args.defaults[i - num_no_default]
                default_value = ast.unparse(default_node)
            parameters.append(self._make_parameter(arg, default_value))

        if args.vararg:
            parameters.append(self._make_parameter(args.vararg, None, prefix="*"))

        for kwarg, default in zip(args.kwonlyargs, args.kw_defaults):
            default_value = ast.unparse(default) if default is not None else None
            parameters.append(self._make_parameter(kwarg, default_value))

        if args.kwarg:
            parameters.append(self._make_parameter(args.kwarg, None, prefix="**"))

        return parameters

    def _make_parameter(
        self, arg: ast.arg, default_value: Optional[str], prefix: str = ""
    ) -> ParameterInfo:
        return ParameterInfo(
            name=prefix + arg.arg,
            type_annotation=ast.unparse(arg.annotation) if arg.annotation else None,
            default_value=default_value,
        )
