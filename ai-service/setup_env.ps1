# ai-service 전용 파이썬 가상환경 자동 설정.
#
# 왜 필요한가: numpy/torch/librosa 같은 패키지는 컴파일된 바이너리(wheel)를
# 받아쓰는데, 시스템 기본 파이썬이 너무 최신(예: 3.14)이면 아직 정식 윈도우
# 휠이 없어서 불안정한 대체 빌드가 깔려 크래시 날 수 있음. 검증된 3.12(또는
# 3.11/3.13/3.10)로 이 프로젝트 전용 가상환경(.venv)을 만들어서 우회한다.
#
# 여러 번 실행해도 안전함(.venv 있으면 재사용, requirements만 다시 설치).
#
# 사용법 (ai-service 폴더에서):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # 최초 1회, 정책 막혀있으면
#   .\setup_env.ps1

$ErrorActionPreference = "Stop"

$pyLauncherExists = $null -ne (Get-Command py -ErrorAction SilentlyContinue)
if (-not $pyLauncherExists) {
    Write-Host "'py' 런처를 못 찾았음(보통 python.org 설치 시 같이 깔림)." -ForegroundColor Red
    Write-Host "아래 명령으로 3.12 설치하고(런처 포함됨) 다시 실행해줘:"
    Write-Host "  winget install Python.Python.3.12"
    exit 1
}

$preferred = @("3.12", "3.11", "3.13", "3.10")
$pyVersion = $null
foreach ($v in $preferred) {
    try {
        & py "-$v" --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $pyVersion = $v
            break
        }
    } catch {
        continue
    }
}

if (-not $pyVersion) {
    Write-Host "3.10~3.13 중 설치된 파이썬을 못 찾았음." -ForegroundColor Red
    Write-Host "아래 명령으로 3.12 설치하고 다시 실행해줘:"
    Write-Host "  winget install Python.Python.3.12"
    exit 1
}

Write-Host "사용할 파이썬: $pyVersion" -ForegroundColor Cyan

if (-not (Test-Path ".venv")) {
    Write-Host "가상환경 생성 중 (.venv)..."
    & py "-$pyVersion" -m venv .venv
} else {
    Write-Host ".venv 이미 있음 — 재사용."
}

$venvPython = ".\.venv\Scripts\python.exe"

Write-Host "pip 업그레이드..."
& $venvPython -m pip install --upgrade pip -q

Write-Host "requirements.txt 설치 중... (torch/librosa 등 포함이라 시간 좀 걸림)"
& $venvPython -m pip install -r requirements.txt

Write-Host ""
Write-Host "완료!" -ForegroundColor Green
Write-Host "앞으로는 활성화 없이 이렇게 실행하면 돼:"
Write-Host "  .\run.ps1 scripts\test_delivery_metrics.py ../test-audio/test01.m4a"
Write-Host "(또는 직접: .\.venv\Scripts\python.exe scripts\...)"
