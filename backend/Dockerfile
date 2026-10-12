FROM python:3.12-slim

WORKDIR /app
RUN useradd --create-home --uid 10001 prism
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini alembic.ini
COPY alembic alembic
COPY app app
USER prism
EXPOSE 8741
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8741"]
