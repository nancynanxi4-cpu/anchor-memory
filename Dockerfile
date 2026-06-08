FROM python:3.12-slim AS builder

WORKDIR /app

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /root/.cache /root/.cache

COPY *.py .
COPY web/ web/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN mkdir -p /data

ENV ANCHOR_WEB_PASSWORD=anchor
ENV TRANSFORMERS_CACHE=/root/.cache/huggingface

EXPOSE 5000 3333

ENTRYPOINT ["./entrypoint.sh"]
