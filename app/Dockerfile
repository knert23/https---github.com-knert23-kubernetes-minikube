FROM python:3.14-slim

WORKDIR /app

# Зависимости 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код приложения
COPY . ./app

EXPOSE 8000

# Запуск FastAPI через uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]