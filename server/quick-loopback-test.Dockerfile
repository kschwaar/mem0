ARG BASE_IMAGE=mem0-server-quick-loopback-base
FROM ${BASE_IMAGE}

RUN pip install --no-cache-dir pytest
WORKDIR /workspace/server
COPY server/*.py ./
COPY server/routers/*.py ./routers/
COPY server/tests/*.py ./tests/
