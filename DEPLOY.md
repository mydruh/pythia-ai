# Деплой Pythia (PaaS: Railway + Vercel)

Топология: **бэкенд + Postgres + Telegram-бот на Railway**, **React Mini App на Vercel**.
Все домены — платформенные, с валидным HTTPS из коробки (нужен Telegram Mini App).

```
Telegram ──/start──▶ bot (Railway worker) ──┐
                                            │  открывает Mini App
Пользователь ──▶ Vercel (React static) ──/api──▶ backend (Railway) ──▶ Postgres (Railway)
                  https://pythia.vercel.app      https://…up.railway.app
```

> **Почему Railway, а не Render free:** торговый цикл (APScheduler) крутится внутри
> бэкенда и должен работать **постоянно**. Render free засыпает при простое → циклы
> пропускаются. Railway держит сервис всегда живым. Fly.io тоже подходит.
> **Регион:** выбирай **EU** (Polymarket геоблочит ряд юрисдикций; в paper-режиме
> ордеров нет, но Gamma/CLOB запросы лучше слать из ЕС — на будущее под live).

---

## 0. Подготовка

- Аккаунты: [Railway](https://railway.app), [Vercel](https://vercel.com), репозиторий на GitHub.
- Telegram-бот: в [@BotFather](https://t.me/BotFather) → `/newbot` → сохрани **токен**.
- Ключи: `GROQ_API_KEY` (обязателен — фильтр+фоллбэк), `XAI_API_KEY` (опц., боевой Grok).
- Сгенерируй общий секрет бот↔бэкенд:
  ```bash
  openssl rand -hex 32   # → это INTERNAL_API_TOKEN
  ```

---

## 1. Бэкенд + Postgres на Railway

1. **New Project → Deploy from GitHub repo** → выбери репозиторий.
2. В сервисе бэкенда: **Settings → Build**
   - `Root Directory` = `backend` (папка самодостаточна: Dockerfile + requirements.txt
     внутри; в контекст сборки попадёт только backend, без фронта/telegram).
3. **New → Database → Add PostgreSQL** (в том же проекте, регион EU).
4. **Variables** бэкенд-сервиса:
   | Переменная | Значение |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (ссылка на плагин; код сам приведёт к `+asyncpg`) |
   | `TELEGRAM_BOT_TOKEN` | токен из BotFather (нужен для проверки подписи initData) |
   | `INTERNAL_API_TOKEN` | секрет из `openssl rand` |
   | `ALLOW_UNVERIFIED_AUTH` | `false` |
   | `GROQ_API_KEY` | … |
   | `XAI_API_KEY` | … (опц.; пусто → фоллбэк на Groq-llama) |
   | `TRADING_MODE` | `paper` |
   | `CYCLE_INTERVAL_MINUTES` | `30` |
5. **Settings → Networking → Generate Domain** → получишь
   `https://pythia-backend-production.up.railway.app`. Запомни — это `VITE_API_URL`.
6. Деплой. Таблицы создадутся сами при старте (`init_db`, без миграций).
   Проверка: `GET https://…up.railway.app/health` → `{"status":"ok", …}`.

---

## 2. Telegram-бот (второй сервис на Railway)

1. В том же проекте: **New → GitHub Repo** (тот же репо) → второй сервис.
2. **Settings → Build:** `Root Directory` = `telegram` (там свой Dockerfile и requirements).
3. **Variables:**
   | Переменная | Значение |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | тот же токен |
   | `API_BASE_URL` | публичный URL бэкенда из шага 1.5 |
   | `WEBAPP_URL` | URL Vercel из шага 3 (впишешь после деплоя фронта) |
   | `INTERNAL_API_TOKEN` | тот же секрет, что у бэкенда |
4. Это **worker** (без порта, long-polling) — домен не генерируй.

---

## 3. Фронтенд на Vercel

1. **Add New → Project** → импортируй репозиторий.
2. **Root Directory** = `frontend` (Vercel подхватит `vercel.json` и Vite).
3. **Environment Variables:**
   | Переменная | Значение |
   |---|---|
   | `VITE_API_URL` | публичный URL бэкенда (шаг 1.5) |
   | `VITE_ALLOW_BROWSER` | `false` (в проде дашборд только из Telegram) |
4. Deploy → получишь `https://pythia.<...>.vercel.app`.
5. Впиши этот URL в `WEBAPP_URL` Telegram-сервиса (шаг 2.3) и передеплой бота.

### Альтернатива: фронтенд на Railway (вместо Vercel)

Фронт можно держать на Railway рядом с бэком — он раздаётся через Dockerfile
(`frontend/Dockerfile`: сборка Vite → отдача статики nginx, SPA-фоллбэк, слушает `$PORT`).
1. **New → GitHub Repo** (тот же репо) → ещё один сервис.
2. **Settings → Root Directory** = `frontend` (Railway возьмёт `frontend/Dockerfile`).
3. **Variables** (важно — Vite впекает их на ЭТАПЕ СБОРКИ, Railway пробрасывает их как
   build-args, имена совпадают с `ARG` в Dockerfile):
   | Переменная | Значение |
   |---|---|
   | `VITE_API_URL` | публичный URL бэкенда |
   | `VITE_ALLOW_BROWSER` | `false` |
4. **Settings → Networking → Generate Domain** → публичный `https://…up.railway.app`.
5. Этот URL → в `WEBAPP_URL` Telegram-сервиса и в BotFather menu button.

> Если меняешь зависимости — генерируй `package-lock.json` на том же npm, что и деплой
> (npm 10/linux), иначе возможна ошибка `Invalid Version:` при сборке. Проще:
> `docker run --rm -v "$PWD":/app -w /app node:22-alpine npm install`.

---

## 4. Привязка Mini App в BotFather

1. `/setmenubutton` → выбрать бота → задать URL = Vercel-URL → текст кнопки, напр. «Открыть Pythia».
2. (Опц.) `/newapp` → привязать Mini App к боту с тем же URL.
3. Открой бота в Telegram → `/start` → кнопка «🚀 Открыть Pythia» → дашборд.

---

## 5. Чек-лист прод-безопасности

- [ ] `ALLOW_UNVERIFIED_AUTH=false` на бэкенде (иначе любой может дёргать чужие данные).
- [ ] `VITE_ALLOW_BROWSER=false` на фронте (дашборд только через Telegram).
- [ ] `TELEGRAM_BOT_TOKEN` задан на бэкенде (без него проверка initData → 500).
- [ ] `INTERNAL_API_TOKEN` одинаковый у бэкенда и бота, сгенерирован случайно.
- [ ] `TRADING_MODE=paper` (live — только после подтверждённого edge).
- [ ] Postgres-плагин в EU-регионе; `DATABASE_URL` — через ссылку `${{Postgres.DATABASE_URL}}`.
- [ ] Никаких секретов в репозитории (`.env` в `.gitignore` — уже так).
- [ ] **Бэкенд — 1 реплика** (replicas=1). Планировщик (APScheduler) крутится внутри
      процесса; несколько реплик → дублирование торговых циклов и двойные сделки.
- [ ] (Под live в будущем) `POLYMARKET_PRIVATE_KEY` — отдельный кошелёк, лимит средств,
      spend-cap на xAI/Groq в их консолях.

---

## 6. Проверка после деплоя

```bash
curl https://<backend>.up.railway.app/health        # {"status":"ok",...}
```
- В Telegram: `/start` создаёт кошелёк, `/status` отдаёт сводку (не падает), кнопка открывает Mini App.
- В Mini App: задать депозит, запустить бота → во вкладке «Сигналы AI» появляются решения,
  «Участвует» — открытые позиции (после первого цикла, ≤ пары минут).
- Логи Railway бэкенда: видно `Планировщик: …` и периодические циклы.

---

## Альтернативы

- **Render:** аналогично, но платный план для always-on (free засыпает). Можно описать
  всё в `render.yaml` (backend docker + Postgres + worker).
- **Fly.io:** `fly launch` в `backend/` и `telegram/`, Postgres через `fly postgres create` (EU-регион).
- **Свой EU VPS + Caddy:** если позже захочешь уйти с PaaS — один `docker-compose` + Caddy
  (авто-TLS) на Hetzner, фронт статикой там же. Скажи — соберу конфиг.
