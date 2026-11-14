"""
architecture_diagram/analyzer.py

實作 BaseAnalyzer：先呼叫共用的 PythonAstAnalyzer 拿到「每個檔案有哪些 import、哪些類別」，
再從這些資料推算出兩張圖需要的關係：
  1. 檔案依賴：哪個 .py 檔案 import 了專案裡的哪個 .py 檔案
  2. 類別繼承：哪個類別繼承了專案裡的哪個類別

這一階段完全不使用 LLM：import 與繼承都是確定性的結構關係，
用靜態分析就能得到精確結果；交給 LLM 反而可能「猜出」不存在的關係，又浪費 token。
（LLM 之後比較有價值的用途是替各個元件歸類成「資料層/業務層/介面層」之類，
結果可以寫進 ArchitectureGraph.extra，這裡先不做。）
"""

import os
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from architecture_diagram.models import ARCHITECTURE_EXTRA_KEY, ArchitectureGraph
from core.base import BaseAnalyzer
from core.models import AnalysisResult, ImportInfo, ModuleInfo
from core.python_analyzer import PythonAstAnalyzer

# 一個類別的唯一識別：(相對路徑, 類別名稱)
ClassId = Tuple[str, str]


class ArchitectureAnalyzer(BaseAnalyzer):
    def __init__(self) -> None:
        self._python_analyzer = PythonAstAnalyzer()

    def analyze(self, file_paths: List[str]) -> AnalysisResult:
        result = self._python_analyzer.analyze(file_paths)
        result.extra[ARCHITECTURE_EXTRA_KEY] = self._build_graph(result.modules)
        return result

    def _build_graph(self, modules: List[ModuleInfo]) -> ArchitectureGraph:
        graph = ArchitectureGraph()
        if not modules:
            return graph

        modules = sorted(modules, key=lambda m: m.file_path)

        # 所有檔案路徑的共同上層資料夾，當作專案根目錄，
        # 之後所有路徑都轉成「相對於它」的樣子，圖上才不會出現一長串絕對路徑
        root = os.path.commonpath([os.path.dirname(m.file_path) for m in modules])
        graph.root_path = root
        relative_path = {m.file_path: _relative(m.file_path, root) for m in modules}
        graph.modules = sorted(relative_path.values())

        # 建立「模組名稱 -> 檔案」的對照表。
        # 同一個檔案可能有兩個名字（見 _module_identity 的說明），所以都登記進去。
        identities = {m.file_path: _module_identity(m.file_path, root) for m in modules}
        index: Dict[str, str] = {}
        for file_path in sorted(identities):
            names, _package = identities[file_path]
            for name in names:
                index.setdefault(name, file_path)

        edges: Set[Tuple[str, str]] = set()
        # 每個檔案「從哪裡 import 了哪些名字」，繼承分析時要用它判斷同名類別到底指哪一個
        imported_symbols: Dict[str, Dict[str, Set[str]]] = {}
        for module in modules:
            _names, package = identities[module.file_path]
            imported_symbols[module.file_path] = _imported_symbol_files(
                module, package, index, relative_path
            )
            for imp in module.imports:
                for target in _resolve_import(imp, package, index):
                    if target != module.file_path:  # 不記錄「自己依賴自己」
                        edges.add((relative_path[module.file_path], relative_path[target]))

        graph.dependencies = sorted(edges)
        graph.cycles = _find_cycles(graph.modules, graph.dependencies)

        self._build_inheritance(modules, relative_path, imported_symbols, graph)
        return graph

    def _build_inheritance(
        self,
        modules: List[ModuleInfo],
        relative_path: Dict[str, str],
        imported_symbols: Dict[str, Dict[str, Set[str]]],
        graph: ArchitectureGraph,
    ) -> None:
        classes_by_name: Dict[str, List[ClassId]] = defaultdict(list)
        for module in modules:
            for cls in module.classes:
                classes_by_name[cls.name].append((relative_path[module.file_path], cls.name))
                graph.total_classes += 1

        edges: List[Tuple[ClassId, ClassId]] = []
        for module in modules:
            imported = imported_symbols[module.file_path]
            for cls in module.classes:
                child = (relative_path[module.file_path], cls.name)
                for base in cls.bases:
                    parent = _resolve_base(base, child, classes_by_name, imported)
                    if parent is not None:
                        edges.append((child, parent))

        # 只把「真的有參與繼承關係」的類別畫進圖裡，沒有繼承關係的類別放進去只會變雜訊
        involved = sorted({node for edge in edges for node in edge})
        name_counts: Dict[str, int] = defaultdict(int)
        for _path, name in involved:
            name_counts[name] += 1
        for path, name in involved:
            key = f"{path}::{name}"
            # 不同檔案有同名類別時，標籤加上檔案路徑才分得出來
            graph.class_labels[key] = f"{name}\n({path})" if name_counts[name] > 1 else name
            graph.class_files[key] = path
        graph.inheritances = sorted((f"{c[0]}::{c[1]}", f"{p[0]}::{p[1]}") for c, p in edges)


