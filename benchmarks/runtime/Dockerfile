# Disposable test environment. Build with network (dependency installation); run with --network none.
# Build context: a pinned source snapshot (git archive) plus this Dockerfile's INSTALL build arg.
FROM python:3.12-slim
ARG INSTALL
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY snapshot/ /work/
RUN pip install --no-cache-dir --upgrade pip && sh -c "$INSTALL" && pip freeze > /work/.deps-freeze.txt
COPY sitecustomize.py /trace/sitecustomize.py
COPY manifest.json /trace/manifest.json
COPY run_tests.sh /trace/run_tests.sh
ENTRYPOINT ["sh", "/trace/run_tests.sh"]
