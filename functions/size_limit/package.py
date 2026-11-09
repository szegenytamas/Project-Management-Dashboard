"""Build the Lambda deployment package: functions/size_limit/build/size_limit.zip.

    uv run python functions/size_limit/package.py

The zip contains the handler module, its pure-Python dependencies (pg8000 and
friends) and the RDS CA bundle for verified TLS. boto3 is not bundled: the
Lambda Python runtime provides it.

Dependency versions come from uv.lock - exactly the versions the tests ran
with, including indirect ones (scramp, asn1crypto, ...). Installing only the
pinned pg8000 once pulled in a brand-new scramp release that broke pg8000 on
TLS connections - in production only, because the tests did not use TLS.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = HERE / "build"
PACKAGE_DIR = BUILD / "package"
ZIP_PATH = BUILD / "size_limit.zip"
PYTHON_VERSION = "3.13"  # must match the Lambda runtime (python3.13)


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    PACKAGE_DIR.mkdir(parents=True)

    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv not found on PATH - see README, 'Getting started'")

    # The locked versions of every dev dependency, as pip-style constraints.
    # Constraints restrict versions without installing anything by themselves.
    constraints = BUILD / "constraints.txt"
    subprocess.run(  # noqa: S603 - fixed arguments, no user input
        [uv, "export", "--format", "requirements-txt", "--no-hashes", "--no-emit-project",
         "--only-group", "dev", "--quiet", "--output-file", str(constraints)],
        check=True,
        cwd=ROOT,
    )  # fmt: skip

    # Install for Lambda's platform, not the developer's (matters on Windows/macOS).
    subprocess.run(  # noqa: S603 - fixed arguments, no user input
        [
            uv,
            "pip",
            "install",
            "--quiet",
            "--target",
            str(PACKAGE_DIR),
            "--python-version",
            PYTHON_VERSION,
            "--python-platform",
            "x86_64-manylinux2014",
            "--only-binary",
            ":all:",
            "-r",
            str(HERE / "requirements.txt"),
            "--constraint",
            str(constraints),
        ],
        check=True,
    )
    shutil.copy(HERE / "size_limit.py", PACKAGE_DIR / "size_limit.py")
    shutil.copy(ROOT / "certs" / "rds-global-bundle.pem", PACKAGE_DIR / "rds-global-bundle.pem")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            # .lock is uv's own bookkeeping file in the target folder, not package content.
            if path.is_file() and "__pycache__" not in path.parts and path.name != ".lock":
                archive.write(path, path.relative_to(PACKAGE_DIR))

    with zipfile.ZipFile(ZIP_PATH) as archive:
        names = archive.namelist()
    top_level = sorted({n.split("/")[0] for n in names})
    size_kb = ZIP_PATH.stat().st_size // 1024
    print(f"Built {ZIP_PATH.relative_to(ROOT)}: {size_kb} KB, {len(names)} files")
    print("Top level:", ", ".join(top_level))
    print("Handler: size_limit.handler   Runtime: python" + PYTHON_VERSION)


if __name__ == "__main__":
    main()
