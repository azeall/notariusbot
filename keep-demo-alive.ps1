# Держит демо живым без присмотра.
#
# Бесплатный туннель localhost.run работает без аккаунта, но живёт около получаса
# и при переподключении получает новый адрес. Скрипт делает по кругу:
#   1. проверяет, что публичный адрес реально отвечает;
#   2. если нет — поднимает туннель заново (убив прежние, чтобы их не стало два);
#   3. прописывает новый адрес в сайт и деплоит его (update-site-url.ps1).
#
# Проверяется именно ответ по HTTP, а не наличие процесса ssh: процесс может
# висеть при мёртвом туннеле, и тогда сторож молча считает, что всё хорошо.
#
# Запуск:  .\keep-demo-alive.ps1      Остановка: Ctrl+C
# Требуется сервис на :8000 — поднимите .\start-demo.ps1 или uvicorn.

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$logPath = Join-Path $env:TEMP 'notarybot-tunnel.log'
$published = ''

function Test-Url {
    param([string]$Url, [int]$Timeout = 15)
    if (-not $Url) { return $false }
    try {
        $r = Invoke-WebRequest -Uri "$Url/healthz" -UseBasicParsing -TimeoutSec $Timeout
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-TunnelUrl {
    if (-not (Test-Path $logPath)) { return '' }
    $m = Select-String -Path $logPath -Pattern 'https://[a-z0-9]+\.lhr\.life' -AllMatches |
        ForEach-Object { $_.Matches } | Select-Object -Last 1
    if ($m) { return $m.Value }
    return ''
}

function Start-Tunnel {
    # Все прежние ssh валим: два туннеля одновременно — это две разных ссылки,
    # и сайт неизбежно окажется прописан на ту, что уже умерла.
    Get-Process ssh -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
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

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 3
        $u = Get-TunnelUrl
        if ($u -and (Test-Url $u)) { return $u }
    }
    return ''
}

Write-Host "Сторож демо запущен. Ctrl+C — остановить." -ForegroundColor Green

while ($true) {
    # Сервис на месте?
    $localOk = $false
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:8000/healthz' -UseBasicParsing -TimeoutSec 10 | Out-Null
        $localOk = $true
    } catch { }

    if (-not $localOk) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  сервис на :8000 не отвечает — жду" -ForegroundColor Yellow
        Start-Sleep -Seconds 20
        continue
    }

    # Публичный адрес реально работает?
    $url = Get-TunnelUrl
    if (-not (Test-Url $url)) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  туннель не отвечает — поднимаю заново" -ForegroundColor Cyan
        $url = Start-Tunnel
        if (-not $url) {
            Write-Host "   поднять не удалось, повтор через полминуты" -ForegroundColor Red
            Start-Sleep -Seconds 30
            continue
        }
        Write-Host "   адрес: $url" -ForegroundColor Green
    }

    # Сайт указывает туда же?
    if ($url -ne $published) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  обновляю сайт на $url" -ForegroundColor Cyan
        try {
            & "$root\update-site-url.ps1" $url
            $published = $url
        } catch {
            Write-Host "   не удалось обновить сайт: $_" -ForegroundColor Red
        }
    }

    Start-Sleep -Seconds 30
}
