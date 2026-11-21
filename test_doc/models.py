"""
test_doc/models.py

測試文件功能「專屬」的資料結構。

命名備註：這裡的類別名稱（CaseSpec、TargetInfo、TargetCollection）刻意避開
「Test」開頭。pytest 會把名稱以 Test 開頭的類別當成測試類別去收集，
之後如果你在測試檔裡 import 這些類別，會跳出一堆無關的警告。

整個測試文件功能的資料流：
  analyzer 找出 TargetInfo（測試對象）並算好風險 → llm_enhancer 替高風險的對象產生 CaseSpec
  → generator 排成 docx。
CaseSpec 是刻意設計成「機器看得懂」的結構化資料，而不是一段文字：
docx 把它排成表格，未來的 pytest 輸出會直接把同一份 CaseSpec 轉成測試程式碼，
不需要重新分析、也不需要再花一次 LLM 的 token。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.models import ParameterInfo

# AnalysisResult.extra 裡存放測試文件資料的 key
TEST_DOC_EXTRA_KEY = "test_doc"

# 「重點測試對象」的數量 N：風險排名前 N 名的對象，會在文件裡被詳細說明，
# 也只有這 N 個會送給 LLM 產生測試案例。要控制 token 用量，調這個數字就好。
FOCUS_TARGET_COUNT = 10


@dataclass
class CaseSpec:
    """一個測試案例。inputs / setup / expected 都是「Python 運算式的字串」，方便未來直接轉成程式碼"""
    title: str                                   # 一句話的案例名稱
    category: str = "normal"                     # normal（正常）/ boundary（邊界）/ error（錯誤處理）
    inputs: Dict[str, str] = field(default_factory=dict)   # 參數名稱 -> 值的運算式，例如 {"a": "1"}
    setup: Optional[str] = None                  # 測試「方法」時，建立物件的運算式，例如 "Calculator()"
    expected: Optional[str] = None               # 預期回傳值的運算式；None 代表「無法從程式碼確定，需人工確認」
    expected_exception: Optional[str] = None     # 預期拋出的例外類別名稱，例如 "ValueError"
    description: str = ""                        # 這個案例在驗證什麼
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetInfo:
    """一個「值得被測試的對象」：一個函式，或一個類別的方法"""
    key: str                                     # 唯一代號："路徑::函式" 或 "路徑::類別.方法"
    file_path: str                               # 相對於專案根目錄的路徑
    name: str                                    # 函式/方法名稱
    owner: Optional[str] = None                  # 方法所屬的類別名稱；模組層級函式為 None
    kind: str = "function"                       # function / method
    visibility: str = "public"                   # public / private / constructor / dunder
    signature: str = ""                          # 顯示用的簽名，例如 "add(a: int, b: int) -> int"
    parameters: List[ParameterInfo] = field(default_factory=list)   # 不含 self / cls
    return_type: Optional[str] = None
    docstring: Optional[str] = None
    is_async: bool = False
    line_count: int = 0
    complexity: int = 1                          # 圈複雜度：分支越多越大
    raises: List[str] = field(default_factory=list)          # 程式碼裡明確 raise 的例外
    boundary_hints: List[str] = field(default_factory=list)  # 依型別推出的邊界條件提示
    risk_score: float = 0.0
    risk_reasons: List[str] = field(default_factory=list)
    covered_by: List[str] = field(default_factory=list)      # 有引用到它的既有測試檔
    source_code: str = ""                        # 原始碼，給 LLM 讀
    owner_init_signature: Optional[str] = None   # 所屬類別的建構子簽名，給 LLM 理解怎麼建立物件
    test_cases: List[CaseSpec] = field(default_factory=list)  # LLM 產生的測試案例
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.owner}.{self.name}" if self.owner else self.name


@dataclass
class TargetCollection:
    """整個專案的測試對象集合，掛在 AnalysisResult.extra[TEST_DOC_EXTRA_KEY] 底下"""
    root_path: str = ""
    targets: List[TargetInfo] = field(default_factory=list)   # 依風險分數由高到低排序
    test_files: List[str] = field(default_factory=list)       # 專案裡偵測到的既有測試檔
    extra: Dict[str, Any] = field(default_factory=dict)       # llm_applied 等旗標也放這裡

    def focus_targets(self, count: Optional[int] = None) -> List[TargetInfo]:
        """風險最高的前 N 個對象（N 預設是 FOCUS_TARGET_COUNT）"""
        return self.targets[: count if count is not None else FOCUS_TARGET_COUNT]
