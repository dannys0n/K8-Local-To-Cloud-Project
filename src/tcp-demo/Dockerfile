FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY web ./web
EXPOSE 7654 8080
USER nobody
CMD ["python3", "-u", "server.py"]
