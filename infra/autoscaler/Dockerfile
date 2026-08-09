FROM python:3.13-slim

RUN pip install --no-cache-dir redis==6.4.0

COPY scaler.py /

ENTRYPOINT ["python", "/scaler.py"]
