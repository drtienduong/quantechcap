# QuanTechCap web: chỉ dùng thư viện chuẩn Python -> không cần pip install.
# Build context là thư mục testversion/ (cần cả common/ và web/).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QC_DATA_DIR=/data \
    QC_WEB_CONFIG=/data/web_config.json \
    QC_HOST=0.0.0.0 \
    QC_PORT=8800

# Chạy bằng user thường, không chạy root.
RUN useradd --create-home --uid 10001 qc \
 && mkdir -p /data && chown qc:qc /data

WORKDIR /app
COPY common/ /app/common/
COPY web/qc_web.py web/qc_admin.py /app/web/
COPY web/static/ /app/web/static/

USER qc
WORKDIR /app/web
VOLUME ["/data"]
EXPOSE 8800

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/login', timeout=4).status==200 else 1)"

# Lần đầu: tự tạo web_config.json + desk_token trong volume (không in token ra log). Các lần sau: giữ nguyên.
CMD ["sh", "-c", "python qc_admin.py init --quiet && exec python qc_web.py"]