# =====================================================================
# 以下是不需要用到 self 的輔助函式
# =====================================================================


def _relative(file_path: str, root: str) -> str:
    return os.path.relpath(file_path, root).replace(os.sep, "/")


def _dotted(relative_file_path: str) -> str:
    """'core/models.py' -> 'core.models'；'pkg/__init__.py' -> 'pkg'"""
    without_ext = os.path.splitext(relative_file_path.replace(os.sep, "/"))[0]
    parts = without_ext.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_root(file_path: str) -> Optional[str]:
    """
    如果這個檔案位在「正規的 package」裡（所在資料夾有 __init__.py），
    就一路往上走到「最上層那個 package 的上一層」，那裡才是 Python 真正的 import 根目錄。
    例如 repo/src/mypkg/x.py，import 時的寫法是 `import mypkg.x`，
    import 根目錄是 repo/src，而不是 repo。
    不在 package 裡的檔案（沒有 __init__.py）回傳 None。
    """
    directory = os.path.dirname(file_path)
    if not os.path.isfile(os.path.join(directory, "__init__.py")):
        return None
    while os.path.isfile(os.path.join(directory, "__init__.py")):
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return directory


def _module_identity(file_path: str, root: str) -> Tuple[Set[str], str]:
    """
    回傳 (這個檔案可能被 import 的名字們, 它所屬的 package 名稱)。

    一個檔案可能有兩個名字：
      - 相對於「分析根目錄」的名字：repo/src/mypkg/x.py -> "src.mypkg.x"
      - 相對於「Python import 根目錄」的名字：-> "mypkg.x"（程式碼裡實際會這樣寫）
    兩個都登記，後面比對 import 敘述時才不會因為專案是 src/ 結構就全部對不上。
    """
    root_name = _dotted(os.path.relpath(file_path, root))
    package_root = _package_root(file_path)
    real_name = _dotted(os.path.relpath(file_path, package_root)) if package_root else root_name

    names = {name for name in (root_name, real_name) if name}
    if os.path.basename(file_path) == "__init__.py":
        package = real_name  # __init__.py 本身就代表那個 package
    else:
        package = real_name.rpartition(".")[0]  # 一般檔案：去掉最後一段，剩下的就是所屬 package
    return names, package


def _import_base(imp: ImportInfo, package: str) -> Optional[str]:
    """
    算出一筆 import 敘述「來源模組」的完整名稱。
    例如在 package "app" 裡的 `from .models import X`（level=1, module="models"）-> "app.models"。
    相對匯入超出最上層 package 時（Python 本身也會報錯的寫法）回傳 None。
    """
    if imp.level == 0:
        return imp.module

    # 相對匯入：level=1 代表「目前所在的 package」，每多一層就再往上走一層
    parts = package.split(".") if package else []
    levels_up = imp.level - 1
    if levels_up > 0 and levels_up >= len(parts):
        return None  # 已經走到（或超過）最上層 package 之外了
    parts = parts[: len(parts) - levels_up]
    if imp.module:
        parts += imp.module.split(".")
    return ".".join(parts)


