FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY web/ web/
COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

RUN mkdir -p /app/anchor_data

EXPOSE 8000 8001

ENTRYPOINT ["./entrypoint.sh"]
