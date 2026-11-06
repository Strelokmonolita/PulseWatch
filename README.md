# PulseWatch

Сервис мониторинга доступности сайтов с поддержкой уведомлений через Telegram и webhook.

## Технологический стек

- **Backend**: FastAPI 0.115.5 + Python 3.12+
- **БД**: PostgreSQL 16 + SQLAlchemy 2.0 + asyncpg
- **Кэш/блокировки**: Redis 7
- **Воркер**: asyncio-based scheduler
- **Тестирование**: pytest + pytest-asyncio
- **Линтинг**: ruff, mypy (strict)

## Быстрый старт

1. Установка зависимостей:
```bash
pip install -e ".[dev]"
```

2. Создание БД:
```bash
alembic upgrade head
```

3. Запуск приложения:
```bash
uvicorn app.main:app --reload
```

4. Запуск воркера:
```bash
python -m app.worker.main
```

5. Запуск тестов:
```bash
pytest
```

## Структура проекта

```
app/
  ├── api/           — FastAPI роутеры
  ├── services/      — Бизнес-логика
  ├── repositories/  — Доступ к БД
  ├── models/        — SQLAlchemy модели
  ├── schemas/       — Pydantic схемы
  ├── core/          — Конфиг, безопасность, логирование
  ├── worker/        — Scheduler и проверки
  └── notifications/ — Уведомления (Telegram, webhook)
tests/               — Тесты
migrations/          — Alembic миграции
```

## Переменные окружения

Скопируйте `.env.example` в `.env` и отредактируйте:

```bash
cp .env.example .env
```

Обязательные переменные:
- `DATABASE_URL` — PostgreSQL connection string
- `SECRET_KEY` — JWT secret key
- `REDIS_URL` — Redis connection string (default: redis://localhost)

## Лицензия

MIT
