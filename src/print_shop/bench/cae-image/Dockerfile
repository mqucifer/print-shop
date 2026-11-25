# The bench's second image (mqucifer/print-shop#142): FreeCAD, headless, with gmsh and CalculiX,
# for the readings OCCT alone doesn't give: `fit-on`, a part's way on, and `bend`, the strain an
# arm takes as it bends. It holds no bench code: `print_shop.bench.host` mounts that read-only at
# run time, as it does for the first image. Built by `scripts/bench.py build cae`, which refuses
# when the disk would fall below its floor. No container of it has a network.
#
# Every package is conda-forge's, by name, version, build and MD5 (`lock-arm64.txt` on the Mac,
# `lock-amd64.txt` in CI), so a build installs that list and solves nothing: FreeCAD 1.1.3, CalculiX 2.23, gmsh 4.15.2,
# OCCT 7.9.3 and Python 3.14 among them. The base is micromamba 2.0.7, by its digest.
#
# Not 2.0.5 or 2.0.6 (mqucifer/print-shop#307): their libcurl, 8.11.1, closes each eventfd of
# its threaded resolver twice (curl/curl#15725), so the second close can take a descriptor
# another thread has just been handed. When that's glibc's netlink socket in a `getaddrinfo`,
# the install aborts with "Unexpected error 9 on netlink descriptor". curl 8.12.0's changelog
# has the fix, "asyn-thread: avoid closing eventfd twice" (curl/curl#15727), and 2.0.7 is the
# first micromamba with it: `micromamba info` says libcurl/8.12.1. Its Debian is 2.0.5's but for
# libgnutls30 and libtasn1-6, which nothing in /opt/conda loads.
FROM mambaorg/micromamba:2.0.7@sha256:5247f7fe17ceb08f0d874444793b7650dca78f976843d4ad533d7ff419431873

ARG TARGETARCH
ARG CHANNEL=https://conda.anaconda.org/conda-forge
COPY lock-${TARGETARCH}.txt /opt/cae/lock.txt
# One layer: what's installed, less the downloaded packages, the headers and the static
# libraries, which nothing here builds against (about 1.1 GB). Then FreeCAD, its FEM workbench,
# gmsh and CalculiX each start, or there's no image.
RUN { echo @EXPLICIT; sed -n "s|^\([a-z0-9-]*\) \([^ ]*\) v\([^ ]*\) \([^ ]*\) \([^ ]*\) \([0-9a-f]*\)\$|${CHANNEL}/\1/\2-\3-\4.\5#\6|p" /opt/cae/lock.txt; } > /tmp/packages.txt \
    && test "$(grep -c '^https' /tmp/packages.txt)" = "$(grep -c '^[a-z]' /opt/cae/lock.txt)" \
    && micromamba install -y -n base -f /tmp/packages.txt \
    && micromamba clean -a -y \
    && rm -rf /opt/conda/pkgs /opt/conda/include \
    && find /opt/conda/lib -name '*.a' -delete \
    && /opt/conda/bin/python -c "import sys; sys.path.append('/opt/conda/lib'); import FreeCAD, Part, ObjectsFem; print('FreeCAD', '.'.join(FreeCAD.Version()[:3]))" \
    && /opt/conda/bin/gmsh --version \
    && { /opt/conda/bin/ccx -v || true; } | grep 'Version 2.23'

# It runs as the invoking user on a read-only filesystem, with no display: nothing here may need
# to write outside /work and /tmp, and FreeCAD is told its home, as the user has no entry in
# /etc/passwd for it to look one up. No entrypoint: the bench names what runs, by its path.
ENV PATH=/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp \
    OMP_NUM_THREADS=2 FREECAD_USER_HOME=/tmp
ENTRYPOINT []
CMD ["/opt/conda/bin/python", "-V"]
# The image's version, as `print_shop.bench.host.CAE_IMAGE` names it. Its code is mounted, not
# copied in.
LABEL org.opencontainers.image.version="0.1"
