FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY badminton_booker ./badminton_booker
COPY request.txt ./request.txt
COPY config.example.json ./config.example.json
COPY config.request-file.example.json ./config.request-file.example.json
COPY README.md ./README.md

EXPOSE 8765

CMD ["python", "-m", "badminton_booker", "--host", "0.0.0.0", "--port", "8765", "web", "--request-file", "request.txt"]
