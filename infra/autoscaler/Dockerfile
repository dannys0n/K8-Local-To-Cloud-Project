FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY scaler.py /

ENTRYPOINT ["python", "/scaler.py"]
