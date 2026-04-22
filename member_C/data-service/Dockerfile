FROM python:3.11-slim

ARG PORT=37588

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DB_PATH=/data/submissions.db \
    PORT=${PORT}

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py store.py schema.sql ./

VOLUME ["/data"]
EXPOSE ${PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/healthz',timeout=2).status==200 else 1)"

CMD ["python", "app.py"]
