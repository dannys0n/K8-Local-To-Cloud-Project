FROM custompodautoscaler/python-3-13:v2.12.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY config.yaml evaluate.py metric.py post_scale.py /
