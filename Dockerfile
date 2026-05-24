FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY badminton_booker ./badminton_booker
COPY config.example.json ./config.example.json
COPY config.request-file.example.json ./config.request-file.example.json
COPY README.md ./README.md
RUN touch ./request.txt

EXPOSE 8765

CMD ["python", "-m", "badminton_booker", "--host", "0.0.0.0", "--port", "8765", "web", "--request-file", "request.txt"]