def _resolve_import(imp: ImportInfo, package: str, index: Dict[str, str]) -> Set[str]:
    """判斷一筆 import 敘述指向專案裡的哪些檔案（指向第三方套件/標準庫的，回傳空集合）"""
    base = _import_base(imp, package)
    if base is None:
        return set()

    targets: Set[str] = set()
    if imp.names:
        # from X import a, b：a、b 有可能是 X 底下的子模組（那就是依賴 X.a、X.b 這些檔案），
        # 也可能只是 X 裡的函式/類別（那就是依賴 X 本身）
        for name in imp.names:
            submodule = f"{base}.{name}" if base else name
            if submodule in index:
                targets.add(index[submodule])
        if not targets and base in index:
            targets.add(index[base])
    elif base in index:
        targets.add(index[base])  # import X.Y
    return targets


def _imported_symbol_files(
    module: ModuleInfo,
    package: str,
    index: Dict[str, str],
    relative_path: Dict[str, str],
) -> Dict[str, Set[str]]:
    """
    整理出「這個檔案 import 進來的名字，各自來自專案裡的哪個檔案」。
    例如 `from .base import Shape` -> {"Shape": {"app/base.py"}}。
    只收「名字本身不是子模組」的（也就是函式/類別這類符號），專案以外的來源不收。
    """
    result: Dict[str, Set[str]] = defaultdict(set)
    for imp in module.imports:
        if not imp.names:
            continue
        base = _import_base(imp, package)
        if base is None or base not in index:
            continue
        for name in imp.names:
            submodule = f"{base}.{name}" if base else name
            if submodule in index:
                continue  # 這個名字是子模組，不是類別/函式
            result[name].add(relative_path[index[base]])
    return result


def _resolve_base(
    base: str,
    child: ClassId,
    classes_by_name: Dict[str, List[ClassId]],
    imported: Dict[str, Set[str]],
) -> Optional[ClassId]:
    """
    判斷一個「父類別寫法」指向專案裡的哪個類別。
    寫法可能是 "Base"、"core.base.Base"、"Generic[T]"，都只取最後的類別名稱去比對。
    找不到（例如 ABC、Exception 這種不在專案裡的）就回傳 None。

    專案裡有好幾個同名類別時，依序用這三個線索判斷，越前面越可靠：
      1. 這個檔案是從哪個檔案 import 這個名字的（from .base import Shape -> base.py 的 Shape）
      2. 同一個檔案裡定義的
      3. 整個專案裡只有一個叫這個名字的
    三個線索都分不出來，就放棄 —— 寧可少畫一條線，也不要畫錯。
    """
    simple_name = base.split("[")[0].split(".")[-1].strip()
    candidates = [c for c in classes_by_name.get(simple_name, []) if c != child]
    if not candidates:
        return None

    imported_from = imported.get(simple_name, set())
    by_import = [c for c in candidates if c[0] in imported_from]
    if len(by_import) == 1:
        return by_import[0]

    same_file = [c for c in candidates if c[0] == child[0]]
    if len(same_file) == 1:
        return same_file[0]

    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_cycles(modules: List[str], edges: List[Tuple[str, str]]) -> List[List[str]]:
    # 延遲 import：networkx 沒裝的話，只有這個功能會出錯（被 main.py 單一功能的錯誤處理接住），
    # 不會讓整個程式一啟動就壞掉、連其他功能都跑不了
    import networkx as nx

    graph = nx.DiGraph()
    graph.add_nodes_from(modules)
    graph.add_edges_from(edges)
    # 強連通元件：裡面任兩個節點都能互相走到對方，大小 > 1 就代表存在循環依賴
    cycles = [
        sorted(component)
        for component in nx.strongly_connected_components(graph)
        if len(component) > 1
    ]
    return sorted(cycles)
