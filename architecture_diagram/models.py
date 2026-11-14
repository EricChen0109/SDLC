"""
architecture_diagram/models.py

架構圖功能「專屬」的資料結構。

跟 core/models.py 的差別：core/models.py 放的是「所有功能共用」的程式碼結構
（有哪些檔案、類別、函式），而這裡放的是「只有架構圖會用到」的分析結果
（檔案之間的依賴關係、類別之間的繼承關係），所以放在自己的資料夾裡，
不去污染共用的 core/models.py。

這份結果會掛在 AnalysisResult.extra[ARCHITECTURE_EXTRA_KEY] 底下，
由 analyzer 寫入、generator 讀出。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

# AnalysisResult.extra 裡存放架構圖資料的 key，analyzer 與 generator 共用這個常數，避免字串打錯
ARCHITECTURE_EXTRA_KEY = "architecture_graph"


@dataclass
class ArchitectureGraph:
    # 分析時推算出的專案根目錄（所有檔案路徑的共同上層資料夾）
    root_path: str = ""

    # ---- 模組依賴 ----
    # 專案裡所有被分析的檔案，用「相對於 root_path」的路徑表示，例如 "core/models.py"
    modules: List[str] = field(default_factory=list)
    # (A, B) 代表「A 這個檔案 import 了 B 這個檔案」（只收專案內部的依賴，第三方套件/標準庫不算）
    dependencies: List[Tuple[str, str]] = field(default_factory=list)
    # 循環依賴：每一組是互相依賴的檔案清單，例如 [["a.py", "b.py"]]
    cycles: List[List[str]] = field(default_factory=list)

    # ---- 類別繼承 ----
    total_classes: int = 0
    # 只收「有參與繼承關係」的類別。key 是唯一代號（"路徑::類別名稱"），value 是圖上顯示的文字
    class_labels: Dict[str, str] = field(default_factory=dict)
    # 同樣的 key，對應這個類別所在的檔案路徑（用來決定顏色分組）
    class_files: Dict[str, str] = field(default_factory=dict)
    # (子類別 key, 父類別 key)
    inheritances: List[Tuple[str, str]] = field(default_factory=list)

    # 預留給未來的擴充（例如 LLM 對各個元件寫的用途說明、分層歸類結果...）
    extra: Dict[str, Any] = field(default_factory=dict)
