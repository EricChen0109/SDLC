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
    CodeBlockInfo,
    FunctionInfo,
    ImportInfo,
    ModuleInfo,
    ParameterInfo,
)

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _is_docstring_node(node: ast.stmt) -> bool:
    """判斷這個節點是不是「模組/函式/類別最開頭的 docstring 字串敘述」"""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
    
    
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

        # 用來暫存「連續的、非結構化」的頂層節點，
        # 遇到 import/function/class 或掃完整個檔案時，就把暫存的這一串打包成一個 CodeBlockInfo
        pending_nodes: List[ast.stmt] = []
        
        def flush_pending_block() -> None:
            if not pending_nodes:
                return
            block = self._make_code_block(pending_nodes, source)
            module_info.code_blocks.append(block)
            pending_nodes.clear()
            
        # 只走訪 tree.body（頂層節點），不用 ast.walk，
        # 這樣才不會把「寫在函式內部的 import」誤判成模組層級的東西。
        # 模組最開頭的 docstring（如果有）在 ast 裡也會是 tree.body[0] 的
        # ast.Expr(ast.Constant(str))，這裡要特別跳過，不然會被誤判成程式碼區塊。
        for index, node in enumerate(tree.body):
            if index == 0 and module_info.module_docstring and _is_docstring_node(node):
                continue
            if isinstance(node, ast.Import):
                flush_pending_block()
                for alias in node.names:
                    module_info.imports.append(
                        ImportInfo(module=alias.name, alias=alias.asname)
                    )
            elif isinstance(node, ast.ImportFrom):
                flush_pending_block()
                module_info.imports.append(
                    ImportInfo(
                        module=node.module or "",
                        names=[alias.name for alias in node.names],
                    )
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                flush_pending_block()
                module_info.functions.append(self._parse_function(node, source))
            elif isinstance(node, ast.ClassDef):
                flush_pending_block()
                module_info.classes.append(self._parse_class(node, source))
            else:
                # 其他所有型態的頂層敘述（賦值、if、for、直接呼叫函式...）
                # 先收集起來，等遇到下一個結構化節點，或掃完整個檔案時再打包
                pending_nodes.append(node)
        
        flush_pending_block()
        
        return module_info

    def _make_code_block(self, nodes: List[ast.stmt], source: str) -> CodeBlockInfo:
            """把一串連續的 ast 節點，打包成一個 CodeBlockInfo（含原始碼片段）"""
            line_start = nodes[0].lineno
            line_end = getattr(nodes[-1], "end_lineno", nodes[-1].lineno)
            source_lines = source.splitlines()
            code_text = "\n".join(source_lines[line_start - 1 : line_end])
            return CodeBlockInfo(source_code=code_text, line_start=line_start, line_end=line_end)
    
    def _parse_class(self, node: ast.ClassDef,source:str) -> ClassInfo:
        methods = [
            self._parse_function(item,source)
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        class_info = ClassInfo(
            name=node.name,
            bases=[ast.unparse(base) for base in node.bases],
            docstring=ast.get_docstring(node),
            methods=methods,
            decorators=[ast.unparse(d) for d in node.decorator_list],
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
        )
        # 存下原始碼片段，讓之後的 LLM enhancer 能在沒有 docstring 時，
        # 讀這段程式碼內容去生成說明。放在 extra 裡，不影響既有欄位。
        source_segment = ast.get_source_segment(source, node)
        if source_segment:
            class_info.extra["source_code"] = source_segment
        return class_info

    def _parse_function(self, node: FunctionNode,source:str) -> FunctionInfo:
        func_info = FunctionInfo(
            name=node.name,
            parameters=self._parse_parameters(node.args),
            return_type=ast.unparse(node.returns) if node.returns else None,
            docstring=ast.get_docstring(node),
            decorators=[ast.unparse(d) for d in node.decorator_list],
            is_async=isinstance(node, ast.AsyncFunctionDef),
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
        )
        source_segment = ast.get_source_segment(source, node)
        if source_segment:
            func_info.extra["source_code"] = source_segment
        return func_info
    
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
