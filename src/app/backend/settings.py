import os


SESSION_SIZE = int(os.getenv("SESSION_SIZE", "12"))
FLUSH_WAIT_SECONDS = float(os.getenv("FLUSH_WAIT_SECONDS", "15"))
MIN_PARTIAL_SESSION_SIZE = int(os.getenv("MIN_PARTIAL_SESSION_SIZE", "2"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres.databases.svc.cluster.local:5432/app",
)
REDIS_HOST = os.getenv("REDIS_HOST", "redis.databases.svc.cluster.local")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
NAMESPACE = os.getenv("NAMESPACE", "default")
