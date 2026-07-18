FROM python:3.12-slim
# Примечание: используется 3.12 для стабильности Docker-образов. 
# Ваша локальная .venv может оставаться на 3.14.6.

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
