# PanSou 启动脚本（Windows 兼容入口）
#
# 真正的启动逻辑已迁移到 Python（core/pansou_launcher.py），实现跨平台统一：
# 自动识别系统与 CPU 架构、从 pansou/dist/ 选对应二进制、注入插件列表、
# 后台运行并校验插件是否真的加载成功。
#
# 本脚本保留是为了兼容原有的使用习惯（powershell -File start_pansou.ps1）。
# 所有平台（含 Windows / Linux / macOS）推荐直接用：
#     python main.py pansou start
#     python main.py pansou status
#     python main.py pansou stop
#
# 加 -Foreground 参数可前台运行（日志直接打在终端，Ctrl+C 退出）。

param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }

if ($Foreground) {
    Write-Host "PanSou 前台启动（Ctrl+C 退出）..." -ForegroundColor Cyan
    & $python main.py pansou start --foreground
} else {
    Write-Host "PanSou 后台启动中..." -ForegroundColor Cyan
    & $python main.py pansou start
    exit $LASTEXITCODE
}
