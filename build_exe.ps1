param(
    [string]$Python = "",
    [switch]$WithCli
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

if (-not $Python) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
Write-Host "Building Moqiao with $Python" -ForegroundColor Cyan

& $Python -c "import PyInstaller, fitz, webview, docx, pptx, openpyxl, bs4"
if ($LASTEXITCODE -ne 0) {
    throw 'Missing dependencies. Run: python -m pip install -e ".[dev]"'
}

$distPath = Join-Path $ProjectDir "dist"
$buildPath = Join-Path $ProjectDir "build"
if (Test-Path -LiteralPath $distPath) { Remove-Item -LiteralPath $distPath -Recurse -Force }
if (Test-Path -LiteralPath $buildPath) { Remove-Item -LiteralPath $buildPath -Recurse -Force }

$common = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--paths", "src",
    "--icon", "assets\moqiao.ico",
    "--add-data", "src\pdf2mdx\frontend;pdf2mdx\frontend",
    "--hidden-import", "docx",
    "--hidden-import", "pptx",
    "--hidden-import", "openpyxl",
    "--hidden-import", "bs4",
    "--exclude-module", "matplotlib",
    "--exclude-module", "numpy",
    "--exclude-module", "pandas",
    "--exclude-module", "IPython",
    "--exclude-module", "pytest",
    "--exclude-module", "cryptography"
)

Write-Host "Building desktop EXE..." -ForegroundColor Green
$guiArgs = @(
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "webview.platforms.winforms",
    "--exclude-module", "webview.platforms.android",
    "--exclude-module", "webview.platforms.cocoa",
    "--exclude-module", "webview.platforms.gtk",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PyQt6",
    "--exclude-module", "PySide2",
    "--exclude-module", "PySide6"
)
& $Python -m PyInstaller @common @guiArgs --windowed --name "Moqiao" "moqiao_gui.py"
if ($LASTEXITCODE -ne 0) { throw "Desktop EXE build failed." }

if ($WithCli) {
    Write-Host "Building optional CLI EXE..." -ForegroundColor Green
    & $Python -m PyInstaller @common --console --name "moqiao-cli" "moqiao_cli.py"
    if ($LASTEXITCODE -ne 0) { throw "CLI EXE build failed." }
}

Write-Host "Build complete:" -ForegroundColor Cyan
Get-ChildItem -LiteralPath $distPath -File | Select-Object Name, Length
