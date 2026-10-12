FROM python:3.13-slim
WORKDIR /app
COPY api/ /app/
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/server.py"]
