"""
test_doc/analyzer.py

實作 BaseAnalyzer：先用共用的 PythonAstAnalyzer 拿到函式/類別清單，
再針對每一個「值得測試的對象」用靜態分析算出：
  - 圈複雜度（分支有多少，分支越多越容易藏 bug）
  - 會 raise 哪些例外（每一個都是該測的錯誤路徑）
  - 依參數型別/預設值推出的邊界條件提示
  - 有沒有既有的測試檔引用它
  - 綜合上面這些的風險分數與原因（用來決定測試順序，以及哪些對象值得花 token 請 LLM 設計案例）

這個階段完全不使用 LLM。分數的算法是刻意寫成簡單、可以解釋的規則（見 _score_risk），
文件裡也會把「為什麼這個函式被排在前面」的原因列出來，不是黑箱。
"""

import ast
import os
import textwrap
from typing import Dict, List, Optional, Set, Union

from core.base import BaseAnalyzer
from core.models import AnalysisResult, FunctionInfo, ModuleInfo, ParameterInfo
from core.python_analyzer import PythonAstAnalyzer
from test_doc.models import TEST_DOC_EXTRA_KEY, TargetCollection, TargetInfo

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
SKIPPED_PARAMETER_NAMES = ("self", "cls")

# 各種型別建議測的邊界條件
KIND_HINTS = {
    "int": "0、負數、很大的數值",
    "float": "0.0、負數、極大或極小的數值",
    "str": "空字串、只有空白、很長的字串、含特殊字元或非 ASCII 字元",
    "bool": "True 與 False 兩種情況都要測",
    "bytes": "空 bytes、很大的資料",
    "list": "空集合、只有一個元素、多個元素、含重複元素",
    "dict": "空字典、缺少預期的 key、巢狀結構",
    "path": "不存在的路徑、空路徑、指到目錄而不是檔案",
}
# 型別名稱（小寫）-> 上面 KIND_HINTS 的分類
KIND_ALIASES = {
    "int": "int", "float": "float", "str": "str", "bool": "bool", "bytes": "bytes",
    "list": "list", "sequence": "list", "iterable": "list", "collection": "list",
    "tuple": "list", "set": "list", "frozenset": "list", "deque": "list",
    "dict": "dict", "mapping": "dict", "ordereddict": "dict", "defaultdict": "dict",
    "path": "path", "pathlike": "path",
}
PATH_LIKE_WORDS = ("path", "file", "dir", "folder")


