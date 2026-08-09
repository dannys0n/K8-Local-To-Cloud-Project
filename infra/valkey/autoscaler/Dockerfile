FROM valkey/valkey:8.1-alpine

RUN apk add --no-cache python3

COPY scaler.py /scaler.py

ENTRYPOINT ["python3", "/scaler.py"]
