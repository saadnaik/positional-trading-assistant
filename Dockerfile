FROM ubuntu:24.04 AS cpp-builder

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates cmake g++ make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY cpp/ cpp/
RUN cmake -S cpp -B /build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF \
    && cmake --build /build --target stock_reader --parallel

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FALCON_STOCK_READER=/app/cpp/build/stock_reader

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=cpp-builder /build/stock_reader /app/cpp/build/stock_reader

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
