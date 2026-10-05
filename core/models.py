"""
core/models.py

這個檔案定義「靜態分析結果」要長成什麼樣子。
所有功能模組（requirements_spec、未來的 architecture_diagram、test_doc...）
都共用這一套資料結構，這樣不同功能之間的分析結果可以互通、甚至互相組合。

設計重點：
1. 用 dataclass，而不是 dict：
   - dict 沒有固定欄位，寫錯 key 名稱不會報錯，容易出 bug 又難維護。
   - dataclass 有明確欄位定義，IDE 會自動提示，寫錯馬上被抓到。

2. 每個 dataclass 都留了 `extra: Dict[str, Any]` 欄位：
   - 這是為了「未來加 LLM」預留的擴充空間。
   - 例如以後 LLM 幫某個函式補了一段「白話說明」，
     可以直接塞進 extra["llm_explanation"]，不用去改動這個檔案、
     不用動任何既有的 analyzer 程式碼。
   - 也就是說：新增能力 → 加資料進 extra；不需要 → 改 schema、動舊程式碼。

3. 每個 dataclass 都留了 line_start / line_end：
   - 現在的文件用不到，但未來如果要做「原始碼片段連結」、
     或是要讓 LLM 針對特定行數範圍做深入分析，這個資訊會需要。
   - 靜態分析階段做起來幾乎不花額外成本（ast 本身就有這個資訊），
     所以先存起來，之後比較不會後悔。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParameterInfo:
    """函式/方法的單一參數資訊"""
    name: str
    type_annotation: Optional[str] = None   # 例如 "int"、"str"，沒標註就是 None
    default_value: Optional[str] = None     # 預設值的原始碼字串，例如 "5"、"None"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FunctionInfo:
    """
    一個函式或方法的資訊（不管是模組層級的函式，還是類別裡的方法）。

    設計備註：
    刻意讓「類別方法」和「模組函式」共用這一個 dataclass，不另外拆成
    MethodInfo，用「放在哪裡」來區分身份 —— 出現在 ClassInfo.methods
    裡的就是方法，出現在 ModuleInfo.functions 裡的就是模組層級函式。
    這樣可以少維護一份幾乎重複的結構。如果未來發現方法真的需要
    函式沒有的專屬欄位（例如 self 的型別、是否為 property），
    可以再考慮拆開，或直接用 extra 塞進去就好，不急著現在拆。
    """
    name: str
    parameters: List[ParameterInfo] = field(default_factory=list)
    return_type: Optional[str] = None       # 回傳型別標註，例如 "-> bool"
    docstring: Optional[str] = None
    decorators: List[str] = field(default_factory=list)   # 例如 ["staticmethod"]
    is_async: bool = False
    line_start: int = 0
    line_end: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassInfo:
    """一個類別的資訊"""
    name: str
    bases: List[str] = field(default_factory=list)         # 繼承的父類別名稱
    docstring: Optional[str] = None
    methods: List[FunctionInfo] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    line_start: int = 0
    line_end: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportInfo:
    """一筆 import 敘述"""
    module: str                              # 例如 "os.path"、"typing"
    names: List[str] = field(default_factory=list)  # from X import a, b -> ["a", "b"]；直接 import X 則為空
    alias: Optional[str] = None              # import X as Y -> "Y"


@dataclass
class CodeBlockInfo:
    """
    模組層級、沒有被封裝進 function/class 的程式碼片段
    （例如 `if __name__ == "__main__":` 底下的執行邏輯、模組層級的變數賦值、
    直接寫在檔案最外層的程式邏輯）。

    這種程式碼靜態分析沒辦法知道「用途」是什麼，只能先把原始碼存起來，
    之後交給 LLM 讀程式碼內容、理解並生成說明，放進 extra["llm_explanation"]。

    連續好幾行這種「沒有結構」的敘述，會被合併成同一個 CodeBlockInfo
    （而不是一行一個），這樣丟給 LLM 時脈絡比較完整，也比較省呼叫次數。
    """
    source_code: str
    line_start: int
    line_end: int
    extra: Dict[str, Any] = field(default_factory=dict)
    
    
@dataclass
class ModuleInfo:
    """一個檔案（模組）分析完的完整結果，是 analyzer 最終輸出的單位"""
    file_path: str
    module_docstring: Optional[str] = None
    imports: List[ImportInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)   # 只放「模組層級」的函式，類別內的在 classes[].methods
    classes: List[ClassInfo] = field(default_factory=list)
    code_blocks: List[CodeBlockInfo] = field(default_factory=list)  # 沒有結構化的頂層程式碼片段
    line_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    """
    整個分析任務的最終結果（可能是一個檔案，也可能是一整個資料夾多個檔案）。
    這是 source_loader -> analyzer 這條流程最後交給「產生文件」那一層的東西。
    """
    source_path: str                          # 使用者一開始輸入的路徑（檔案或資料夾）
    modules: List[ModuleInfo] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
