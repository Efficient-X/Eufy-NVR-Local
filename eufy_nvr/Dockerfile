ARG BUILD_FROM="ghcr.io/home-assistant/base-debian:bookworm@sha256:60cd882d5200157082e568a0b775cca0d9cb5704452ada3cf6f206c4dd6d83c5"
FROM ${BUILD_FROM} AS legacy

ARG GO2RTC_VERSION="v1.9.14"
ARG GO2RTC_SHA256_AMD64="32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6"
ARG GO2RTC_SHA256_ARM64="359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50"

LABEL org.opencontainers.image.title="Eufy NVR Local" \
    org.opencontainers.image.description="Local eufy S4/T8N00 WebRTC to RTSP bridge for Home Assistant" \
    org.opencontainers.image.source="https://github.com/Efficient-X/Eufy-Home-Assistant"

# The image includes third-party components; the project MIT licence alone
# does not describe the licences of its dependencies.
COPY THIRD_PARTY.md /usr/share/doc/eufy-nvr/THIRD_PARTY.md
COPY licenses /usr/share/doc/eufy-nvr/licenses

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AV_LOG_FORCE_NOCOLOR=1

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-dev \
        python3-venv \
        nodejs \
        npm \
        ffmpeg \
        build-essential \
        pkg-config \
        libavformat-dev \
        libavcodec-dev \
        libavdevice-dev \
        libavutil-dev \
        libavfilter-dev \
        libswscale-dev \
        libswresample-dev \
        libopus-dev \
        libvpx-dev \
        libsrtp2-dev \
        libffi-dev \
        libssl-dev \
        ca-certificates \
        curl \
        git \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN if [ ! -e /usr/bin/python ]; then ln -s /usr/bin/python3 /usr/bin/python; fi

# The reviewed bridge is vendored into the add-on build context. This prevents
# a package version from accidentally running an older remote source commit.
COPY bridge /opt/eufy/bridge

WORKDIR /opt/eufy/bridge

RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

RUN node fetch_deps.js \
    && node sctp_oracle.js selftest

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
        amd64)  asset="go2rtc_linux_amd64"; sha256="${GO2RTC_SHA256_AMD64}" ;; \
        arm64)  asset="go2rtc_linux_arm64"; sha256="${GO2RTC_SHA256_ARM64}" ;; \
        *) echo "unsupported arch ${arch}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /opt/eufy/bridge/bin; \
    curl -fsSL -o /opt/eufy/bridge/bin/go2rtc \
        "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/${asset}"; \
    echo "${sha256}  /opt/eufy/bridge/bin/go2rtc" | sha256sum --check --strict; \
    chmod +x /opt/eufy/bridge/bin/go2rtc; \
    /opt/eufy/bridge/bin/go2rtc -version || true

RUN python3 -c "import av, aiortc, Crypto, cryptography; from aiortc import RTCPeerConnection; print('av', av.__version__, 'aiortc', aiortc.__version__, 'cryptography', cryptography.__version__)"

COPY run.sh /run.sh
RUN chmod a+x /run.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/1985' || exit 1

WORKDIR /opt/eufy/bridge
CMD [ "/run.sh" ]

# Guided setup startup stage. The final release stage below chooses the default;
# sealed guided packages and the public source preview select this guided stage.
FROM legacy AS guided
# Let the guided coordinator close its own process tree before S6 shuts down.
ENV S6_CMD_RECEIVE_SIGNALS=1
COPY guided-entry.sh /guided-entry.sh
RUN chmod a+x /guided-entry.sh
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 /opt/eufy/bridge/guided_launcher.py --health
CMD [ "/guided-entry.sh" ]

FROM guided AS release
