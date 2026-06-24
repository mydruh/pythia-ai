# ── Сборка статики (Vite) ────────────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Vite «впекает» VITE_*-переменные в бандл на ЭТАПЕ СБОРКИ (не рантайма).
# Railway пробрасывает переменные сервиса как build-args — объявляем их здесь.
ARG VITE_API_URL
ARG VITE_ALLOW_BROWSER=false
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_ALLOW_BROWSER=$VITE_ALLOW_BROWSER

RUN npm run build

# ── Раздача статики (nginx) ───────────────────────────────────────
FROM nginx:alpine
# Шаблон конфига: nginx-образ прогоняет его через envsubst при старте и
# подставляет $PORT (Railway задаёт порт через переменную окружения).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
