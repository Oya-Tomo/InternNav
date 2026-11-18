"""PEP 517 build backend that repacks the official habitat-sim conda build as a wheel.

habitat-sim publishes no Linux wheels, and building 0.2.4 from source needs an old toolchain.
The aihabitat conda build only needs glibc 2.14, GLIBCXX 3.4.19, zlib, libgomp, and the host's
libEGL/libOpenGL (glvnd), so the same binary runs from a plain virtual environment. This backend
downloads that build, checks its SHA-256, and writes its Python packages into a wheel.
"""

import base64
import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import PurePosixPath

NAME, VERSION = "habitat_sim", "0.2.4"
# The headless build with Bullet physics, as `conda install habitat-sim=0.2.4 withbullet headless`.
CONDA_URL = (
    "https://conda.anaconda.org/aihabitat/linux-64/"
    "habitat-sim-0.2.4-py3.9_headless_bullet_linux_f179b584bcd713c5a2a998132211e2cae881d6d1.tar.bz2"
)
CONDA_SHA256 = "5e833354536f8e2b69995b58d737fa115ef2df4c56dd8e5c082867f91696facd"
SITE_PACKAGES = PurePosixPath("lib/python3.9/site-packages")
EGG = SITE_PACKAGES / "habitat_sim-0.2.4-py3.9-linux-x86_64.egg"
# Python packages and extension modules taken from the conda build. Besides habitat_sim, the build
# ships the Magnum and Corrade bindings that habitat-sim and habitat-lab import.
PACKAGES = ("habitat_sim", "magnum", "corrade")
EXTENSIONS = ("_magnum", "_corrade")
TOP_LEVEL = PACKAGES + EXTENSIONS
TAG = "cp39-cp39-linux_x86_64"
# The conda package's run requirements that are Python packages, under their PyPI names.
METADATA = f"""\
Metadata-Version: 2.1
Name: habitat-sim
Version: {VERSION}
Summary: habitat-sim {VERSION} (headless, Bullet) repacked from the aihabitat conda build
Home-page: https://github.com/facebookresearch/habitat-sim
License: MIT
Requires-Python: ==3.9.*
Requires-Dist: attrs>=19.1.0
Requires-Dist: gitpython
Requires-Dist: imageio
Requires-Dist: imageio-ffmpeg
Requires-Dist: matplotlib
Requires-Dist: numba
Requires-Dist: numpy>=1.13
Requires-Dist: numpy-quaternion
Requires-Dist: pillow
Requires-Dist: scipy>=1.3.0
Requires-Dist: tqdm
"""
WHEEL = f"Wheel-Version: 1.0\nGenerator: repack\nRoot-Is-Purelib: false\nTag: {TAG}\n"
DIST_INFO = f"{NAME}-{VERSION}.dist-info"
DIST_FILES = {"METADATA": METADATA, "WHEEL": WHEEL, "top_level.txt": "\n".join(TOP_LEVEL) + "\n"}


def _download(destination):
    digest = hashlib.sha256()
    with urllib.request.urlopen(CONDA_URL) as response, open(destination, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != CONDA_SHA256:
        raise RuntimeError(f"SHA-256 mismatch for {CONDA_URL}: got {digest.hexdigest()}")


def _wheel_path(member):
    """Map a conda archive member to its path in the wheel, or None to leave it out."""
    path = PurePosixPath(member.name)
    for root in (EGG, SITE_PACKAGES):
        if root in path.parents:
            relative = path.relative_to(root)
            break
    else:
        return None
    if not member.isfile() or "__pycache__" in relative.parts:
        return None
    in_package = len(relative.parts) > 1 and relative.parts[0] in PACKAGES
    is_extension = len(relative.parts) == 1 and relative.suffix == ".so" and relative.name.split(".")[0] in EXTENSIONS
    return str(relative) if in_package or is_extension else None


def _write_dist_info(directory):
    os.makedirs(os.path.join(directory, DIST_INFO), exist_ok=True)
    for name, text in DIST_FILES.items():
        with open(os.path.join(directory, DIST_INFO, name), "w") as f:
            f.write(text)
    return DIST_INFO


def _record_line(path, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def get_requires_for_build_wheel(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _write_dist_info(metadata_directory)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    wheel_name = f"{NAME}-{VERSION}-{TAG}.whl"
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "habitat-sim.tar.bz2")
        _download(archive)
        partial = os.path.join(tmp, wheel_name)
        records = []
        with tarfile.open(archive, "r:bz2") as tar, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as whl:
            for member in tar:
                if (path := _wheel_path(member)) is None:
                    continue
                data = tar.extractfile(member).read()
                info = zipfile.ZipInfo(path, date_time=(2023, 4, 1, 0, 0, 0))
                info.external_attr = (member.mode & 0o777 | 0o100000) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                whl.writestr(info, data)
                records.append(_record_line(path, data))
            if not any(r.startswith("habitat_sim/_ext/") for r in records):
                raise RuntimeError(f"no habitat_sim bindings found in {CONDA_URL}")
            for name, text in DIST_FILES.items():
                path = f"{DIST_INFO}/{name}"
                whl.writestr(path, text)
                records.append(_record_line(path, text.encode()))
            records.append(f"{DIST_INFO}/RECORD,,")
            whl.writestr(f"{DIST_INFO}/RECORD", "\n".join(records) + "\n")
        shutil.move(partial, os.path.join(wheel_directory, wheel_name))
    return wheel_name

