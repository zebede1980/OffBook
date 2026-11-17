# Zonos3-Mini speech server: the ggml build, CPU only. Built from source because there is no published ARM64
# image. Pinned to the commit tested on the OCI Ampere box (2026-11-17). The first build takes ~10 minutes.

FROM debian:trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential curl ca-certificates \
    pkg-config && rm -rf /var/lib/apt/lists/*
# the text normaliser is a Rust library that CMake builds with cargo
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH=/root/.cargo/bin:$PATH
ARG ZONOS_COMMIT=96d9e0ec1e39083ed336ac78f24e3dbc93721c0f
WORKDIR /src
RUN git init -q && git remote add origin https://github.com/Zyphra/ZONOS3-Mini.git \
    && git fetch -q --depth 1 origin "$ZONOS_COMMIT" && git checkout -q FETCH_HEAD \
    && git submodule update -q --init --depth 1 servers/ggml/ggml third_party/zyphra-textnorm
WORKDIR /src/servers/ggml
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j"$(nproc)" --target zonos3-server
RUN mkdir -p /out/lib && cp build/zonos3-server /out/ && find build -name 'libggml*.so*' -exec cp -P {} /out/lib/ \;

# Model weights in their own stage, so a code rebuild doesn't download them again.
FROM debian:trixie-slim AS models
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /models
RUN for f in backbone-ptq.gguf zac-keep2.gguf spk-encoder.gguf; do \
      curl -fL -sS -o "$f" "https://huggingface.co/Zyphra/ZONOS3-Mini/resolve/main/gguf/$f"; \
    done && ls -la

FROM debian:trixie-slim
# ffmpeg: the server uses it to decode the stock voices' reference clips
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home zonos
COPY --from=build /out/zonos3-server /opt/zonos/
COPY --from=build /out/lib/ /opt/zonos/lib/
COPY --from=build --chown=1000:1000 /src/assets/default_voices /opt/zonos/voices
COPY --from=models /models /opt/zonos/models
ENV LD_LIBRARY_PATH=/opt/zonos/lib
USER zonos
EXPOSE 1919
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s CMD curl -fsS http://127.0.0.1:1919/v2/speakers >/dev/null || exit 1
CMD ["/opt/zonos/zonos3-server", "/opt/zonos/models/backbone-ptq.gguf", "--host", "0.0.0.0", "--port", "1919", \
     "--tts-default-voices-dir", "/opt/zonos/voices", "--studio-projects-dir", "/tmp/zonos-projects"]
