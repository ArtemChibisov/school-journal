# Перенос на сервер Projector School

Рунбук миграции с сервера `195.54.178.243` на школьный сервер `169.58.83.125`.

Дата: 19.09.2026. Переезд завершён 21.09.2026.

Документ сохранён как история и как справочник по устройству сервера: адреса, порты, правила безопасности и команды проверки остаются актуальными.

## Что известно о целевом сервере

| Параметр | Значение |
| --- | --- |
| Адрес | `169.58.83.125` |
| ОС | Ubuntu 24.04 LTS |
| Пользователь | `deploy` |
| Reverse proxy | Caddy, системный сервис, не в контейнере |
| Конфиг Caddy | `/etc/caddy/Caddyfile` |
| Порты 80 и 443 | заняты Caddy |
| Наш домен | `reports.projector-school.info` |
| Наш порт | `127.0.0.1:8090` |
| Папка проекта | `/home/deploy/school-journal` |
| Репозиторий | `proektordocs-ops/school-journal`, приватный |

На сервере уже работают Moodle, Nextcloud, NocoDB, Homepage, Uptime Kuma, Netdata и WUD.
Каждый сервис слушает только `127.0.0.1` на своём порту, наружу их пускает Caddy.

Занятые локальные порты: `3000` homepage, `3001` wud, `3002` uptime-kuma, `8080` nocodb,
`8081` moodle, `8084` nextcloud, `19999` netdata.

## Правила безопасности

1. Мы только добавляем новый сервис. Чужие контейнеры не останавливаем.
2. Порты 80 и 443 не занимаем.
3. Домены других сервисов не трогаем.
4. Перед правкой `/etc/caddy/Caddyfile` запускаем `/usr/local/bin/backup-all.sh`.
5. После перезагрузки Caddy обязательно проверяем, что соседние сайты живы.
6. Docker volumes не удаляем.
7. Секреты живут в `.env.production` на сервере и в GitHub Secrets. В репозиторий не попадают.

## Что уже сделано

- Создан приватный репозиторий `proektordocs-ops/school-journal`, история залита.
- Добавлен `docker-compose.server.yml`: без собственного reverse proxy, `web` на `127.0.0.1:8090`.
- В настройки добавлен `DJANGO_TRUST_PROXY_HEADERS`: за Caddy приложение получает обычный HTTP,
  и без этого флага Django считал бы соединение незащищённым, ломая CSRF и абсолютные ссылки.
- Workflow деплоя больше не пересобирает `docker-compose.server.yml` из production-файла.
- Оба workflow прогоняют тесты `school_years`.
- Завершено переименование токена Google в `rt.json`, файл закрыт в `.gitignore`.

## Фаза D. Подготовка сервера

Бэкап перед изменениями:

```bash
/usr/local/bin/backup-all.sh
```

Клонируем репозиторий:

```bash
git clone https://github.com/proektordocs-ops/school-journal.git /home/deploy/school-journal
```

Создаём файл секретов из шаблона:

```bash
cp /home/deploy/school-journal/.env.production.example /home/deploy/school-journal/.env.production
```

Заполняем обязательное:

- `DJANGO_SECRET_KEY` — длинная случайная строка;
- `POSTGRES_PASSWORD` и тот же пароль внутри `DATABASE_URL`;
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `ADMIN_LOG_CHAT_ID`;
- `OPENAI_API_KEY`, если используется AI-вычитка критериев.

Значения `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `GOOGLE_OAUTH_REDIRECT_URI` и
`DJANGO_TRUST_PROXY_HEADERS` в шаблоне уже проставлены под `reports.projector-school.info`.

Закрываем файл от посторонних:

```bash
chmod 600 /home/deploy/school-journal/.env.production
```

## Фаза E. Первый запуск без домена

Запуск:

```bash
docker compose -f docker-compose.server.yml up -d --build
```

Состояние контейнеров:

```bash
docker compose -f docker-compose.server.yml ps
```

Проверка изнутри сервера:

```bash
curl -sS http://127.0.0.1:8090/healthz
```

Ожидаем `{"status":"ok"}`.

Посмотреть глазами до подключения домена — с локальной машины:

```bash
ssh -L 8090:127.0.0.1:8090 deploy@169.58.83.125
```

Пока туннель открыт, приложение доступно в браузере по `http://127.0.0.1:8090/`.
Наружу при этом ничего не открыто.

