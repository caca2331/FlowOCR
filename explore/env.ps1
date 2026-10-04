# 把所有会往用户目录里写模型/权重的库，统统重定向到数据根下 explore 的 `_cache/` 目录。
# 用法（每个新开的 PowerShell 都要先跑一次）：
#     . .\explore\env.ps1
# 目标：删掉 explore/ 就等于没装过。做不到的库要写清它在目录外留下了什么。

$repo  = Split-Path -Parent $PSScriptRoot
# 缓存落**数据根**（flowocr.paths 的 data_root()）：槽里 dot-source 这份也落回主 checkout，
# 不会每开一个槽就把模型重下一遍（2026-09-21，旧槽里实测多出一份 133 MB 的 PP-OCRv6）
# PYTHONPATH 只在算数据根这一步临时设、算完还原：dot-source 进来的是用户自己的 shell，
# 留着它会让之后在这个 shell 里起的每个解释器（包括 explore 的 venv）都先看到这份 src/
$oldPyPath = $env:PYTHONPATH
$env:PYTHONPATH = (Join-Path $repo "src")
try { $data = (& python -m flowocr.paths) } finally { $env:PYTHONPATH = $oldPyPath }
if (-not $data) { throw "env.ps1：算不出数据根（$repo\src 里没有 flowocr？要 dot-source 代码 checkout 里的这份）" }
$cache = Join-Path $data.Trim() "explore\_cache"
# 游戏文本包（flowocr.paths.gametext_root）：数据根没有 gametext\ 时，开发机用 game-text-data 那边 bundle 出的 out\
# （生产代码不认这个兄弟目录布局，只在开发环境里设）
$gtdOut = Join-Path (Split-Path -Parent $data.Trim()) "game-text-data\out"
if (-not $env:FLOWOCR_GAMETEXT -and -not (Test-Path (Join-Path $data.Trim() "gametext")) -and (Test-Path $gtdOut)) {
    $env:FLOWOCR_GAMETEXT = $gtdOut
}

New-Item -ItemType Directory -Force -Path $cache | Out-Null

# HuggingFace（transformers / hub / datasets 全走 HF_HOME）
$env:HF_HOME                 = Join-Path $cache "hf"
# PyTorch hub 权重
$env:TORCH_HOME              = Join-Path $cache "torch"
# EasyOCR
$env:EASYOCR_MODULE_PATH     = Join-Path $cache "easyocr"
# ModelScope（国内镜像常用）
$env:MODELSCOPE_CACHE        = Join-Path $cache "modelscope"
# 通用兜底：不少库按 XDG 约定找缓存目录
$env:XDG_CACHE_HOME          = Join-Path $cache "xdg"
# uv 的包缓存也隔离，免得和其它项目互相污染
$env:UV_CACHE_DIR            = Join-Path $cache "uv"

foreach ($k in "HF_HOME","TORCH_HOME","EASYOCR_MODULE_PATH","MODELSCOPE_CACHE","XDG_CACHE_HOME","UV_CACHE_DIR") {
    New-Item -ItemType Directory -Force -Path ([Environment]::GetEnvironmentVariable($k)) | Out-Null
    Write-Host ("  {0,-24} {1}" -f $k, [Environment]::GetEnvironmentVariable($k))
}
Write-Host "`n注意：PaddleOCR 2.x 仍会写 ~/.paddleocr，没有环境变量可改——用完手动删。" -ForegroundColor Yellow
