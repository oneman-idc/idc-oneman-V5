ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS build
ARG PIP_INDEX_URL
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PIP_DEFAULT_TIMEOUT=60
COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then pip wheel --retries 5 --index-url "$PIP_INDEX_URL" --wheel-dir /wheels -r requirements.txt; else pip wheel --retries 5 --wheel-dir /wheels -r requirements.txt; fi

ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv
COPY --from=build /wheels /wheels
COPY requirements.txt /app/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /app/requirements.txt && rm -rf /wheels
WORKDIR /app
COPY vps_one ./vps_one
RUN mkdir -p /app/data && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 9080
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=5 CMD ["python","-c","import urllib.request;urllib.request.urlopen('http://127.0.0.1:9080/healthz',timeout=2)"]
CMD ["uvicorn","vps_one.main:app","--host","0.0.0.0","--port","9080","--workers","1","--proxy-headers","--limit-concurrency","512","--backlog","1024","--timeout-keep-alive","5"]
