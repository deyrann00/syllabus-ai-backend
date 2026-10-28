# Syllabus AI — бэкенд

Бэкенд для прототипа `syllabus-ai-prototype` (Narxoz University). Он проверяет силлабусы по рубрике качества (9 принципов, 49 критериев) с помощью локальной LLM и ведёт академическое согласование: Draft → AI Checked → Head of Program Review → KOK Review → Approved. Кроме того, он поддерживает массовые сессии проверки, аналитику, приватную предпроверку и мастер создания силлабуса.

**Стек:** FastAPI · PostgreSQL (SQLAlchemy 2 + Alembic) · MongoDB (Beanie) · Celery + Redis · vLLM (OpenAI-совместимый API).

## Быстрый старт

```bash
cp .env.example .env              # LLM_PROVIDER=fake работает без GPU
docker compose up -d --build      # postgres, mongo, redis, migrate, api, worker, beat
docker compose exec api python -m app.cli seed   # данные прототипа + первые проверки
open http://localhost:8080/docs   # Swagger
uv run python scripts/demo_flow.py                # e2e: сессия → правки → v4 → Approved
```

Чтобы получить токен без Google, вызовите `POST /api/v1/auth/dev-login {"email": "provost@demo.narxoz.kz"}`. Этот эндпоинт работает только при `ENV != prod`.

Демо-пользователи:

| Пользователь | Роль |
|---|---|
| `provost@…` | Офис провоста |
| `kok@…` | КОК |
| `admin.digital@…` | Администратор School of Digital Technologies |
| `head.cs@…` | Руководитель программы Computer Science |
| `shalkar@…` | Ведущий курса AI |
| `lee@…` | Ведущий курса ML, преподаватель AI |
| `karimova@…` | Преподаватель |
| `sultanov@…` | Преподаватель |
| `abayeva@…` | Преподаватель |
| `sadykov@…` | Преподаватель |

У всех адресов домен `@demo.narxoz.kz`.

## Разработка без Docker для приложения

```bash
uv sync
docker compose up -d postgres mongo redis
uv run alembic upgrade head
uv run python -m app.cli seed
uv run uvicorn app.main:app --reload --port 8080
uv run celery -A app.workers.celery_app worker -Q ai,default -c 2   # или TASKS_EAGER=true в .env
uv run celery -A app.workers.celery_app beat
```

Проверки (тестам нужен запущенный Docker, testcontainers поднимает свои контейнеры):

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app seed
```

## Как устроено

```
app/
  api/        роутеры (/api/v1), по одному на экран прототипа
  services/   бизнес-логика: checks, approval, proposals, precheck, sessions, analytics, wizard, notifications
  ai/         конвейер: extraction → technical → criteria (LLM) → evidence; proposals, apply, render_docx, fake
  models/     PostgreSQL     documents/  MongoDB     schemas/  Pydantic DTO
  auth/       Google OIDC, JWT, RBAC     workers/  Celery + dispatch     storage/  файлы, Redis
rubric/       рубрика в YAML (версионируется)     seed/  демо-данные     migrations/  Alembic
```

**Где хранятся данные:**
- **PostgreSQL** — справочники, пользователи и роли, силлабусы, версии, запуски проверок, статусы критериев (`criterion_outcomes`, для аналитики), события согласования, сессии, уведомления.
- **MongoDB** — извлечённая структура документа, полные результаты ИИ (цитата, объяснение, рекомендация), предложенные правки, черновики мастера, история ассистента, трейсы LLM.
- **Файлы** — на локальном диске: `FILES_ROOT/{syllabus_id}/v{n}/{файл}`. Версии неизменяемы.

**Проверка силлабуса** выполняется в задаче Celery `check_run` в очереди `ai`:
1. Документ (.docx или .pdf) разбирается на разделы, LO, недели и задания с весами. Распознаются заголовки на EN, RU и KZ.
2. Детерминированные правила проверяют 3.2, 6.8 и 8.2 и составляют технический отчёт.
3. LLM вызывается один раз на принцип, ответ строго по JSON-схеме.
4. Критерии с внешним контекстом получают `Cannot verify`, если контекста нет. Это 3.4 (паспорт программы, поле `programs.passport_text`) и 7.2 (библиотечный каталог).
5. Цитату (`evidence`) сверяем с текстом документа. Если её там нет, статус понижается до `Cannot verify`: так отсекаются выдуманные цитаты.

**Права:**
- `provost` и `kok` видят весь университет, `school_admin` — свою школу, `program_head` — свою программу.
- Преподаватель видит курсы, которые ведёт или на которых преподаёт.
- Загрузку, улучшение и предпроверку может выполнять только ведущий курса.

**Согласование** описано в `app/services/approval.py`, таблица `TRANSITIONS`. При возврате на доработку комментарий обязателен. Новая версия возвращает силлабус в Draft, после проверки он переходит в AI Checked.

**Уведомления** создаются в приложении. Письма отправляет задача beat `send_pending_emails` по принципу outbox. При `EMAIL_BACKEND=console` письма пишутся в лог.

## Подключение vLLM

```bash
vllm serve Qwen/Qwen2.5-32B-Instruct --port 8000 --max-model-len 32768
```

В `.env` задайте:
```
LLM_PROVIDER=vllm
LLM_BASE_URL=http://<gpu-host>:8000/v1
LLM_MODEL=Qwen/Qwen2.5-32B-Instruct
LLM_MAX_CONCURRENCY=4
```

Нужна многоязычная модель, потому что силлабусы бывают на EN, RU и KZ. Параметр `-c` воркера (сейчас 2) и `LLM_MAX_CONCURRENCY` подберите под пропускную способность сервера.

## Рубрика

Рубрика лежит в `rubric/2026.1.yaml`. Чтобы изменить её, создайте новый файл с новым `code` и выполните `python -m app.cli import-rubric rubric/<code>.yaml`. Новая версия станет активной. Каждый запуск проверки хранит ссылку на свою версию рубрики, поэтому старые результаты не меняются.

## Отличия от прототипа

- Google Drive не подключается, файлы загружаются через API. Экран «Schools & Drive» опирается на логические папки школ (`/schools/{id}/folders`), сессия может проверять одну папку.
- Детерминированными сделаны 3.2, 6.8 и 8.2. В прототипе это были 8.2 и 9.2, но проверка 9.2 требует понимания смысла текста.
- `LLM_PROVIDER=fake` — эвристический оценщик. Он воспроизводит пример проверки из прототипа (у AI v3 проблемы 1.2, 3.5, 4.3, 6.1, 6.4, 9.2) и нужен для тестов и разработки без GPU.

## Известные ограничения

- `POST /runs/{id}/proposals` и ассистент мастера синхронно ждут ответа LLM. С большой моделью запрос может занимать десятки секунд.
- `apply` для PDF не может править исходник, поэтому собирает новый .docx из извлечённого текста.
- Сравнение версий выдаёт построчный diff абзацев, без форматирования.
