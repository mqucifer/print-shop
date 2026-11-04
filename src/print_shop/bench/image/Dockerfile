# The small bench's image (mqucifer/print-shop#14): CadQuery as the model already writes
# it, Manifold and Shapely for the second checks, and OSMesa for renders. It holds no
# bench code: `print_shop.bench.host` mounts that read-only at run time, so a change to
# the code never costs Docker's disk another image. Built by `scripts/bench.py build`,
# which refuses when the disk would fall below its floor. No container of it has a
# network. It's built for arm64 on the Mac and amd64 in CI, from the same pins.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS bench

# These first three steps are mqucifer/model-performance's `cad_eval/image/Dockerfile`,
# byte for byte (its requirements too, in base/). That's the CadQuery 2.8.0 its numbers
# were measured on, and on a machine holding that image they cost no more disk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*
COPY base/requirements.txt /opt/cad-eval/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /opt/cad-eval/requirements.txt \
    && python -c "import cadquery, trimesh; print(cadquery.__version__, trimesh.__version__)"

# What the bench adds, locked with hashes by `uv pip compile --generate-hashes`.
COPY requirements.txt /opt/bench/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes -r /opt/bench/requirements.txt \
    && python -c "import manifold3d, rtree, shapely; print(shapely.__version__)"

# OSMesa, so VTK renders with no display, at its pinned version from Debian's archive as
# it stood on a fixed date.
ARG SNAPSHOT=20261027T000000Z
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
       "deb http://snapshot.debian.org/archive/debian/${SNAPSHOT} bookworm main" \
       "deb http://snapshot.debian.org/archive/debian/${SNAPSHOT} bookworm-updates main" \
       "deb http://snapshot.debian.org/archive/debian-security/${SNAPSHOT} bookworm-security main" \
       > /etc/apt/sources.list.d/snapshot.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends libosmesa6=22.3.6-1+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

# It runs as the invoking user on a read-only filesystem: nothing here may need to write
# outside /work and /tmp.
ENV PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp NUMBA_CACHE_DIR=/tmp \
    VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow

# CadQuery's own documentation and examples at its v2.8.0 tag, fetched by commit, for the rounds'
# `docs` tool (mqucifer/print-shop#17). Apache-2.0: the index's builder checks the licence and
# refuses without it. Each code block is run here, in this image, and marked as it ran. As in
# mqucifer/model-performance's `cad_eval/image/Dockerfile.v2`, with its builders, but for where the
# repository's name is given, since print-shop's source holds one URL (`test_config_boundary.py`).
FROM bench AS docs
ARG CADQUERY_COMMIT=fb4c6d41863aee270c46ab64397f0d2675e74be0
ARG CADQUERY_REPOSITORY=https://github.com/CadQuery/cadquery
ADD --keep-git-dir=false ${CADQUERY_REPOSITORY}.git#${CADQUERY_COMMIT} /src/cadquery
COPY docs_index.py /build/docs_index.py
RUN cd /tmp && python /build/docs_index.py /src/cadquery ${CADQUERY_COMMIT} v2.8.0 /build/docs.json \
    ${CADQUERY_REPOSITORY}

# The image itself: the bench, and the two indexes `lookup` and `docs` search. What's installed,
# read at the build, not a list anyone wrote.
FROM bench
COPY lookup_index.py /build/lookup_index.py
RUN mkdir -p /opt/cad-eval \
    && cd /tmp && python /build/lookup_index.py /opt/cad-eval/lookup.json \
    && rm -rf /build
COPY --from=docs /build/docs.json /opt/cad-eval/docs.json
# The bench's version, as `print_shop.bench.host.IMAGE` names it. 0.3 reads islands inside an
# opening and the wall round a hole (mqucifer/print-shop#27); 0.4 has `light` and `thickness`
# (mqucifer/print-shop#37). Its code is mounted, not copied in.
LABEL org.opencontainers.image.version="0.4"
