[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

try {
    $root = Split-Path -Parent $PSScriptRoot
    $environment = Join-Path $root '.venv'
    $helper = Join-Path $PSScriptRoot 'prepare_source.py'
    $arguments = @('-I', '-B', '-X', 'utf8', $helper)
    if (Test-Path -LiteralPath $environment) {
        $item = Get-Item -LiteralPath $environment -Force
        if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '.venv는 연결 경로가 아닌 실제 프로젝트 폴더여야 합니다.'
        }
        $python = Join-Path $environment 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw '기존 .venv가 불완전합니다. 덮어쓰기·재설치 없이 중단합니다.'
        }
    } else {
        $windowsApps = (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps') + '\'
        $command = Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { -not $_.Source.StartsWith($windowsApps, [StringComparison]::OrdinalIgnoreCase) } |
            Select-Object -First 1
        if ($null -ne $command) {
            $python = $command.Source
        } else {
            $command = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
            if ($null -eq $command) {
                throw 'Windows x64 Python3.11 이상을 먼저 설치하세요. 검증 기준은 Python3.14.8입니다. EXE 배포본에는 필요하지 않습니다.'
            }
            $python = $command.Source
            $arguments = @('-3') + $arguments
        }
    }
    & $python @arguments
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('소스 환경 준비 중단: ' + $_.Exception.Message)
    [Console]::Error.WriteLine('기존·부분 .venv를 자동 삭제·복구·재시도하지 않습니다.')
    exit 1
}
