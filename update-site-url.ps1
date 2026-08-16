# Прописывает текущий адрес туннеля в сайт нотариуса и деплоит его.
#
# Бесплатный туннель без аккаунта меняет адрес при каждом переподключении,
# поэтому после перезапуска демо сайт нужно перенастроить. Скрипт делает это
# за один шаг: правит lib/notarybot.ts в ветке template и пушит — Vercel
# собирает сам (Ignored Build Step в проекте пропускает все ветки кроме template).
#
# Использование:  .\update-site-url.ps1 https://xxxxx.lhr.life

param(
    [Parameter(Mandatory = $true)]
    [string]$Url
)

$ErrorActionPreference = 'Stop'

# Каталог самого проекта: сайт сервиса лежит рядом со скриптом.
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

$Url = $Url.TrimEnd('/')
if ($Url -notmatch '^https://') {
    Write-Host "Адрес должен начинаться с https://" -ForegroundColor Red
    exit 1
}

$work = Join-Path $env:TEMP 'notarius-site'

if (Test-Path (Join-Path $work '.git')) {
    Write-Host "Обновляю копию сайта..."
    git -C $work fetch --quiet origin
    git -C $work checkout --quiet -B template origin/template
} else {
    Write-Host "Клонирую сайт..."
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    git clone --quiet --branch template https://github.com/azeall/notarius.git $work
    git -C $work config user.name 'azeall'
    git -C $work config user.email 'negay2020@gmail.com'
}

# Читаем и пишем строго UTF-8 без BOM. Get-Content/Set-Content в PowerShell 5.1
# читают файл как ANSI и сохраняют как UTF-8, из-за чего кириллица в комментариях
# перекодировалась повторно при каждом запуске и превращалась в кашу.
$utf8 = New-Object System.Text.UTF8Encoding($false)

$configPath = Join-Path $work 'lib\notarybot.ts'
$config = [System.IO.File]::ReadAllText($configPath, $utf8)

$pattern = "const DEMO_FALLBACK_URL = '[^']*'"
$replacement = "const DEMO_FALLBACK_URL = '$Url'"

if ($config -notmatch $pattern) {
    Write-Host "Не нашёл строку DEMO_FALLBACK_URL в lib/notarybot.ts" -ForegroundColor Red
    exit 1
}

$updated = $config -replace $pattern, $replacement

if ($updated -eq $config) {
    Write-Host "Сайт нотариуса уже знает этот адрес." -ForegroundColor Yellow
} else {
    [System.IO.File]::WriteAllText($configPath, $updated, $utf8)
    git -C $work add lib/notarybot.ts
    git -C $work commit --quiet -m "Демо: адрес сервиса заявок $Url"
    git -C $work push --quiet origin template
    Write-Host "Сайт нотариуса обновлён." -ForegroundColor Green
}

# --- сайт сервиса ---
# Он в этом же репозитории, но кабинет живёт на сервисе, поэтому ссылки
# «Войти» должны быть абсолютными — иначе браузер ищет /platform/login
# на самом сайте и показывает 404.

$vendorConfig = Join-Path $root 'vendor-site\config.js'
$vendor = [System.IO.File]::ReadAllText($vendorConfig, $utf8)
$vendorPattern = 'window\.SERVICE_URL = "[^"]*";'
$vendorReplacement = "window.SERVICE_URL = `"$Url`";"

if ($vendor -notmatch $vendorPattern) {
    Write-Host "Не нашёл SERVICE_URL в vendor-site/config.js" -ForegroundColor Red
} else {
    $vendorUpdated = $vendor -replace $vendorPattern, $vendorReplacement
    if ($vendorUpdated -eq $vendor) {
        Write-Host "Сайт сервиса уже знает этот адрес." -ForegroundColor Yellow
    } else {
        [System.IO.File]::WriteAllText($vendorConfig, $vendorUpdated, $utf8)
        git -C $root add vendor-site/config.js
        git -C $root commit --quiet -m "Сайт сервиса: адрес кабинета $Url"
        git -C $root push --quiet origin main
        Write-Host "Сайт сервиса обновлён." -ForegroundColor Green
    }
}

Write-Host "`nVercel собирает оба сайта — обычно занимает минуту." -ForegroundColor Green
Write-Host "  Сайт нотариуса: https://notarius-wn4h.vercel.app"
Write-Host "  Сайт сервиса:   https://notariusbot-azealls-projects.vercel.app"
Write-Host "  Виджет:         $Url/widget/demo"
Write-Host "  Кабинет:        $Url/platform/login"
