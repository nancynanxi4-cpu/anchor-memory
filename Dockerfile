FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

COPY *.py .
COPY web/ web/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN mkdir -p /app/anchor_data

ENV SENTENCE_TRANSFORMERS_HOME=/app/models

EXPOSE 8000 8001

ENTRYPOINT ["./entrypoint.sh"]
