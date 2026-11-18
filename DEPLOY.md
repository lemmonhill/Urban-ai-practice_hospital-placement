# Публикация на GitHub и бесплатном Render

Приложение можно опубликовать без внешних карт и без исходных данных практики 2:
в репозитории лежит компактный набор `data/astrakhan_compact/` размером около
40 МБ. Этого достаточно для GitHub (файлы меньше 100 МБ) и для Docker-образа
Render.

## 1. Подготовить репозиторий

Из папки `hospital-placement`:

```powershell
git init
git add .
git commit -m "Hospital placement app"
git branch -M main
```

Создайте пустой репозиторий на GitHub и добавьте remote:

```powershell
git remote add origin https://github.com/<username>/hospital-placement.git
git push -u origin main
```

Проверьте, что в коммит не попали `.venv`, `__pycache__` и старая папка
`data/astrakhan/`: они перечислены в `.gitignore` и `.dockerignore`.
Компактные данные должны попасть в репозиторий.

## 2. Вариант A: Render Blueprint (Docker, рекомендуется)

1. Зарегистрируйтесь на <https://render.com> и привяжите GitHub.
2. Нажмите **New → Blueprint**.
3. Выберите репозиторий `hospital-placement`.
4. Render прочитает `render.yaml` и создаст web-сервис с Dockerfile.
5. Дождитесь окончания сборки и откройте выданный адрес `https://*.onrender.com`.

В `render.yaml` уже задано:

- `plan: free`;
- один контейнер и health check `/api/health`;
- `HOSPITAL_PLACEMENT_VALIDATE_BLOCKSNET=0` — при старте не запускается
  дополнительная сверка BlocksNet, чтобы быстрее стартовать и не тратить память;
  сохранённый расчёт всё равно сверяется с векторизованной реализацией;
- `MALLOC_ARENA_MAX=2` — меньше фрагментации памяти.

## 3. Вариант B: Render вручную через Docker

1. **New → Web Service**.
2. Подключите GitHub-репозиторий.
3. Language: **Docker**.
4. Plan: **Free**.
5. Health Check Path: `/api/health`.
6. Добавьте переменные окружения:
   - `HOSPITAL_PLACEMENT_VALIDATE_BLOCKSNET=0`
   - `MALLOC_ARENA_MAX=2`
7. Нажмите **Create Web Service**.

## 4. Вариант C: Render без Docker (Python)

В настройках web-сервиса:

- Build Command: `pip install -r requirements.txt`
- Start Command:
  `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300`
- Health Check Path: `/api/health`
- Environment: `HOSPITAL_PLACEMENT_VALIDATE_BLOCKSNET=0`

Этот же запуск лежит в `Procfile`:

```text
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300
```

## 5. Проверка после публикации

```powershell
Invoke-WebRequest -UseBasicParsing https://<service>.onrender.com/api/health
```

Ожидаемый ответ:

```json
{"blocks": 4441, "ok": true}
```

Затем откройте сайт и повторите быструю проверку из `README.md`: исходный
дефицит 1 546, сценарий с кварталом 321 даёт дефицит 952 и сокращение 594,
автоподбор загружает 4 441 вариант и находит 23 равноценных квартала.

## 6. Ограничения бесплатного Render

- Сервис засыпает после простоя; первый запрос после пробуждения может занять
  десятки секунд.
- Память ограничена (ориентир — 512 МБ). Приложение с компактной матрицей
  занимает около 360 МБ RSS; запускайте **один** worker gunicorn.
- Диск эфемерный: сохранённые пользователем сценарии в `data/results/scenarios/`
  могут исчезнуть после перезапуска. Это ожидаемо для демо.
- Автоподбор с холодным кэшем считается около 3 минут. В репозитории уже лежит
  готовый кэш на 600 мест, поэтому кнопка автоподбора отвечает сразу.
- Для постоянного хранения сценариев нужен Render Disk (платно) или внешняя БД.

## 7. Локальная проверка Docker

Если установлен Docker Desktop:

```powershell
docker build -t hospital-placement .
docker run --rm -p 8050:8050 -e PORT=8050 hospital-placement
```

Откройте <http://127.0.0.1:8050/>. Контейнер работает так же, как на Render:
`gunicorn`, один worker, четыре потока, без внешних картографических запросов.

## 8. Обновление компактных данных

Если исходные данные практики 2 изменились, пересоберите компактный набор:

```powershell
& "..\python-package-boilerplate\.venv\Scripts\python.exe" scripts\build_compact_data.py
```

Скрипт читает `examples/data/astrakhan/` из `python-package-boilerplate` и
перезаписывает `data/astrakhan_compact/`. После этого перезапустите тесты и
проверьте исходный дефицит 1 546.
