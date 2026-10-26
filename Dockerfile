# scpi-sim in a container: by default the example bench (supply, multimeter,
# multiplexer, control port). Mount your own bench file over /bench/bench.yaml.
#
#   docker run --rm -p 5025-5027:5025-5027 -p 5099:5099 ghcr.io/steitec/scpi-sim
#   docker run --rm -p 5025-5028:5025-5028 -v ./bench.yaml:/bench/bench.yaml ghcr.io/steitec/scpi-sim
FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/steitec/scpi-sim \
      org.opencontainers.image.description="Simulated SCPI lab instruments on raw TCP sockets" \
      org.opencontainers.image.licenses=MIT

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /src

COPY examples/bench.yaml /bench/bench.yaml
RUN useradd --create-home --uid 10001 scpi
USER scpi
WORKDIR /bench

EXPOSE 5025 5026 5027 5028 5099
ENTRYPOINT ["scpi-sim"]
CMD ["serve", "--bench", "/bench/bench.yaml", "--host", "0.0.0.0"]
