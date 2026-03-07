FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false

COPY pyproject.toml ./
RUN poetry install --only main --no-interaction --no-ansi

COPY llmos_hub/ llmos_hub/

EXPOSE 8080

CMD ["uvicorn", "llmos_hub.api:create_hub_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
