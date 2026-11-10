# The browser demo as a static site (https://scpi-sim.steitec.net).
# Build from the repository root:
#
#   docker build -f web/Dockerfile -t scpi-sim-web .
#   docker run --rm -p 8080:80 scpi-sim-web
# pinned by digest; Dependabot (docker, /web) moves it, the build tools are in build-requirements.txt
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build
WORKDIR /src
COPY web/build-requirements.txt ./web/
RUN pip install --no-cache-dir -r web/build-requirements.txt
ENV PIP_CONSTRAINT=/src/web/build-requirements.txt
COPY pyproject.toml README.md LICENSE CHANGELOG.md MANIFEST.in ./
COPY src ./src
COPY examples ./examples
COPY web ./web
RUN python web/build_site.py --out /dist

FROM nginx:1.30-alpine
LABEL org.opencontainers.image.source=https://github.com/steitec/scpi-sim \
      org.opencontainers.image.description="scpi-sim browser demo (static site)" \
      org.opencontainers.image.licenses=MIT
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dist /usr/share/nginx/html
