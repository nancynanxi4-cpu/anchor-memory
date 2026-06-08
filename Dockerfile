FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends nginx && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV SENTENCE_TRANSFORMERS_HOME=/app/models

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

COPY *.py .
COPY web/ web/
COPY entrypoint.sh .
COPY nginx.conf /etc/nginx/nginx.conf
RUN chmod +x entrypoint.sh

RUN mkdir -p /app/anchor_data /var/log/nginx

EXPOSE 80

ENTRYPOINT ["./entrypoint.sh"]
