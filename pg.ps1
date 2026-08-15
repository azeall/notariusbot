# Управление локальным PostgreSQL (портативная сборка, без службы Windows).
# Использование:  .\pg.ps1 start | stop | status | psql
param(
    [ValidateSet('start', 'stop', 'status', 'psql')]
    [string]$Action = 'status'
)

$PgHome = 'C:\Users\TRITON 700\pgsql'
$PgData = "$PgHome\data"
$PgLog = "$PgHome\server.log"

switch ($Action) {
    'start' {
        & "$PgHome\bin\pg_ctl.exe" -D $PgData -l $PgLog -o "-p 5432" start
    }
    'stop' {
        & "$PgHome\bin\pg_ctl.exe" -D $PgData -m fast stop
    }
    'status' {
        & "$PgHome\bin\pg_isready.exe" -p 5432
    }
    'psql' {
        $env:PGPASSWORD = 'notarybot_dev'
        & "$PgHome\bin\psql.exe" -U notary -d notarybot -h 127.0.0.1 -p 5432
    }
}
