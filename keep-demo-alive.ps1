# Держит демо живым без присмотра.
#
# Бесплатный туннель localhost.run работает без аккаунта, но живёт около получаса
# и при переподключении получает новый адрес. Руками чинить это каждый раз
# бессмысленно, поэтому скрипт делает три вещи по кругу:
#   1. следит, что туннель поднят, и перезапускает его при обрыве;
#   2. замечает смену адреса;
#   3. прописывает новый адрес в сайт и деплоит его (update-site-url.ps1).
#
# Запуск:  .\keep-demo-alive.ps1
# Остановка: Ctrl+C в этом окне.
#
# Требуется, чтобы сервис уже слушал :8000 — поднимите его через .\start-demo.ps1
# или оставьте запущенным uvicorn.

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$logPath = Join-Path $env:TEMP 'notarybot-tunnel.log'
$current = ''

function Get-TunnelUrl {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return '' }
    $match = Select-String -Path $Path -Pattern 'https://[a-z0-9]+\.lhr\.life' -AllMatches |
        ForEach-Object { $_.Matches } | Select-Object -Last 1
    if ($match) { return $match.Value }
    return ''
}

Write-Host "Сторож демо запущен. Ctrl+C — остановить." -ForegroundColor Green

while ($true) {
    # Сервис на месте?
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:8000/healthz' -UseBasicParsing -TimeoutSec 10 | Out-Null
    } catch {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  сервис на :8000 не отвечает — жду" -ForegroundColor Yellow
        Start-Sleep -Seconds 20
        continue
    }

    # Туннель на месте?
    $alive = Get-Process ssh -ErrorAction SilentlyContinue
    if (-not $alive) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  поднимаю туннель..." -ForegroundColor Cyan
        Remove-Item $logPath -ErrorAction SilentlyContinue
        Start-Process -FilePath 'ssh' -WindowStyle Hidden `
            -RedirectStandardOutput $logPath `
            -ArgumentList @(
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'ServerAliveInterval=20',
                '-R', '80:127.0.0.1:8000',
                'nokey@localhost.run'
            )
        Start-Sleep -Seconds 15
    }

    $url = Get-TunnelUrl -Path $logPath

    if ($url -and $url -ne $current) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  новый адрес: $url" -ForegroundColor Cyan
        try {
            & "$root\update-site-url.ps1" $url
            $current = $url
        } catch {
            Write-Host "   не удалось обновить сайт: $_" -ForegroundColor Red
        }
    }

    Start-Sleep -Seconds 30
}
