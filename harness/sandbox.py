"""Process isolation and compute dispatch for the scoring harness.

This module deliberately imports only the Python standard library.  The numerical
modules are imported lazily for native execution; official Docker execution keeps
all NumPy/SciPy work inside the pinned toolchain image.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Any, Iterable, Sequence

from harness.gates import GateError


# Native CPU timings are estimate-only; official CI scoring always uses Docker.
DEFAULT_MEMORY_GB = 6
DECODE_TIMEOUT_S = 300.0
_STDERR_TAIL_CHARS = 8_000
_TIME_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_native_cpu_warning_lock = threading.Lock()
_native_cpu_warning_printed = False


@dataclass(frozen=True, slots=True)
class _CommandResult:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True, slots=True)
class DockerMount:
    """A bind mount used by :func:`docker_run_argv`."""

    host: Path
    container: str
    read_only: bool

    def argument(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{self.host.resolve()}:{self.container}:{mode}"


def docker_run_argv(
    image: str,
    command: Sequence[str | os.PathLike[str]],
    *,
    mounts: Sequence[DockerMount] = (),
    memory_gb: int = DEFAULT_MEMORY_GB,
    cpuset_core: int | None = None,
    uid: int | None = None,
    gid: int | None = None,
) -> list[str]:
    """Construct one hermetic ``docker run`` command without executing it."""

    if memory_gb <= 0:
        raise ValueError("Docker memory limit must be positive")
    actual_uid = os.getuid() if uid is None else uid
    actual_gid = os.getgid() if gid is None else gid
    argv = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--cgroupns=private",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        f"{actual_uid}:{actual_gid}",
        "--pids-limit",
        "512",
        "--memory",
        f"{memory_gb}g",
        "--memory-swap",
        f"{memory_gb}g",
    ]
    if cpuset_core is not None:
        if cpuset_core < 0:
            raise ValueError("cpuset core must be non-negative")
        argv.append(f"--cpuset-cpus={cpuset_core}")
    for mount in mounts:
        argv.extend(("-v", mount.argument()))
    argv.append(image)
    argv.extend(os.fspath(part) for part in command)
    return argv


def encoder_argv(
    binary: str | os.PathLike[str],
    in_y4m: str | os.PathLike[str],
    out_ivf: str | os.PathLike[str],
    encoder_args: Iterable[str],
    crf: int,
) -> list[str]:
    """Construct the fixed encoder invocation in benchmark argument order."""

    return [
        os.fspath(binary),
        "-i",
        os.fspath(in_y4m),
        "-b",
        os.fspath(out_ivf),
        *encoder_args,
        "--crf",
        str(crf),
    ]


def _parse_time_v(stderr: str) -> float:
    """Parse GNU ``time -v`` output and return user plus system CPU seconds."""

    values: list[float] = []
    for label in ("User time (seconds)", "System time (seconds)"):
        matches = re.findall(
            rf"^\s*{re.escape(label)}:\s*({_TIME_NUMBER})\s*$",
            stderr,
            re.MULTILINE,
        )
        if not matches:
            raise RuntimeError(f"cannot parse {label!r} from /usr/bin/time output")
        values.append(float(matches[-1]))
    return sum(values)


def _parse_time_l(stderr: str) -> float:
    """Parse Darwin ``time -l`` output and return user plus system CPU seconds."""

    values: list[float] = []
    for label in ("user", "sys"):
        matches = re.findall(
            rf"({_TIME_NUMBER})\s+{label}\b",
            stderr,
        )
        if not matches:
            raise RuntimeError(f"cannot parse {label!r} from /usr/bin/time output")
        values.append(float(matches[-1]))
    return sum(values)


def _parse_cgroup_cpu(stderr: str) -> float:
    """Parse the sole cgroup usage marker emitted by ``cpu-wrap``."""

    matches = re.findall(
        r"^\s*CGROUP_CPU_USEC\s+([0-9]+)\s*$",
        stderr,
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise RuntimeError(
            "expected exactly one CGROUP_CPU_USEC marker from cpu-wrap, "
            f"found {len(matches)}"
        )
    return int(matches[0]) / 1_000_000.0


def _warn_native_cpu_unavailable(detail: str) -> None:
    """Print the native CPU-accounting warning once, including across workers."""

    global _native_cpu_warning_printed
    with _native_cpu_warning_lock:
        if _native_cpu_warning_printed:
            return
        _native_cpu_warning_printed = True
        print(f"WARNING per-encode CPU accounting unavailable: {detail}", flush=True)


def _native_time_command(command: Sequence[str]) -> tuple[list[str], str] | None:
    """Return a supported native time wrapper and its output format, if present."""

    time_binary = Path("/usr/bin/time")
    if not time_binary.is_file() or not os.access(time_binary, os.X_OK):
        return None
    if sys.platform == "darwin":
        return [str(time_binary), "-l", *command], "time-l"
    if sys.platform.startswith("linux"):
        return [str(time_binary), "-v", *command], "time-v"
    return None


def _tail(stderr: str, stdout: str = "") -> str:
    content = stderr.strip() or stdout.strip()
    return content[-_STDERR_TAIL_CHARS:] if content else "no process output"


def _docker_runtime_argv(argv: Sequence[str], cidfile: Path) -> list[str]:
    """Attach a host-side CID file without changing the pure public argv helpers."""

    if len(argv) >= 2 and argv[0] == "docker" and argv[1] == "run":
        return [*argv[:2], "--cidfile", str(cidfile), *argv[2:]]
    return list(argv)


def _kill_container(cidfile: Path) -> None:
    try:
        container_id = cidfile.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if not container_id:
        return
    try:
        subprocess.run(
            ["docker", "kill", container_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _execute(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    timeout_s: float,
) -> _CommandResult:
    """Run a captured subprocess and kill its process group on wall timeout."""

    cidfile: Path | None = None
    runtime_argv = list(argv)
    if len(argv) >= 2 and argv[0] == "docker" and argv[1] == "run":
        descriptor, raw_cidfile = tempfile.mkstemp(prefix="videofast-", suffix=".cid")
        os.close(descriptor)
        cidfile = Path(raw_cidfile)
        cidfile.unlink()
        runtime_argv = _docker_runtime_argv(argv, cidfile)
    try:
        process = subprocess.Popen(
            runtime_argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        if cidfile is not None:
            cidfile.unlink(missing_ok=True)
        raise RuntimeError(f"cannot start {argv[0]!r}: {exc}") from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        if cidfile is not None:
            _kill_container(cidfile)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        stdout, stderr = process.communicate()
        error = subprocess.TimeoutExpired(argv, timeout_s, stdout, stderr)
        raise error from exc
    finally:
        if cidfile is not None:
            cidfile.unlink(missing_ok=True)
    return _CommandResult(stdout, stderr, process.returncode)


class Runner:
    """Execute build, codec, and numerical substeps natively or in Docker."""

    def __init__(
        self,
        mode: str,
        config: Any,
        work_dir: str | os.PathLike[str] | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        protected_paths: Sequence[str | os.PathLike[str]] = (),
    ) -> None:
        if mode not in {"docker", "native"}:
            raise ValueError(f"unsupported runner mode {mode!r}")
        if mode == "docker" and "PENDING" in config.toolchain_image:
            raise RuntimeError("toolchain image not yet published")
        self.mode = mode
        self.config = config
        self.work_dir = Path(work_dir or config.work_dir).expanduser().resolve()
        self.cache_dir = Path(cache_dir or config.cache_dir).expanduser().resolve()
        self.harness_dir = (Path(config.repo_root) / "harness").resolve()
        self.corpus_dir = (self.cache_dir / "corpus").resolve()
        self.protected_paths = tuple(
            Path(path).expanduser().resolve() for path in protected_paths
        )
        if self.mode == "docker":
            for path in self.protected_paths:
                try:
                    path.relative_to(self.work_dir)
                except ValueError as exc:
                    raise ValueError(
                        f"protected path {path} is outside the Docker work directory"
                    ) from exc
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _work_mounts(self, *, protect: bool = False) -> tuple[DockerMount, ...]:
        """Mount run storage, optionally overlaying trusted paths read-only."""

        mounts = [DockerMount(self.work_dir, "/work", False)]
        if not protect:
            return tuple(mounts)
        for path in self.protected_paths:
            relative = path.relative_to(self.work_dir)
            mounts.append(DockerMount(path, str(Path("/work") / relative), True))
        return tuple(mounts)

    def _work_container_path(self, path: str | os.PathLike[str]) -> str:
        resolved = Path(path).expanduser().resolve()
        try:
            relative = resolved.relative_to(self.work_dir)
        except ValueError as exc:
            raise ValueError(
                f"path {resolved} is outside the Docker work directory"
            ) from exc
        return str(Path("/work") / relative)

    def _encode_mounts(
        self, in_y4m: str | os.PathLike[str]
    ) -> tuple[DockerMount, ...]:
        return (
            *self._work_mounts(protect=True),
            DockerMount(Path(in_y4m), "/input/src.y4m", True),
        )

    def _xcompute_mounts(
        self, src: str | os.PathLike[str] | None = None
    ) -> tuple[DockerMount, ...]:
        mounts = [
            *self._work_mounts(),
            DockerMount(self.harness_dir, "/harness", True),
        ]
        if src is not None:
            mounts.append(DockerMount(Path(src), "/input/src.y4m", True))
        return tuple(mounts)

    def _docker(
        self,
        command: Sequence[str],
        *,
        mounts: Sequence[DockerMount] | None = None,
        memory_gb: int = DEFAULT_MEMORY_GB,
        cpuset_core: int | None = None,
    ) -> list[str]:
        return docker_run_argv(
            self.config.toolchain_image,
            command,
            mounts=() if mounts is None else mounts,
            memory_gb=memory_gb,
            cpuset_core=cpuset_core,
        )

    def build_commands(self, source_copy: str | os.PathLike[str]) -> tuple[list[str], list[str]]:
        """Return the two commands used to configure and compile a copied tree."""

        source = Path(source_copy).expanduser().resolve()
        if self.mode == "docker":
            mount = (DockerMount(source, "/src", False),)
            cmake = self._docker(
                [
                    "cmake",
                    "-S",
                    "/src",
                    "-B",
                    "/src/build_hn",
                    "-G",
                    "Ninja",
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_SHARED_LIBS=OFF",
                ],
                mounts=mount,
            )
            ninja = self._docker(
                ["ninja", "-C", "/src/build_hn"], mounts=mount
            )
            return cmake, ninja
        build_dir = source / "build_hn"
        return (
            [
                "cmake",
                "-S",
                str(source),
                "-B",
                str(build_dir),
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=OFF",
            ],
            ["ninja", "-C", str(build_dir)],
        )

    def encode_command(
        self,
        binary: str | os.PathLike[str],
        in_y4m: str | os.PathLike[str],
        out_ivf: str | os.PathLike[str],
        crf: int,
        *,
        timed: bool = False,
        cpuset_core: int | None = None,
    ) -> list[str]:
        """Return a native command or complete Docker command for one encode."""

        if self.mode == "native":
            return encoder_argv(binary, in_y4m, out_ivf, self.config.encoder_args, crf)
        command = [
            "cpu-wrap",
            *encoder_argv(
                self._work_container_path(binary),
                "/input/src.y4m",
                self._work_container_path(out_ivf),
                self.config.encoder_args,
                crf,
            ),
        ]
        return self._docker(
            command,
            mounts=self._encode_mounts(in_y4m),
            cpuset_core=cpuset_core,
        )

    def _checked(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None,
        timeout_s: float,
        failure_gate: str | None = None,
        timeout_gate: str | None = None,
    ) -> _CommandResult:
        try:
            result = self._execute(argv, cwd=cwd, timeout_s=timeout_s)
        except subprocess.TimeoutExpired as exc:
            detail = "wall timeout"
            if timeout_gate is not None:
                raise GateError(timeout_gate, detail) from exc
            raise RuntimeError(f"command timed out after {timeout_s:g}s: {argv[0]}") from exc
        if result.returncode != 0:
            detail = _tail(result.stderr, result.stdout)
            if failure_gate is not None:
                raise GateError(failure_gate, detail)
            raise RuntimeError(
                f"command failed with exit code {result.returncode}: {argv[0]}\n{detail}"
            )
        return result

    def _execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None,
        timeout_s: float,
    ) -> _CommandResult:
        """Injection seam used by argv tests; production delegates to the executor."""

        return _execute(argv, cwd=cwd, timeout_s=timeout_s)

    def build_encoder(self, src_dir: str | os.PathLike[str], label: str) -> Path:
        """Copy and build an encoder source tree in this run's work directory."""

        destination = self.work_dir / f"{label}_src"
        if destination.is_symlink():
            destination.unlink()
        elif destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(
            Path(src_dir).expanduser().resolve(),
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "Bin"),
        )
        for command in self.build_commands(destination):
            self._checked(
                command,
                cwd=destination if self.mode == "native" else None,
                timeout_s=float(self.config.build_timeout_s),
            )
        binary = destination / self.config.encoder_binary_relpath
        if not binary.is_file():
            raise RuntimeError(f"built encoder binary is missing: {binary}")
        return binary

    def encode(
        self,
        binary: str | os.PathLike[str],
        in_y4m: str | os.PathLike[str],
        out_ivf: str | os.PathLike[str],
        crf: int,
        wall_timeout_s: float,
    ) -> float | None:
        """Encode one clip and return process-tree CPU seconds when available."""

        output = Path(out_ivf)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = self.encode_command(
            binary,
            in_y4m,
            output,
            crf,
            timed=self.mode == "docker",
        )
        time_format = None
        if self.mode == "native":
            native_time = _native_time_command(command)
            if native_time is not None:
                command, time_format = native_time
            else:
                _warn_native_cpu_unavailable("/usr/bin/time is unsupported or unavailable")
        result = self._checked(
            command,
            cwd=self.work_dir if self.mode == "native" else None,
            timeout_s=wall_timeout_s,
            timeout_gate="speed",
        )
        if self.mode == "docker":
            return _parse_cgroup_cpu(result.stderr)
        if time_format == "time-v":
            try:
                return _parse_time_v(result.stderr)
            except RuntimeError:
                _warn_native_cpu_unavailable("cannot parse /usr/bin/time -v output")
                return None
        if time_format == "time-l":
            try:
                return _parse_time_l(result.stderr)
            except RuntimeError:
                _warn_native_cpu_unavailable("cannot parse /usr/bin/time -l output")
                return None
        return None

    def timed_encode(
        self,
        binary: str | os.PathLike[str],
        in_y4m: str | os.PathLike[str],
        out_ivf: str | os.PathLike[str],
        crf: int,
        wall_timeout_s: float,
        cpuset_core: int | None = None,
    ) -> float:
        """Encode one clip and return user plus system CPU seconds."""

        output = Path(out_ivf)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = self.encode_command(
            binary,
            in_y4m,
            output,
            crf,
            timed=self.mode == "docker",
            cpuset_core=cpuset_core,
        )
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        result = self._checked(
            command,
            cwd=self.work_dir if self.mode == "native" else None,
            timeout_s=wall_timeout_s,
            timeout_gate="speed",
        )
        if self.mode == "native":
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
        return _parse_cgroup_cpu(result.stderr)

    def decode(
        self,
        in_ivf: str | os.PathLike[str],
        out_y4m: str | os.PathLike[str],
    ) -> None:
        """Decode an IVF bitstream with dav1d, mapping all failures to conformance."""

        output = Path(out_y4m)
        output.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "docker":
            command = self._docker(
                [
                    "dav1d",
                    "-i",
                    self._work_container_path(in_ivf),
                    "-o",
                    self._work_container_path(output),
                ],
                mounts=self._work_mounts(),
                memory_gb=6,
            )
            cwd = None
        else:
            command = ["dav1d", "-i", os.fspath(in_ivf), "-o", os.fspath(output)]
            cwd = self.work_dir
        self._checked(
            command,
            cwd=cwd,
            timeout_s=DECODE_TIMEOUT_S,
            failure_gate="conformance",
            timeout_gate="conformance",
        )

    def compute_psnr(
        self,
        src: str | os.PathLike[str],
        dec: str | os.PathLike[str],
        frames: int,
    ) -> dict[str, float]:
        """Compute clip PSNR natively or in the pinned container."""

        if self.mode == "native":
            from harness.metrics import clip_psnr

            return clip_psnr(src, dec, frames)
        command = self._docker(
            [
                "python3",
                "/harness/xcompute.py",
                "psnr",
                "--src",
                "/input/src.y4m",
                "--dec",
                self._work_container_path(dec),
                "--frames",
                str(frames),
            ],
            mounts=self._xcompute_mounts(src),
        )
        result = self._checked(command, cwd=None, timeout_s=DECODE_TIMEOUT_S)
        parsed = json.loads(result.stdout)
        if not isinstance(parsed, dict):
            raise RuntimeError("xcompute PSNR output is not a JSON object")
        return {str(key): float(value) for key, value in parsed.items()}

    def compute_bd(
        self,
        anchor_points: Sequence[Sequence[float]],
        candidate_points: Sequence[Sequence[float]],
    ) -> float:
        """Compute one BD-rate natively or in the pinned container."""

        if self.mode == "native":
            from harness.bdrate import bd_rate

            return float(bd_rate(anchor_points, candidate_points))

        command = self._docker(
            [
                "python3",
                "/harness/xcompute.py",
                "bd",
                "--anchor-json",
                json.dumps(anchor_points, separators=(",", ":")),
                "--candidate-json",
                json.dumps(candidate_points, separators=(",", ":")),
            ],
            mounts=self._xcompute_mounts(),
        )
        try:
            result = self._execute(command, cwd=None, timeout_s=DECODE_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("BD-rate compute wall timeout") from exc
        if result.returncode == 2:
            raise GateError("rd-validity", _tail(result.stderr, result.stdout))
        if result.returncode != 0:
            raise RuntimeError(
                "BD-rate compute failed with exit code "
                f"{result.returncode}: {_tail(result.stderr, result.stdout)}"
            )
        try:
            parsed = json.loads(result.stdout)
            value = parsed.get("bd_rate") if isinstance(parsed, dict) else parsed
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"invalid BD-rate value {value!r}")
            return float(value)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid BD-rate compute output: {exc}") from exc

    def vmaf(
        self,
        src: str | os.PathLike[str],
        dec: str | os.PathLike[str],
        model: str,
    ) -> dict[str, float] | None:
        """Compute optional guard metrics; guard failures only produce a warning."""

        output: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix="vmaf_", suffix=".json", dir=self.work_dir
            )
            os.close(descriptor)
            output = Path(raw_path)
            if self.mode == "docker":
                command = self._docker(
                    [
                        "vmaf",
                        "-r",
                        "/input/src.y4m",
                        "-d",
                        self._work_container_path(dec),
                        "--model",
                        model,
                        "--feature",
                        "float_ssim",
                        "--json",
                        "-o",
                        self._work_container_path(output),
                    ],
                    mounts=(
                        *self._work_mounts(),
                        DockerMount(Path(src), "/input/src.y4m", True),
                    ),
                )
                cwd = None
            else:
                command = [
                    "vmaf",
                    "-r",
                    os.fspath(src),
                    "-d",
                    os.fspath(dec),
                    "--model",
                    model,
                    "--feature",
                    "float_ssim",
                    "--json",
                    "-o",
                    str(output),
                ]
                cwd = self.work_dir
            result = self._execute(command, cwd=cwd, timeout_s=DECODE_TIMEOUT_S)
            if result.returncode != 0:
                raise RuntimeError(_tail(result.stderr, result.stdout))
            data = json.loads(output.read_text(encoding="utf-8"))
            pooled = data["pooled_metrics"]
            return {
                "vmaf_mean": float(pooled["vmaf"]["mean"]),
                "ssim_mean": float(pooled["float_ssim"]["mean"]),
            }
        except Exception as exc:  # Guard metrics are explicitly best effort.
            print(f"WARNING vmaf guard unavailable: {exc}")
            return None
        finally:
            if output is not None:
                output.unlink(missing_ok=True)