class TargetAnalyzer(BaseAnalyzer):
    def __init__(self) -> None:
        self._python_analyzer = PythonAstAnalyzer()

    def analyze(self, file_paths: List[str]) -> AnalysisResult:
        result = self._python_analyzer.analyze(file_paths)
        result.extra[TEST_DOC_EXTRA_KEY] = self._build(result.modules)
        return result

    def _build(self, modules: List[ModuleInfo]) -> TargetCollection:
        data = TargetCollection()
        if not modules:
            return data

        modules = sorted(modules, key=lambda m: m.file_path)
        root = os.path.commonpath([os.path.dirname(m.file_path) for m in modules])
        data.root_path = root
        relative = {m.file_path: _relative(m.file_path, root) for m in modules}

        # 把「測試檔」跟「被測試的程式碼」分開：測試檔本身的函式不是測試對象，
        # 但它們引用了哪些名字，可以用來判斷哪些函式已經有人測過
        test_modules = [m for m in modules if _is_test_file(relative[m.file_path])]
        source_modules = [m for m in modules if not _is_test_file(relative[m.file_path])]
        data.test_files = sorted(relative[m.file_path] for m in test_modules)
        referenced_names = {
            relative[m.file_path]: _collect_referenced_names(m.file_path) for m in test_modules
        }

        for module in source_modules:
            data.targets.extend(
                self._targets_from_module(module, relative[module.file_path], referenced_names)
            )
        data.targets.sort(key=lambda t: (-t.risk_score, t.key))
        return data

    def _targets_from_module(
        self,
        module: ModuleInfo,
        relative_path: str,
        referenced_names: Dict[str, Set[str]],
    ) -> List[TargetInfo]:
        # PythonAstAnalyzer 沒有記錄函式的內部結構（分支、raise），這裡再解析一次原始碼，
        # 用「函式開頭的行號」對回 ast 節點，才能算圈複雜度、找 raise
        try:
            with open(module.file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            return []
        nodes_by_line: Dict[int, FunctionNode] = {
            node.lineno: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        targets: List[TargetInfo] = []
        for func in module.functions:
            target = self._make_target(
                func, None, None, relative_path, source, nodes_by_line, referenced_names
            )
            if target:
                targets.append(target)

        for cls in module.classes:
            init = next((m for m in cls.methods if m.name == "__init__"), None)
            init_signature = _format_signature(init) if init else None
            for method in cls.methods:
                target = self._make_target(
                    method, cls.name, init_signature, relative_path, source,
                    nodes_by_line, referenced_names,
                )
                if target:
                    targets.append(target)
        return targets

    def _make_target(
        self,
        func: FunctionInfo,
        owner: Optional[str],
        owner_init_signature: Optional[str],
        relative_path: str,
        source: str,
        nodes_by_line: Dict[int, FunctionNode],
        referenced_names: Dict[str, Set[str]],
    ) -> Optional[TargetInfo]:
        # 抽象方法只有介面、沒有實作，沒辦法直接測，跳過
        if any(d.split(".")[-1] == "abstractmethod" for d in func.decorators):
            return None

        node = nodes_by_line.get(func.line_start)
        parameters = [p for p in func.parameters if p.name not in SKIPPED_PARAMETER_NAMES]
        qualified = f"{owner}.{func.name}" if owner else func.name

        target = TargetInfo(
            key=f"{relative_path}::{qualified}",
            file_path=relative_path,
            name=func.name,
            owner=owner,
            kind="method" if owner else "function",
            visibility=_visibility(func.name),
            signature=_format_signature(func),
            parameters=parameters,
            return_type=func.return_type,
            docstring=func.docstring,
            is_async=func.is_async,
            line_count=func.line_end - func.line_start + 1,
            complexity=_cyclomatic_complexity(node) if node is not None else 1,
            raises=_raised_exceptions(node) if node is not None else [],
            boundary_hints=_boundary_hints(parameters),
            source_code=_extract_source(source, node) if node is not None else "",
            owner_init_signature=owner_init_signature,
        )
        target.covered_by = _find_covering_tests(target, referenced_names)
        target.risk_score, target.risk_reasons = _score_risk(target)
        return target


# =====================================================================
# 以下是不需要用到 self 的輔助函式
# =====================================================================


def _relative(file_path: str, root: str) -> str:
    return os.path.relpath(file_path, root).replace(os.sep, "/")


def _is_test_file(relative_path: str) -> bool:
    """依慣例判斷是不是測試檔：test_*.py、*_test.py、conftest.py，或放在 tests/、test/ 資料夾底下"""
    parts = relative_path.split("/")
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or any(part in ("tests", "test") for part in parts[:-1])
    )


def _visibility(name: str) -> str:
    if name == "__init__":
        return "constructor"
    if name.startswith("__") and name.endswith("__"):
        return "dunder"
    if name.startswith("_"):
        return "private"
    return "public"


def _format_signature(func: FunctionInfo) -> str:
    parts = []
    for p in func.parameters:
        if p.name in SKIPPED_PARAMETER_NAMES:
            continue
        text = p.name
        if p.type_annotation:
            text += f": {p.type_annotation}"
        if p.default_value is not None:
            text += f" = {p.default_value}"
        parts.append(text)
    signature = f"{'async ' if func.is_async else ''}{func.name}({', '.join(parts)})"
    if func.return_type:
        signature += f" -> {func.return_type}"
    return signature


def _extract_source(source: str, node: FunctionNode) -> str:
    """
    取出函式的原始碼並去掉縮排。
    ast.get_source_segment 回傳的第一行沒有縮排、後面幾行卻保留原本的縮排，
    所以要先把第一行補回 col_offset 個空白，dedent 才能正確去掉共同縮排。
    """
    segment = ast.get_source_segment(source, node)
    if not segment:
        return ""
    return textwrap.dedent(" " * node.col_offset + segment)


def _cyclomatic_complexity(node: FunctionNode) -> int:
    """
    圈複雜度 = 1 + 決策點的數量（if、迴圈、except、三元運算、and/or、推導式的條件...）。
    數值就是「要走遍所有分支，至少需要幾個測試案例」的粗略估計。
    """
    match_case = getattr(ast, "match_case", None)  # match 敘述在 Python 3.10 才有
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            complexity += 1 + len(child.ifs)
        elif match_case is not None and isinstance(child, match_case):
            complexity += 1
    return complexity


def _raised_exceptions(node: FunctionNode) -> List[str]:
    """函式裡明確 `raise XXX(...)` 的例外類別名稱（單純 `raise` 重新拋出不算）"""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Raise) and child.exc is not None:
            exc = child.exc.func if isinstance(child.exc, ast.Call) else child.exc
            names.add(ast.unparse(exc))
    return sorted(names)


def _simple_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _annotation_kinds(annotation: str) -> List[str]:
    """把型別標註字串（"Optional[int]"、"str | None"、"List[str]"）攤平成型別分類清單"""
    try:
        tree = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return []
    kinds: List[str] = []
    _collect_kinds(tree, kinds)
    return kinds


def _collect_kinds(node: ast.AST, kinds: List[str]) -> None:
    if isinstance(node, ast.Constant):
        if node.value is None:
            kinds.append("none")
        elif isinstance(node.value, str):  # 字串形式的前向參照，例如 "Foo"
            kinds.extend(_annotation_kinds(node.value))
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):  # int | None
        _collect_kinds(node.left, kinds)
        _collect_kinds(node.right, kinds)
    elif isinstance(node, ast.Subscript):
        outer = _simple_name(node.value)
        if outer in ("Optional", "Union"):
            if outer == "Optional":
                kinds.append("none")
            inner = node.slice
            for element in inner.elts if isinstance(inner, ast.Tuple) else [inner]:
                _collect_kinds(element, kinds)
        else:  # List[str] 這類容器，只看最外層（容器本身），不管裡面裝什麼
            kind = KIND_ALIASES.get(outer.lower())
            if kind:
                kinds.append(kind)
    else:
        kind = KIND_ALIASES.get(_simple_name(node).lower())
        if kind:
            kinds.append(kind)


