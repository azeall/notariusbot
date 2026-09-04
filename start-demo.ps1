# Поднимает демо целиком: база, сервис заявок, публичный туннель.
# Использование:  .\start-demo.ps1
#
# Туннель бесплатный и без аккаунта, поэтому адрес меняется при каждом запуске.
# Новый адрес скрипт печатает в конце — его нужно вписать в lib/notarybot.ts
# репозитория сайта (или в переменную NEXT_PUBLIC_NOTARYBOT_URL в Vercel).

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

Write-Host "1/3 База..." -ForegroundColor Cyan
& "$root\dev-db.ps1" start
& "$root\dev-db.ps1" status

Write-Host "`n2/3 Сервис заявок на :8000..." -ForegroundColor Cyan
Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList '-m', 'uvicorn', 'app.web.main:app', '--port', '8000' `
    -WorkingDirectory $root -WindowStyle Minimized
Start-Sleep -Seconds 4

try {
    $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/healthz' -UseBasicParsing -TimeoutSec 15
    Write-Host "   сервис отвечает: $($health.StatusCode)"
} catch {
    Write-Host "   сервис не поднялся — посмотрите окно uvicorn" -ForegroundColor Red
    exit 1
}

Write-Host "`n3/3 Публичный туннель..." -ForegroundColor Cyan
Write-Host "   Запускается в отдельном окне. Адрес вида https://xxxx.lhr.life"
Write-Host "   появится в нём через несколько секунд — скопируйте его.`n"

Start-Process -FilePath 'ssh' -ArgumentList @(
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'UserKnownHostsFile=/dev/null',
    '-o', 'ServerAliveInterval=30',
    '-R', '80:127.0.0.1:8000',
    'nokey@localhost.run'
)

Write-Host "Готово." -ForegroundColor Green
Write-Host "  Виджет локально:  http://127.0.0.1:8000/widget/demo"
Write-Host "  Панель:           http://127.0.0.1:8000/staff/demo/login"
Write-Host "  Вход:             owner@demo.ru / demo12345"
