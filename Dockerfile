# The RayoHX command line as a small, non-root image. It runs the same `rayohx` command as the
# Python package; there is no second downloader in here.
#
#   docker run --rm -v "$PWD/downloads:/output" -v rayohx-work:/workspace \
#       ghcr.io/coldsofttech/rayohx download https://example.com/master.m3u8 -o /output/movie.mp4
#
# Muxing is built in, so FFmpeg is not installed. See docs/docker.md.

ARG PYTHON_VERSION=3.13

# --- build: put the package and its dependencies in a virtual environment ----------------------
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src/rayohx ./src/rayohx
COPY src/rayohx_cli ./src/rayohx_cli
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-compile . \
    && /opt/venv/bin/pip uninstall --yes pip

# --- run: the same base without compilers, pip or the sources ----------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG VERSION=0.0.0
LABEL org.opencontainers.image.title="RayoHX" \
      org.opencontainers.image.description="HLS download engine and command line" \
      org.opencontainers.image.source="https://github.com/coldsofttech/RayoHX" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

# A fixed, unprivileged user, so files written to a mounted folder have a predictable owner.
RUN groupadd --system --gid 10001 rayohx \
    && useradd --system --uid 10001 --gid rayohx --no-create-home --shell /usr/sbin/nologin rayohx \
    && mkdir /output /workspace \
    && chown rayohx:rayohx /output /workspace

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RAYOHX_WORKSPACE=/workspace \
    NO_COLOR=1

USER rayohx
WORKDIR /output
VOLUME ["/output", "/workspace"]

ENTRYPOINT ["rayohx"]
CMD ["--help"]
