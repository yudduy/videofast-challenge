# videofast toolchain: pinned build + measurement environment.
# All untrusted (submission) code and all trusted compute run inside this image.
# The image is pushed to ghcr and pinned BY DIGEST in harness/config.json —
# the digest, not the tags below, is the hermetic anchor.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    nasm \
    meson \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv \
    time \
    util-linux \
    ca-certificates \
    curl \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# dav1d (conformance decoder), pinned tag — STATIC so the CLI is self-contained
# across the fresh `docker run` invocations used per decode (no libdav1d.so at runtime).
RUN curl -fsSL https://code.videolan.org/videolan/dav1d/-/archive/1.5.1/dav1d-1.5.1.tar.gz -o /tmp/dav1d.tgz \
    && mkdir -p /tmp/dav1d && tar -xzf /tmp/dav1d.tgz -C /tmp/dav1d --strip-components=1 \
    && meson setup /tmp/dav1d/build /tmp/dav1d --buildtype release \
        -Ddefault_library=static -Denable_tools=true -Denable_tests=false \
    && ninja -C /tmp/dav1d/build \
    && cp /tmp/dav1d/build/tools/dav1d /usr/local/bin/dav1d \
    && rm -rf /tmp/dav1d /tmp/dav1d.tgz

# libvmaf CLI (guard metrics only, not scored), pinned tag; neg model built in — STATIC.
RUN curl -fsSL https://github.com/Netflix/vmaf/archive/refs/tags/v3.0.0.tar.gz -o /tmp/vmaf.tgz \
    && mkdir -p /tmp/vmaf && tar -xzf /tmp/vmaf.tgz -C /tmp/vmaf --strip-components=1 \
    && meson setup /tmp/vmaf/libvmaf/build /tmp/vmaf/libvmaf --buildtype release \
        -Ddefault_library=static -Denable_tests=false -Denable_docs=false \
    && ninja -C /tmp/vmaf/libvmaf/build \
    && cp /tmp/vmaf/libvmaf/build/tools/vmaf /usr/local/bin/vmaf \
    && rm -rf /tmp/vmaf /tmp/vmaf.tgz

# Trusted CPU-accounting wrapper (baked in, not solver-editable). Reads the container's
# own cgroup CPU usage around the wrapped command, so it counts EVERY process in the pid
# namespace — including double-forked / reparented / un-reaped children that evade
# /usr/bin/time and RUSAGE_CHILDREN. Requires `docker run --cgroupns=private`.
COPY cpu-wrap /usr/local/bin/cpu-wrap
RUN chmod +x /usr/local/bin/cpu-wrap

# Pinned python compute environment (PSNR + BD-rate run inside the container)
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir numpy==2.2.6 scipy==1.15.3

ENV PATH="/opt/venv/bin:${PATH}"

RUN useradd -m -u 10001 sandbox
WORKDIR /work
