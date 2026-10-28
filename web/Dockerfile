# The browser demo as a static site (https://scpi-sim.steitec.net).
# Build from the repository root:
#
#   docker build -f web/Dockerfile -t scpi-sim-web .
#   docker run --rm -p 8080:80 scpi-sim-web
FROM python:3.13-slim AS build
RUN pip install --no-cache-dir "build>=1.2"
WORKDIR /src
COPY pyproject.toml README.md LICENSE CHANGELOG.md MANIFEST.in ./
COPY src ./src
COPY examples ./examples
COPY web ./web
RUN python web/build_site.py --out /dist

FROM nginx:1.31-alpine
LABEL org.opencontainers.image.source=https://github.com/steitec/scpi-sim \
      org.opencontainers.image.description="scpi-sim browser demo (static site)" \
      org.opencontainers.image.licenses=MIT
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dist /usr/share/nginx/html
