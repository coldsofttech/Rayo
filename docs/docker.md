# Docker

The image runs the same `rayohx` command as the Python package, for headless use on servers and in pipelines. It holds no second downloader: the image builds the same `rayohx` and `rayohx_cli` packages from this repository.

- **Small:** a slim Python base with only the package and its dependencies. FFmpeg is not included, because joining audio and video is built in. If you need FFmpeg for an unusual codec, build your own image on top of this one and install it (see the [engine guide](engine.md)).
- **Non-root:** it runs as user `rayohx` (uid and gid `10001`), with no shell login.
- **Platforms:** `linux/amd64` and `linux/arm64`.

## Running

```bash
docker run --rm ghcr.io/coldsofttech/rayohx info https://example.com/master.m3u8

docker run --rm \
  -v "$PWD/downloads:/output" \
  -v rayohx-work:/workspace \
  ghcr.io/coldsofttech/rayohx download https://example.com/master.m3u8 -o /output/movie.mp4
```

The image's entry point is `rayohx`, so everything after the image name is a normal command; see the [command line guide](cli.md). Results go to stdout and messages to stderr, so `--json` output can be piped. Without a terminal, progress is printed as a line every 10%.

## Folders

| Folder | Purpose |
| --- | --- |
| `/output` | The working directory. A download without `-o` is saved here. Mount a folder to keep the files. |
| `/workspace` | Where partial downloads and resume state are kept (`RAYOHX_WORKSPACE`). Mount a folder or a named volume to resume after the container is removed. |

Files are written as uid `10001`. When you mount a host folder, make it writable for that user, or run the container as your own user instead:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/downloads:/output" ghcr.io/coldsofttech/rayohx download URL
```

With `--user`, also mount a writable `/workspace`, since the built-in one belongs to uid `10001`.

## Settings

Every option can be set with a `RAYOHX_` environment variable, for example `-e RAYOHX_QUALITY=720p -e RAYOHX_CONCURRENCY=8`; see [settings](cli.md#settings). The image sets `RAYOHX_WORKSPACE=/workspace` and `NO_COLOR=1`. Command line options win over the environment.

Pass secrets as files, not on the command line: mount a file and use `-H @/run/secrets/headers`, so they do not appear in the process list or shell history. Do not bake them into an image.

Exit codes are those of the command: `0` success, `1` failure, `2` invalid usage or settings, `130` cancelled. To cancel cleanly and keep what a resume needs, send an interrupt: `docker kill --signal INT <container>`.

## Building

```bash
docker build -t rayohx .
docker build --build-arg PYTHON_VERSION=3.12 -t rayohx:py312 .
```

The build context is limited by `.dockerignore` to the two packages, `pyproject.toml`, the README and the license.

## Publishing

The [Docker workflow](../.github/workflows/docker.yml) builds the image and checks it on every change to the image or the packages: it starts, it is not root, its folders are writable, and a failure is a short message with exit code 1. When a release is published, it builds both platforms and pushes `ghcr.io/<owner>/<repository>:<version>` (and `latest`, unless the release is marked as a pre-release) with provenance, but only if the repository variable `PUBLISH_GHCR` is `true`. It uses the workflow's own token, so no secret is needed. After the first push, make the package public in the repository's package settings if you want anyone to pull it.
