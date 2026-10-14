FROM python:3.13-slim
WORKDIR /control
COPY server.py /control/server.py
RUN mkdir /queue && chown 10001:10001 /queue
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "/control/server.py"]
