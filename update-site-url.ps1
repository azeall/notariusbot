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

$configPath = Join-Path $work 'lib\notarybot.ts'
$config = Get-Content $configPath -Raw

$pattern = "const DEMO_FALLBACK_URL = '[^']*'"
$replacement = "const DEMO_FALLBACK_URL = '$Url'"

if ($config -notmatch $pattern) {
    Write-Host "Не нашёл строку DEMO_FALLBACK_URL в lib/notarybot.ts" -ForegroundColor Red
    exit 1
}

$updated = $config -replace $pattern, $replacement
if ($updated -eq $config) {
    Write-Host "Адрес уже актуален, деплоить нечего." -ForegroundColor Yellow
    exit 0
}

Set-Content -Path $configPath -Value $updated -Encoding utf8 -NoNewline

git -C $work add lib/notarybot.ts
git -C $work commit --quiet -m "Демо: адрес сервиса заявок $Url"
git -C $work push --quiet origin template

Write-Host "`nГотово. Vercel собирает сайт — обычно занимает минуту." -ForegroundColor Green
Write-Host "  Сайт:   https://notarius-wn4h.vercel.app"
Write-Host "  Виджет: $Url/widget/demo"