def _default_kind(default_value: str) -> Optional[str]:
    """沒有型別標註時，從預設值的樣子猜型別"""
    try:
        value = ast.literal_eval(default_value)
    except (ValueError, SyntaxError):
        return None
    if value is None:
        return "none"
    for python_type, kind in (
        (bool, "bool"), (int, "int"), (float, "float"), (str, "str"), (bytes, "bytes"),
        ((list, tuple, set, frozenset), "list"), (dict, "dict"),
    ):
        if isinstance(value, python_type):
            return kind
    return None


def _boundary_hints(parameters: List[ParameterInfo]) -> List[str]:
    hints: List[str] = []
    for p in parameters:
        if p.name.startswith("*"):
            hints.append(f"{p.name}：不傳入任何參數、傳入一個、傳入多個")
            continue

        kinds = _annotation_kinds(p.type_annotation) if p.type_annotation else []
        if not kinds and p.default_value is not None:
            default_kind = _default_kind(p.default_value)
            if default_kind:
                kinds = [default_kind]
        if p.default_value == "None" and "none" not in kinds:
            kinds.append("none")
        # 沒有明確型別、或型別是字串時，名字像路徑的參數也提示路徑相關的邊界條件
        if any(word in p.name.lower() for word in PATH_LIKE_WORDS) and "path" not in kinds:
            if not kinds or "str" in kinds:
                kinds.append("path")

        seen: Set[str] = set()
        for kind in kinds:
            if kind in seen:
                continue
            seen.add(kind)
            if kind == "none":
                hints.append(f"{p.name}：None（這個參數可以是 None）")
            elif kind in KIND_HINTS:
                hints.append(f"{p.name}（{kind}）：{KIND_HINTS[kind]}")
    return hints


def _collect_referenced_names(file_path: str) -> Set[str]:
    """測試檔裡出現過的所有名字（變數、屬性、import 進來的名字），用來粗略判斷哪些函式有被測試引用"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


def _find_covering_tests(target: TargetInfo, referenced_names: Dict[str, Set[str]]) -> List[str]:
    """
    粗略判斷哪些測試檔「引用」了這個對象。這是比對名字的啟發式做法，不是真的執行測試量覆蓋率：
      - 模組函式：測試檔裡出現同名的名字
      - 建構子：測試檔裡出現所屬類別的名字（建立物件就是在呼叫建構子）
      - 方法：測試檔裡同時出現方法名稱與所屬類別名稱
    名字很常見的函式（例如 run、get）可能誤判成「有測試」，所以文件裡會註明這是粗略的結果。
    """
    covering = []
    for test_file, names in referenced_names.items():
        if target.owner is None:
            hit = target.name in names
        elif target.name == "__init__":
            hit = target.owner in names
        else:
            hit = target.name in names and target.owner in names
        if hit:
            covering.append(test_file)
    return sorted(covering)


def _score_risk(target: TargetInfo):
    """
    風險分數 = 「這個對象出錯的可能性」與「出錯了不容易被發現」的粗略估計。
    每一項都對應一個可以解釋的原因，會一起寫進文件，所以調整權重時也方便檢查。
    """
    score = 0.0
    reasons: List[str] = []

    if target.complexity > 1:
        score += (target.complexity - 1) * 2
        reasons.append(f"圈複雜度 {target.complexity}（分支多，至少需要 {target.complexity} 個案例才能走遍）")
    if target.raises:
        score += 2 * len(target.raises)
        reasons.append(f"會拋出 {', '.join(target.raises)}（每個例外都是需要驗證的錯誤路徑）")
    if target.parameters:
        score += min(len(target.parameters), 6) * 0.5
        if len(target.parameters) >= 4:
            reasons.append(f"{len(target.parameters)} 個參數（輸入組合多）")
    score += min(target.line_count / 10, 3)
    if target.line_count >= 30:
        reasons.append(f"函式很長（{target.line_count} 行）")
    if not target.docstring:
        score += 1
        reasons.append("沒有 docstring，預期行為缺乏書面說明")
    if not target.covered_by:
        score += 3
        reasons.append("沒有任何既有測試檔引用它")

    # 私有函式與特殊方法通常是透過公開介面被間接測到的，優先順序往後排；
    # 但如果它本身夠複雜，打折後分數仍然可能很高，一樣會排進前面
    if target.visibility in ("private", "dunder"):
        score *= 0.5
        reasons.append("私有/特殊方法，通常透過公開介面間接測試，分數已打折")
    return round(score, 1), reasons
