$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Please install Python 3.12 x64 with Tk support.' }
}
& $pythonPath -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $pythonPath -m unittest -v test_engine test_simplified test_workflow test_soniox test_soniox_context test_split test_image_video test_sentence_context test_complete_sentences test_punctuation test_utterances test_phrases test_member_colors test_lyrics_response test_members test_wordbook
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $pythonPath gui_checks.py
if ($LASTEXITCODE -ne 0) { throw 'DPI layout checks failed.' }
$version = (& $pythonPath -c 'from app import VERSION; print(VERSION)').Trim()
$releasePath = Join-Path 'dist' $version
$appPath = Join-Path $releasePath 'AI-Sub-Maker'
& $pythonPath -m PyInstaller --noconfirm --distpath $releasePath --workpath (Join-Path 'build' $version) AI-Sub-Maker.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath README.md -Destination (Join-Path $appPath '使用说明.md') -Force
Copy-Item -LiteralPath 成员应援色.md -Destination (Join-Path $appPath '成员应援色.md') -Force
Copy-Item -LiteralPath requirements-lock.txt -Destination (Join-Path $appPath 'requirements-lock.txt') -Force
Copy-Item -LiteralPath start_soniox.bat -Destination (Join-Path $appPath 'start_soniox.bat') -Force
Copy-Item -LiteralPath (Join-Path $appPath 'AI-Sub-Maker.exe') -Destination (Join-Path $appPath 'Mp3ImageToMp4.exe') -Force
& $pythonPath collect_licenses.py (Join-Path $appPath 'THIRD_PARTY_NOTICES.txt')
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
$reportPath = Join-Path $PSScriptRoot ('test-output\frozen-smoke-' + $version + '.json')
$testProcess = Start-Process -FilePath (Join-Path $appPath 'AI-Sub-Maker.exe') -ArgumentList @('--smoke-test', ('"' + $reportPath + '"')) -WindowStyle Hidden -PassThru -Wait
if ($testProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) { throw 'Packaged smoke test failed.' }
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if (-not $report.ok) { throw 'Packaged smoke test failed. Read test-output/frozen-smoke.json.' }
& $pythonPath package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
Write-Host ('Ready: dist\AI-Sub-Maker-' + $version + '-Windows11-x64.zip')
