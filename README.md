# PulseWatch

Сервис мониторинга доступности сайтов с уведомлениями через Telegram и webhook.

## Технологический стек

- **Backend**: FastAPI 0.115.5 + Python 3.12+
- **БД**: PostgreSQL 16 + SQLAlchemy 2.0 + asyncpg (в тестах — SQLite)
- **Кэш/блокировки**: Redis 7
- **Воркер**: asyncio-based scheduler
- **Тестирование**: pytest + pytest-asyncio
- **Линтинг**: ruff, mypy (strict)

## Быстрый старт

### Docker

```bash
cp .env.example .env   # задайте SECRET_KEY
docker compose up --build
```

Поднимутся Postgres, Redis, применение миграций, API на `http://localhost:8000`
и воркер проверок. Документация — `http://localhost:8000/docs`.

### Локально

1. Установка зависимостей:
```bash
python -m venv .venv
.venv/Scripts/activate        # Windows: .venv\Scripts\activate; Unix: source .venv/bin/activate
pip install -e ".[dev]"
```

2. Создание БД:
```bash
alembic upgrade head
```

3. Запуск API:
```bash
uvicorn app.main:app --reload
```

4. Запуск воркера (в отдельном терминале):
```bash
python -m app.worker.main
```

5. Запуск тестов:
```bash
pytest
```

## Порядок работы

Воркер раз в секунду опрашивает активные мониторы и запускает проверки, у которых
подошёл `interval_seconds`. Каждая проверка — один HTTP-запрос (`GET`, `HEAD` или
`OPTIONS`) с `timeout_seconds`; результат сохраняется в `checks`. После
`INCIDENT_THRESHOLD` (по умолчанию 3) подряд неудачных проверок открывается
инцидент и отправляется уведомление; первая успешная проверка его закрывает.
Успехом считается ответ с кодом, равным `expected_status` (по умолчанию 200).
Редиректы не отслеживаются. История проверок чистится через
`HISTORY_RETENTION_DAYS` (по умолчанию 30 дней).

Перед каждым запросом URL проверяется на SSRF: блокируются loopback, link-local
(включая облачные metadata-эндпоинты), RFC 1918, CGNAT, unique-local IPv6 и
служебные адреса. Диапазон `198.18.0.0/15` намеренно **не** блокируется: через
него Docker Desktop, VPN и прокси перехватывают DNS, и блокировка сделала бы
недостижимыми обычные публичные сайты. Если в вашей сети этот диапазон ведёт
во внутреннюю инфраструктуру, добавьте его в `SSRF_BLOCKED_NETWORKS`
(список CIDR через запятую).

## API

Все прикладные маршруты доступны под `/api` и требуют
`Authorization: Bearer <access_token>`, кроме `/api/auth/register` и `/api/auth/login`.

### Auth

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/auth/register` | Регистрация (`username`, `email`, `password` ≥ 8 символов) |
| POST | `/api/auth/login` | Логин по username или email → `access_token` + `refresh_token` |
| POST | `/api/auth/refresh` | Обмен refresh-токена на новую пару (ротация) |
| GET | `/api/auth/me` | Текущий пользователь |

Access-токен живёт `ACCESS_TOKEN_EXPIRE_MINUTES`, refresh — `REFRESH_TOKEN_EXPIRE_DAYS`.
Токены типизированы: refresh нельзя использовать для доступа к API и наоборот.

### Мониторы

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/monitors` | Создать монитор |
| GET | `/api/monitors?limit=&offset=` | Список с пагинацией |
| GET | `/api/monitors/{id}` | Один монитор |
| PATCH | `/api/monitors/{id}` | Частичное обновление |
| DELETE | `/api/monitors/{id}` | Удалить вместе с историей |
| GET | `/api/monitors/{id}/status` | Состояние и статистика за 24 часа |
| GET | `/api/monitors/{id}/checks` | История проверок монитора |

Ограничения: `url` — только `http(s)`, `method` — `GET`/`HEAD`/`OPTIONS`,
`interval_seconds` — 30..86400, `timeout_seconds` — 1..120,
`expected_status` — 100..599. Чужие мониторы возвращают 404.

### История

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/checks` | Проверки по всем мониторам пользователя (`monitor_id`, `since`, `success`) |
| GET | `/api/incidents` | Инциденты (`monitor_id`, `status=open\|closed`) |

Ответы списков имеют вид `{items, total, limit, offset}`. Ошибки — единый формат
`{detail, code}`; ошибки валидации дополнительно содержат `errors`.

### Служебные

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/health/ready` | Readiness с проверкой БД |

## Структура проекта

```
app/
  ├── api/           — FastAPI роутеры и зависимости
  ├── services/      — Бизнес-логика (auth, мониторы, инциденты, проверки)
  ├── repositories/  — Доступ к БД
  ├── models/        — SQLAlchemy модели
  ├── schemas/       — Pydantic схемы
  ├── core/          — Конфиг, безопасность, БД, SSRF, логирование
  ├── worker/        — Scheduler и проверки
  └── notifications/ — Telegram и webhook уведомления
tests/               — Тесты
migrations/          — Alembic миграции
```

## Переменные окружения

Скопируйте `.env.example` в `.env` и отредактируйте:

```bash
cp .env.example .env
```

Обязательные переменные:
- `DATABASE_URL` — строка подключения (async-драйвер, напр. `postgresql+asyncpg://…`)
- `SECRET_KEY` — ключ подписи JWT
- `REDIS_URL` — Redis (блокировки между воркерами; без Redis воркер работает без блокировок)

Опционально: `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` включают Telegram-уведомления;
`webhook_url` в мониторе включает уведомления на собственный эндпоинт.

## Лицензия

MIT
