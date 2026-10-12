# One image, six Lambdas: Terraform picks the handler per function via image_config.command,
# e.g. ["ags.handlers.commodity_pipeline.handler"].
FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.12

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
# Locked runtime dependencies only: no dev tools, no Streamlit (the ui group).
RUN uv export --frozen --no-dev --no-group ui --no-hashes --no-emit-project -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && rm requirements.txt

COPY src/ ${LAMBDA_TASK_ROOT}/

CMD ["ags.handlers.commodity_pipeline.handler"]
