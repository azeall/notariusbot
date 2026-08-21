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

---

# Боевой сервер

Развёрнут 2026-08-21. Ubuntu 24.04, Timeweb, Москва.

| | |
|---|---|
| IP | 201.34.133.70 |
| Адрес | https://201.34.133.70.sslip.io |
| Доступ | `ssh -i ~/.ssh/notarybot_vps root@201.34.133.70` |
| Код | `/opt/notarybot` |
| Документы | `/var/lib/notarybot/storage` |
| Выгрузки | `/var/backups/notarybot` |

Вход по паролю отключён, работает только ключ. Фаервол пропускает 22, 80, 443.
База слушает localhost и снаружи не видна. Сервис работает от пользователя
`notarybot` без прав root.

## Службы

```bash
systemctl status notarybot-web    # веб
systemctl status notarybot-tg     # Telegram-бот
systemctl list-timers | grep notarybot
```

Таймеры: напоминания каждые 20 минут, чистка документов по сроку раз в сутки,
выгрузка базы и документов в 03:30.

## Обновление после правок в репозитории

```bash
ssh -i ~/.ssh/notarybot_vps root@201.34.133.70 '
  cd /opt/notarybot && git fetch -q origin && git reset -q --hard origin/main &&
  .venv/bin/pip install -q -r requirements.txt &&
  sudo -u notarybot .venv/bin/python -m alembic upgrade head &&
  systemctl restart notarybot-web notarybot-tg'
```

## Секреты

Лежат в `/opt/notarybot/.env`, права 600, владелец `notarybot`. В репозиторий
не попадают. Пароль базы продублирован в `/root/.notarybot-dbpass`, пароль
кабинета — в `/root/.notarybot-adminpass`.

## Про адрес

`201.34.133.70.sslip.io` — это IP, обёрнутый в имя, чтобы получить настоящий
сертификат Let's Encrypt без покупки домена. Когда появится свой домен,
достаточно направить его A-записью на этот IP и выпустить сертификат заново:

```bash
certbot --nginx -d ваш-домен.ru --redirect
```
