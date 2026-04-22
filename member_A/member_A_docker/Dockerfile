FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV WORKFLOW_SERVICE_URL=REPLACED_FOR_SAFETY_AND_ANONYMITY

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

EXPOSE 37900

CMD ["python", "app.py"]
