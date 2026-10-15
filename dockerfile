FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY wheels/ /tmp/wheels/

RUN --mount=type=cache,target=/root/.cache/pip \
    for wheel in \
        /tmp/wheels/nvidia_nccl_cu13-2.30.7-py3-none-manylinux_2_18_x86_64.whl \
        /tmp/wheels/nvidia_nvshmem_cu13-3.4.5-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl; do \
        if [ -f "$wheel" ]; then pip install --no-deps --no-index "$wheel"; fi; \
    done && \
    if [ -f /tmp/wheels/torch-2.14.1-cp311-cp311-manylinux_2_28_x86_64.whl ]; then \
        pip install \
            --default-timeout=1000 \
            --retries 20 \
            --find-links=/tmp/wheels \
            -i https://mirrors.aliyun.com/pypi/simple \
            --trusted-host mirrors.aliyun.com \
            /tmp/wheels/torch-2.14.1-cp311-cp311-manylinux_2_28_x86_64.whl \
            -r requirements.txt; \
    else \
        pip install \
            --default-timeout=1000 \
            --retries 20 \
            -i https://mirrors.aliyun.com/pypi/simple \
            --trusted-host mirrors.aliyun.com \
            -r requirements.txt; \
    fi && \
    pip check

COPY . .

RUN mkdir -p data logs

EXPOSE 8000 8501

CMD ["python", "main.py", "serve"]