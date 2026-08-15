# Окружение разработки

Всё поставлено и проверено 2026-08-15. Платных сервисов не задействовано.

## Что установлено

| Компонент | Версия | Где |
|---|---|---|
| Python | 3.12.10 | `%LOCALAPPDATA%\Programs\Python\Python312` |
| PostgreSQL | 17.11 | `C:\Users\TRITON 700\pgsql` (портативная сборка) |
| venv проекта | — | `C:\claude\.venv` |

PostgreSQL стоит **не как служба Windows**: установщик EDB требует прав администратора,
а подтвердить UAC было некому. Вместо него распакованы официальные бинарники EDB —
работают идентично, но сервер нужно запускать вручную.

## База

- БД `notarybot`, владелец — роль `notary`, пароль `notarybot_dev`
- Кодировка UTF8, collation ICU `ru-RU` (корректная сортировка кириллицы)
- Таймзона Europe/Moscow
- Порт 5432

Пароли здесь заведомо простые: база слушает только `127.0.0.1` и содержит лишь
тестовые данные. Для продакшна секреты генерируются отдельно и хранятся вне репозитория.

## Команды

Запустить базу (нужно после каждой перезагрузки):

```powershell
.\pg.ps1 start
```

Проверить, остановить, зайти в консоль базы:

```powershell
.\pg.ps1 status
.\pg.ps1 stop
.\pg.ps1 psql
```

Активировать окружение Python:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Проверка, что всё живо

```powershell
.\.venv\Scripts\python.exe -c "import asyncio,sqlalchemy.ext.asyncio as a,sqlalchemy as s; e=a.create_async_engine('postgresql+asyncpg://notary:notarybot_dev@127.0.0.1:5432/notarybot'); asyncio.run(e.connect().__aenter__()); print('OK')"
```

## Осталось сделать вручную

- Получить токен тестового бота у `@BotFather` в Telegram и вписать в `.env`
  (`TELEGRAM_BOT_TOKEN`). Понадобится на этапе Telegram-адаптера.
- Создать приватный репозиторий на GitHub (аккаунт `azeall` уже подключён).
