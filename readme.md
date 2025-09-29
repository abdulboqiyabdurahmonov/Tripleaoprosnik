# Telegram Survey Bot (Car Rental Warm-Up)

Готовый бот-опросник для автопарков. Собирает ответы в Google Sheets. Работает в двух режимах:
- **Локально** — через polling (`python bot.py`)
- **На сервере (Render)** — через webhook (`uvicorn bot:app --host 0.0.0.0 --port $PORT`)

## 1) Создай бота у BotFather
1. В Telegram: `@BotFather` → `/newbot`
2. Назови бота, получи **BOT_TOKEN**
3. (Опционально) Задай описание и аватар.

## 2) Подготовь Google Sheets
1. Создай таблицу Google и скопируй её ID из URL.
2. Создай **Service Account** в Google Cloud → выдай роль «Editor».
3. Скачай JSON ключ (service account).
4. Открой созданную таблицу → «Поделиться» → добавь e-mail сервис-аккаунта с правом **Редактор**.
5. Скопируй содержимое JSON в переменную `GOOGLE_SERVICE_ACCOUNT_JSON` (можно как raw JSON, можно base64).
6. В переменную `GOOGLE_SHEET_ID` внеси ID таблицы.
   - Бот сам создаст заголовок строк, если его нет.

## 3) Запуск локально
```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Заполни .env на основе .env.example и экспортируй переменные окружения
export $(grep -v '^#' .env | xargs)  # Windows: set VAR=...
python bot.py
```
Открой Telegram, напиши боту `/start` и пройди опрос.

## 4) Деплой на Render (Webhook)
1. Создай **Web Service** → Python → подключи репозиторий или загрузи эти файлы.
2. **Start Command:** `uvicorn bot:app --host 0.0.0.0 --port $PORT`
3. В **Environment** добавь переменные:
   - `BOT_TOKEN`
   - `BASE_URL` (например, `https://your-service.onrender.com`)
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`
4. Разверни сервис и открой URL `/set-webhook` один раз, чтобы бот начал получать обновления:
   - `https://your-service.onrender.com/set-webhook`
5. Проверка: `GET /healthz` → должно вернуть `ok`.

## 5) Редактирование вопросов
Файл `bot.py` → список `SURVEY`. Можно менять текст/порядок/опции.
- Для мультивыбора правь список `FEATURES`.
- Можно пропускать опрос и оставить только контакт, кликнув «Оставить контакт без опроса».

## 6) Где лежат ответы
- Если настроен Google Sheets — ответы пишутся в **лист 1**.
- Если нет — создаётся локальный `responses.csv` (временный на Render).

## 7) Команды
- `/start` — начать
- `/cancel` — остановить опрос

## 8) Безопасность
- Не публикуй `BOT_TOKEN` и JSON ключ сервис-аккаунта.
- На проде используй отдельный Google Project/Sheet, ограничь доступ.

