# Лабораторная работа: приложение в Kubernetes

FastAPI-приложение для работы с бакетами и объектами Yandex Cloud Object Storage и распознавания текста через Yandex Vision OCR. Контейнер приложения разворачивается в локальном кластере Minikube; S3 и OCR остаются облачными сервисами.

## Возможности

- Просмотр, создание и удаление бакетов и объектов S3.
- Загрузка, скачивание и удаление объектов.
- Распознавание текста в изображениях через Vision OCR.
- Веб-интерфейс и HTTP API.
- Развертывание приложения в Kubernetes с помощью Deployment, Service, ConfigMap и Secret.

## Структура проекта

- `app/` — исходный код FastAPI-приложения.
- `app/routes/` — API-маршруты бакетов, объектов и OCR.
- `app/static/index.html` — веб-интерфейс.
- `app/Dockerfile` — сборка контейнерного образа.
- `app/requirements.txt` — зависимости Python.
- `k8s/configmap.yaml` — несекретные настройки облачных endpoint’ов.
- `k8s/deployment.yaml` — параметры запуска Pod, проверки здоровья и подключение конфигурации.
- `k8s/service.yaml` — сетевой доступ к приложению внутри кластера.
- `app/.env.k8s-secret.example` — файл с именами секретных переменных.

## Требования

- Docker Desktop запущен.
- Установлены Minikube и `kubectl`.
- Учётные данные Yandex Cloud с доступом к S3 и ключ для Vision OCR.

Kubernetes в этой лабораторной работает локально через Minikube; облачный Kubernetes-провайдер не используется.

## Подготовка облачных учётных данных

Приложению нужны следующие переменные окружения:

| Переменная | Хранение | Назначение |
| --- | --- | --- |
| `S3_ENDPOINT_URL` | ConfigMap | S3 endpoint; по умолчанию `https://storage.yandexcloud.net` |
| `VISION_OCR_ENDPOINT_URL` | ConfigMap | Vision OCR endpoint; по умолчанию `https://ai.api.cloud.yandex.net` |
| `IAM_TOKEN` | Kubernetes Secret | IAM-токен для доступа к S3 |
| `API_KEY_YC_OCR` | Kubernetes Secret | API-ключ Vision OCR |

Скопируйте шаблон и отредактируйте локальный файл, заменив фиктивные значения на свои:

```bash
cp app/.env.k8s-secret.example app/.env.k8s-secret
```

Создайте или обновите Secret в текущем Kubernetes-контексте:

```bash
kubectl create secret generic lab4-kubernetes-credentials \
  --from-env-file=app/.env.k8s-secret \
  --dry-run=client -o yaml | kubectl apply -f -
```

`app/.env.k8s-secret` исключен из Git и Docker build context. Никогда не публикуйте этот файл, не вставляйте секреты в манифесты и не коммитьте сгенерированный из них Secret YAML. Kubernetes Secret не шифрует значения сам по себе.

При необходимости IAM-токен можно получить скриптом `app/gen-and-exchange-jwt-to-iam.py`, используя закрытый ключ сервисного аккаунта. Скрипт сохраняет токен в `app/.env`; скопируйте его значение в локальный `app/.env.k8s-secret`. Файл ключа нельзя публиковать или добавлять в образ.

## Развертывание в Minikube

Выполняйте команды из корня проекта. Сначала запустите локальный кластер и убедитесь, что узел готов:

```bash
minikube start --driver=docker
kubectl get nodes
```

Создайте Secret, как описано выше, затем соберите образ и загрузите его в Minikube:

```bash
docker build -t lab4-kubernetes-app:local -f app/Dockerfile app
minikube image load lab4-kubernetes-app:local
```

Примените манифесты и дождитесь завершения развертывания:

```bash
kubectl apply -f k8s/
kubectl get deployments,pods,services
```

Откройте приложение с локального компьютера. Команда `port-forward` должна оставаться запущенной в отдельном терминале:

```bash
kubectl port-forward service/lab4-kubernetes-app 8000:8000
```

В другом терминале проверьте endpoint здоровья:

```bash
curl http://localhost:8000/health
```

Ожидаемый ответ: `{"status":"ok"}`. Веб-интерфейс доступен по адресу <http://localhost:8000>, интерактивная документация API — <http://localhost:8000/docs>.


## API

Базовый путь: `/`.

- `GET /health` — проверка работоспособности сервиса.
- `GET /buckets` — список бакетов.
- `POST /buckets` — создание бакета; тело JSON: `{ "name": "bucket-name" }`.
- `GET /buckets/{bucket_name}` — сведения о бакете и статистика объектов.
- `DELETE /buckets/{bucket_name}?force=true` — удаление бакета; при `force=true` сначала удаляются объекты.
- `GET /buckets/{bucket_name}/objects` — список объектов; поддерживается параметр `prefix`.
- `POST /buckets/{bucket_name}/objects` — загрузка через `multipart/form-data` с полем `file` и необязательным полем `key`.
- `GET /buckets/{bucket_name}/objects/{object_key}` — просмотр или скачивание объекта; `download=true` включает режим скачивания.
- `DELETE /buckets/{bucket_name}/objects/{object_key}` — удаление объекта.
- `POST /buckets/{bucket_name}/recognitions` — запуск асинхронного OCR для объекта из бакета.
- `GET /recognitions/{operation_id}?bucketName={bucket_name}&objectKey={object_key}` — получение результата OCR и сохранение копии исходного PNG/JPEG с рамками в том же бакете.

Полная схема API доступна в Swagger UI по адресу `/docs`.

## Веб-интерфейс

Интерфейс позволяет просматривать, создавать и удалять бакеты, управлять объектами, загружать файлы размером до 50 МБ и запускать распознавание текста. После OCR интерфейс отображает распознанный текст и ссылку на изображение с рамками.