Гейт фазы: приложение отвечает, соседние сервисы не затронуты.

## Фаза F. Домен, Caddy, сертификат

DNS: добавить A-запись `reports` → `169.58.83.125`.

Проверка:

```bash
dig +short reports.projector-school.info
```

Бэкап перед правкой конфига:

```bash
/usr/local/bin/backup-all.sh
```

Добавить в конец `/etc/caddy/Caddyfile`:

```text
reports.projector-school.info {
        reverse_proxy 127.0.0.1:8090
}
```

Проверить синтаксис до применения:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

Применить без перезапуска процесса:

```bash
sudo systemctl reload caddy
```

Caddy сам получит сертификат Let's Encrypt и сам проставит заголовки `X-Forwarded-Proto`,
`X-Forwarded-For` и `X-Forwarded-Host`, которые ждёт Django.

Сразу после перезагрузки проверить соседей:

```bash
curl -I https://learn.projector-school.info
```

```bash
curl -I https://files.projector-school.info
```

Если что-то отвалилось — вернуть прежний Caddyfile и повторить `systemctl reload caddy`.

Проверить себя:

```bash
curl -sS https://reports.projector-school.info/healthz
```

## Фаза G. Автодеплой

Ключ для GitHub Actions создаётся на локальной машине:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deploy_school_journal -N "" -C "github-actions-school-journal"
```

Публичная половина добавляется на сервер в `/home/deploy/.ssh/authorized_keys`.
Приватная кладётся в GitHub Secrets.

| Тип | Имя | Значение |
| --- | --- | --- |
| Secret | `DEPLOY_HOST` | `169.58.83.125` |
| Secret | `DEPLOY_USER` | `deploy` |
| Secret | `DEPLOY_SSH_KEY` | приватный ключ целиком |
| Variable | `DEPLOY_PATH` | `/home/deploy/school-journal` |
| Variable | `DEPLOY_COMPOSE_FILE` | `docker-compose.server.yml` |
| Variable | `DEPLOY_HEALTH_URL` | `https://reports.projector-school.info/healthz` |

Пользователь `deploy` должен запускать `docker compose` без запроса пароля, то есть состоять
в группе `docker`. На этом сервере это уже так.

## Фаза H. Зависящее от домена

Google Cloud Console, OAuth-клиент, разрешённые redirect URI — добавить:

```text
https://reports.projector-school.info/links/google/oauth/callback/
```

Telegram, перерегистрация адреса бота:

```bash
curl -sS "https://api.telegram.org/bot<ТОКЕН>/setWebhook?url=https://reports.projector-school.info/telegram/webhook/"
```

Проверка:

```bash
curl -sS "https://api.telegram.org/bot<ТОКЕН>/getWebhookInfo"
```

Группы прав внутри приложения:

```bash
docker compose -f docker-compose.server.yml exec web python manage.py setup_rbac_roles
```

Учётные данные Google кладутся в том `creds_data`: `client_secret.json` и `rt.json` в
подпапку `google`. Либо переносятся со старого сервера, либо OAuth проходится заново кнопкой
`Подключить Google` на странице «Классы и таблицы».

## Приёмка

- `https://reports.projector-school.info/healthz` возвращает `ok`;
- `https://reports.projector-school.info/readyz` возвращает `ok`;
- соседние сервисы отвечают как раньше;
- вход в приложение работает, разделы открываются;
- кнопка `Подключить Google` доходит до Google и возвращается без ошибки redirect URI;
- бот отвечает в Telegram;
- push в `main` запускает деплой, и он завершается зелёным.

## Старый контур

Сервер `195.54.178.243` и репозиторий `ArtChi10/school_journal` остаются как есть.
Локальная ветка `main` теперь отслеживает новый репозиторий, поэтому старый контур обновляться
не будет. Гасить его имеет смысл после того, как новый пройдёт приёмку.

Важно: на старом сервере в томе учётных данных лежит `token.json`, а код теперь ищет `rt.json`.
Если кто-то всё же задеплоит туда из старого репозитория, Google-интеграция там перестанет
работать до переименования файла.
