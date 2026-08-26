# Локальная база для разработки — без прав администратора и без службы.
#
# На сервере PostgreSQL стоит нормальным пакетом и работает службой. Здесь
# сознательно иначе: установщик EDB требует администратора, а домашней машине
# ради разработки этого давать незачем. Сборка «только двоичные файлы»
# распаковывается в обычную папку и работает от учётной записи пользователя.
#
#   .\dev-db.ps1 start   — поднять базу (при первом запуске заводит кластер)
#   .\dev-db.ps1 stop    — остановить
#   .\dev-db.ps1 status  — жива ли

param([ValidateSet("start", "stop", "status")] [string]$Action = "start")

$ErrorActionPreference = "Stop"

$PgHome = "C:\claude\pgsql"
$PgData = "C:\claude\pgdata"
$Port = 5432
$SuperPassword = "notarybot_dev"
$AppUser = "notary"
$AppPassword = "notarybot_dev"  # ровно то, что ждёт tests/conftest.py по умолчанию

$initdb = Join-Path $PgHome "bin\initdb.exe"
$pgctl = Join-Path $PgHome "bin\pg_ctl.exe"
$psql = Join-Path $PgHome "bin\psql.exe"

if (-not (Test-Path $pgctl)) {
    throw "Не найден $pgctl — распакуйте сборку PostgreSQL в $PgHome"
}

function Invoke-Psql([string]$Database, [string]$Sql) {
    $env:PGPASSWORD = $SuperPassword
    try { & $psql -h 127.0.0.1 -p $Port -U postgres -d $Database -v ON_ERROR_STOP=1 -tAc $Sql }
    finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
}

switch ($Action) {
    "status" {
        & $pgctl -D $PgData status
        return
    }
    "stop" {
        & $pgctl -D $PgData -m fast stop
        return
    }
}

if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
    Write-Host "Завожу кластер в $PgData"

    # Пароль передаётся файлом, а не аргументом: аргументы видны в списке
    # процессов любому пользователю машины.
    $pwfile = Join-Path $env:TEMP "pgpw.txt"
    Set-Content -Path $pwfile -Value $SuperPassword -Encoding ascii -NoNewline
    try {
        & $initdb -D $PgData -U postgres --auth-host=scram-sha-256 --auth-local=trust `
            --pwfile=$pwfile --encoding=UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) { throw "initdb вернул $LASTEXITCODE" }
    }
    finally { Remove-Item $pwfile -Force -ErrorAction SilentlyContinue }

    # Слушаем только петлю: база с боевой схемой не должна быть видна из сети,
    # даже домашней.
    Add-Content -Path (Join-Path $PgData "postgresql.conf") -Value @"

# --- локальная разработка ---
listen_addresses = '127.0.0.1'
port = $Port
"@
}

$running = (& $pgctl -D $PgData status) 2>$null
if ($LASTEXITCODE -ne 0) {
    & $pgctl -D $PgData -l (Join-Path $PgData "server.log") -w start
    if ($LASTEXITCODE -ne 0) { throw "не удалось запустить PostgreSQL, смотрите $PgData\server.log" }
}

# Роль и базы заводятся при каждом запуске, но только если их ещё нет:
# скрипт должен переживать повторный вызов, иначе им перестанут пользоваться.
$hasRole = Invoke-Psql "postgres" "SELECT 1 FROM pg_roles WHERE rolname = '$AppUser'"
if (-not $hasRole) {
    Invoke-Psql "postgres" "CREATE ROLE $AppUser LOGIN PASSWORD '$AppPassword'" | Out-Null
    Write-Host "Заведена роль $AppUser"
}

foreach ($db in @("notarybot", "notarybot_test")) {
    $hasDb = Invoke-Psql "postgres" "SELECT 1 FROM pg_database WHERE datname = '$db'"
    if (-not $hasDb) {
        Invoke-Psql "postgres" "CREATE DATABASE $db OWNER $AppUser" | Out-Null
        Write-Host "Заведена база $db"
    }
}

Write-Host "База готова: 127.0.0.1:$Port, роль $AppUser, базы notarybot и notarybot_test"
