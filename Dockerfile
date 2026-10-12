# One image, five Lambdas: Terraform picks the handler per function (image_config.command),
# e.g. ["ags.handlers.commodity_pipeline.handler"].
# CI builds it for arm64:  docker buildx build --platform linux/arm64 --build-arg GIT_SHA=... .

FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.12 AS build
# git: uv needs it for git dependencies (llmkit)
RUN dnf install -y git && dnf clean all
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
# Locked runtime dependencies only: no dev tools, no Streamlit (the ui group).
RUN uv export --frozen --no-dev --no-group ui --no-emit-project --no-hashes -o requirements.txt \
    && uv pip install --no-cache --python /var/lang/bin/python --target /deps -r requirements.txt

FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.12
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY src/ags ${LAMBDA_TASK_ROOT}/ags

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

CMD ["ags.handlers.commodity_pipeline.handler"]
