"""
core/source_loader.py

負責處理「輸入來源」這件事：使用者可能給一個單一 .py 檔案的路徑、
一個資料夾路徑，未來還可能給一個 git repo 網址。

這個模組的工作，就是把上面這些五花八門的輸入，統一轉換成
一份「要分析的 Python 檔案絕對路徑清單」，讓後面的 analyzer
完全不用管輸入到底長什麼樣子、也不用知道怎麼走訪資料夾。

擴充設計：
用抽象基底類別 SourceLoader + 工廠函式 get_source_loader() 的組合。
以後要加「git repo」來源，只要：
  1. 新增一個繼承 SourceLoader 的類別，實作 load()
  2. 在 get_source_loader() 裡加一個判斷條件
main.py 和 analyzer 完全不用改，因為它們只認得 SourceLoader 這個介面，
不管背後實際是哪一種 Loader 在運作。
"""

import os
from abc import ABC, abstractmethod
from typing import List

# 目前支援分析的原始碼副檔名。
# 現在只有 .py 真正有 analyzer 支援，其他語言先放在註解裡示意，
# 之後要加 Java / HTML 支援時，把對應副檔名加進這個集合即可，
# DirectorySourceLoader 的掃描邏輯完全不用改。
SUPPORTED_EXTENSIONS = {
    ".py",
    # ".java",   # 未來擴充
    # ".html",   # 未來擴充
}


class SourceLoader(ABC):
    """所有來源載入器的共同介面"""

    def __init__(self, source: str):
        self.source = source

    @abstractmethod
    def load(self) -> List[str]:
        """回傳這個來源底下，所有要分析的 Python 檔案的絕對路徑清單"""
        raise NotImplementedError


class FileSourceLoader(SourceLoader):
    """來源是單一檔案"""

    def load(self) -> List[str]:
        _, ext = os.path.splitext(self.source)
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"目前支援的檔案類型為 {sorted(SUPPORTED_EXTENSIONS)}，"
                f"收到的檔案是：{self.source}"
            )
        return [os.path.abspath(self.source)]


class DirectorySourceLoader(SourceLoader):
    """來源是資料夾，遞迴找出底下所有 .py 檔案"""

    # 這些資料夾通常不是「使用者自己寫的程式碼」，掃到只會製造雜訊，直接跳過
    EXCLUDED_DIR_NAMES = {
        "__pycache__", ".git", ".venv", "venv", "env",
        "node_modules", "build", "dist", ".pytest_cache",
    }

    def load(self) -> List[str]:
        py_files: List[str] = []
        for root, dirs, files in os.walk(self.source):
            # 用 dirs[:] = ... 原地修改，讓 os.walk 不要再往這些資料夾裡走
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIR_NAMES]
            for filename in files:
                _, ext = os.path.splitext(filename)
                if ext in SUPPORTED_EXTENSIONS:
                    py_files.append(os.path.abspath(os.path.join(root, filename)))
        return sorted(py_files)


class GitRepoSourceLoader(SourceLoader):
    """
    來源是 git repo（尚未實作，先卡位）。

    之後實作大概會長這樣：
      1. clone 或 fetch repo 到暫存資料夾
      2. 直接重用 DirectorySourceLoader 去掃那個暫存資料夾
    main.py 那邊完全不用改任何呼叫方式，因為對外都是同一個 load() 介面。
    """

    def load(self) -> List[str]:
        raise NotImplementedError("Git repo 來源尚未實作")


def get_source_loader(source: str) -> SourceLoader:
    """
    工廠函式：依照 source 字串的樣子，決定要用哪一種 SourceLoader。
    這是整個模組唯一對外的入口 —— main.py / analyzer 只需要呼叫這個函式，
    完全不需要知道底下有哪些 Loader 類別存在，也不用自己判斷路徑類型。
    """
    if source.startswith(("http://", "https://", "git@")):
        return GitRepoSourceLoader(source)
    if os.path.isdir(source):
        return DirectorySourceLoader(source)
    if os.path.isfile(source):
        return FileSourceLoader(source)
    raise FileNotFoundError(f"找不到指定的來源路徑：{source}")
