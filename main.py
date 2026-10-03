"""
main.py

整個程式的進入點。只負責「調度」，不寫任何分析或產生文件的實際邏輯：
1. 解析命令列參數（分析來源、要跑哪個功能）
2. 把來源路徑交給 source_loader，統一整理成檔案清單
3. 依序呼叫每個功能（Feature）的 analyzer 跟 generator
4. 印出結果

用法：
    python main.py --source ./my_project
        → 掃過 REGISTERED_FEATURES 清單裡的每一個功能，全部都跑一次

    python main.py --source ./my_project --doc requirements_spec
        → 只跑 key 是 "requirements_spec" 的那個功能
"""

import argparse
import os
import sys
from typing import List

from core.base import Feature
from core.source_loader import get_source_loader

# ============================================================
# 功能註冊清單（手動註冊）
#
# 新增功能時，要做兩件事：
#   1. 在上面 import 區塊，仿照下面這行的樣子，import 新功能資料夾的 get_feature：
#        from architecture_diagram import get_feature as get_architecture_diagram_feature
#   2. 在下面的 REGISTERED_FEATURES 清單裡，加一行呼叫它：
#        get_architecture_diagram_feature(),
#
# 除了這兩處，不需要改動 main.py 其他任何地方。
# ============================================================
from requirements_spec import get_feature as get_requirements_spec_feature

REGISTERED_FEATURES: List[Feature] = [
    get_requirements_spec_feature(),
    # 未來新增功能，依照上面說明，在這裡加一行 ↓↓↓
    # get_architecture_diagram_feature(),
    # get_test_doc_feature(),
]


def build_feature_lookup() -> dict:
    """把註冊清單轉成 {key: Feature} 方便用 --doc 參數查找"""
    return {feature.key: feature for feature in REGISTERED_FEATURES}


def parse_args() -> argparse.Namespace:
    feature_keys = [f.key for f in REGISTERED_FEATURES]

    parser = argparse.ArgumentParser(
        description="分析程式碼並自動產生 SDLC 相關文件"
    )
    parser.add_argument(
        "--source",
        required=True,
        help="要分析的來源路徑，可以是單一檔案或資料夾",
    )
    parser.add_argument(
        "--doc",
        choices=feature_keys,
        default=None,
        help="只產生指定的文件類型；不指定則會產生所有已註冊的文件類型。"
        f"目前可選：{feature_keys}",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="輸出文件存放的資料夾，預設為 ./output",
    )
    parser.add_argument(
            "--debug",
            action="store_true",
            help="發生錯誤時顯示完整 traceback，方便除錯（預設只顯示簡短錯誤訊息）",
        )
    return parser.parse_args()


def run_feature(
    feature: Feature, file_paths: List[str], source: str, output_dir: str, debug: bool
) -> bool:
    """
    執行單一功能。回傳是否成功，讓呼叫端可以統計「跑了幾個、成功幾個」。

    這裡只攔截「執行這個功能」過程中的例外，刻意不攔截來源讀取階段的錯誤
    （那個在 main() 裡更早就攔截掉了），讓錯誤處理的責任範圍清楚分開：
    - 來源讀不到：屬於「這次執行整體就不該繼續」的錯誤
    - 單一功能跑壞：屬於「這個功能有問題，但其他功能應該還有機會跑」的錯誤
    """
    print(f"[執行中] {feature.display_name} ...")
    try:
        analysis_result = feature.analyzer.analyze(file_paths)
        analysis_result.source_path = source  # analyzer 只知道檔案清單，來源路徑由這裡補上

        output_path = os.path.join(output_dir, f"{feature.key}.docx")
        actual_path = feature.generator.generate(analysis_result, output_path)

        print(f"[完成] {feature.display_name} → {actual_path}")
        return True
    except Exception as e:
        if debug:
            raise
        print(f"[錯誤] 「{feature.display_name}」執行失敗：{e}")
        return False

def main() -> None:
    args = parse_args()
    feature_lookup = build_feature_lookup()

    # 檔案清單只需要載入一次，多個功能可以共用同一份分析來源。
    # 這裡的錯誤屬於「整次執行都無法繼續」的等級（路徑不存在、
    # 副檔名不支援、git repo 尚未實作...），所以攔截後直接結束程式。
    try:
        file_paths = get_source_loader(args.source).load()
    except (FileNotFoundError, ValueError, NotImplementedError) as e:
        if args.debug:
            raise
        print(f"[錯誤] {e}")
        sys.exit(1)

    if not file_paths:
        print(f"[警告] 在 {args.source} 底下找不到任何支援的原始碼檔案")
        return

    # 確定來源沒問題、真的有東西要分析了，才建立輸出資料夾，
    # 避免執行失敗時在使用者的專案裡留下一個空的 output/ 資料夾
    os.makedirs(args.output_dir, exist_ok=True)

    if args.doc:
        features_to_run = [feature_lookup[args.doc]]
    else:
        features_to_run = REGISTERED_FEATURES

    success_count = 0
    for feature in features_to_run:
        if run_feature(feature, file_paths, args.source, args.output_dir, args.debug):
            success_count += 1

    print(f"\n完成 {success_count}/{len(features_to_run)} 個文件")
    if success_count < len(features_to_run):
        sys.exit(1)  # 讓外部腳本（例如CI）能偵測到「有功能失敗」



if __name__ == "__main__":
    main()
