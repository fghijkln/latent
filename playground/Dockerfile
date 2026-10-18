# Build from the repository root:  docker build -f playground/Dockerfile -t latent-playground .
FROM python:3.12-slim
WORKDIR /app
COPY *.py ./
COPY runtime ./runtime
COPY playground/app.py playground/index.html ./playground/
RUN useradd -m -u 10001 runner && chown -R runner /app
USER runner
ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python3", "playground/app.py"]
