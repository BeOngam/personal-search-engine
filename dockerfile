FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# اول requirements.txt رو کپی کن (این خیلی مهمه!)
COPY requirements.txt .

RUN pip install \
    --default-timeout=1000 \
    --retries 20 \
    -i https://mirrors.aliyun.com/pypi/simple \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

COPY . .

RUN mkdir -p data logs

EXPOSE 8000 8501

CMD ["python", "main.py", "serve"]