# explore/ —— 一次性探索区

试一个新引擎、新模型或新想法时用这里：**一个候选一个目录**，目录里自带 venv；结论写进文档，目录随时可以整个删掉。
实验本身不随公开仓库发布，公开的只有这份说明和两个环境脚本。

规矩：

1. **一个候选一个目录**，目录里自带 `.venv`：`uv venv explore\<名字>\.venv`。卸载就是删目录。
2. **先 `. .\explore\env.ps1`**（Git Bash 用 `source explore/env.sh`），再装任何东西。它把 HF / torch / easyocr / modelscope / uv
   的缓存全指到数据根下 explore 的 `_cache/` 目录，装的东西不落进用户目录。
3. **装之前拍快照，装完再拍一张，diff 一次**，确认没有留在 `explore/` 之外的残留：

   ```powershell
   python dev_tools\residue_scan.py snapshot --label before-<名字>
   # ... 安装 + 跑通 ...
   python dev_tools\residue_scan.py snapshot --label after-<名字>
   python dev_tools\residue_scan.py diff --before before-<名字> --after after-<名字>
   ```

4. **跑产物的那个 venv 不装工具**：转换 / 导出这类一次性工具另开目录，别装进产出观测的环境。

生产代码在 `src/flowocr/`，跑在仓库根的 `.venv` 里，不在这里。
