# .venv의 파이썬으로 스크립트를 실행하는 짧은 래퍼 — 매번 활성화(Activate.ps1)
# 안 해도 됨. setup_env.ps1을 먼저 한 번 돌려서 .venv가 있어야 동작함.
#
# 사용법 (ai-service 폴더에서):
#   .\run.ps1 scripts\test_delivery_metrics.py ../test-audio/test01.m4a
#   .\run.ps1 -m pytest tests/ -q
#   .\run.ps1 scripts\test_llm_gateway.py

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host ".venv가 없음 — 먼저 .\setup_env.ps1 을 실행해줘." -ForegroundColor Red
    exit 1
}

& $venvPython @args
