FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY picoin_forge_l2 ./picoin_forge_l2

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

EXPOSE 9380

CMD ["picoin-forge-coordinator", "start", "--host", "0.0.0.0", "--port", "9380"]
