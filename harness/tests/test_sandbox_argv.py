from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import harness.sandbox as sandbox
from harness.gates import GateError


def _config(repo: Path, image: str = "toolchain@example") -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=repo,
        work_dir=repo / "work",
        cache_dir=repo / "cache",
        toolchain_image=image,
        build_timeout_s=1200,
        encoder_binary_relpath="Bin/Release/SvtAv1EncApp",
        encoder_args=("--preset", "6", "-n", "64"),
    )


def _docker_prefix(image: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--cgroupns=private",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        "501:20",
        "--pids-limit",
        "512",
        "--memory",
        "6g",
        "--memory-swap",
        "6g",
    ]


def test_docker_build_argv_has_only_source_mount(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "source"
    source.mkdir(parents=True)
    (source / "CMakeLists.txt").write_text("", encoding="utf-8")
    stale_bin = source / "Bin" / "old"
    stale_bin.mkdir(parents=True)
    (stale_bin / "marker").write_text("stale", encoding="utf-8")
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner("docker", config)
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        if len(calls) == 2:
            binary = repo / "work/candidate_src/Bin/Release/SvtAv1EncApp"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"built")
        return sandbox._CommandResult("", "", 0)

    monkeypatch.setattr(runner, "_execute", fake_execute)
    binary = runner.build_encoder(source, "candidate")
    copied = repo / "work/candidate_src"

    assert binary == copied / "Bin/Release/SvtAv1EncApp"
    assert not (copied / "Bin/old/marker").exists()
    expected_prefix = _docker_prefix(config.toolchain_image) + [
        "-v",
        f"{copied}:/src:rw",
        config.toolchain_image,
    ]
    assert calls == [
        expected_prefix
        + [
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
        expected_prefix + ["ninja", "-C", "/src/build_hn"],
    ]
    assert all("/corpus" not in " ".join(call) for call in calls)
    assert all("--network=none" in call for call in calls)


def test_build_copy_preserves_source_symlinks(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "source"
    source.mkdir(parents=True)
    outside = tmp_path / "host-secret"
    outside.write_text("must not be copied", encoding="utf-8")
    (source / "external-link").symlink_to(outside)
    config = _config(repo)
    runner = sandbox.Runner("native", config)

    def fake_execute(argv, *, cwd, timeout_s):
        if argv[0] == "ninja":
            binary = repo / "work/candidate_src/Bin/Release/SvtAv1EncApp"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"built")
        return sandbox._CommandResult("", "", 0)

    monkeypatch.setattr(runner, "_execute", fake_execute)
    runner.build_encoder(source, "candidate")

    copied_link = repo / "work/candidate_src/external-link"
    assert copied_link.is_symlink()
    assert copied_link.readlink() == outside


def test_docker_encode_argv_is_isolated_and_uses_neutral_input(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    harness_dir = repo / "harness"
    work = repo / "work"
    corpus = repo / "cache/corpus"
    harness_dir.mkdir(parents=True)
    binary = work / "candidate_src/Bin/Release/SvtAv1EncApp"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    source = corpus / "clip.y4m"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"YUV4MPEG2")
    output = work / "streams/clip-39.ivf"
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner("docker", config)
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        assert cwd is None
        assert timeout_s == 12.5
        return sandbox._CommandResult(
            "",
            "CGROUP_CPU_USEC 1750000\n",
            0,
        )

    monkeypatch.setattr(runner, "_execute", fake_execute)
    cpu_seconds = runner.encode(binary, source, output, 39, 12.5)

    assert cpu_seconds == 1.75
    assert calls == [
        _docker_prefix(config.toolchain_image)
        + [
            "-v",
            f"{work}:/work:rw",
            "-v",
            f"{source}:/input/src.y4m:ro",
            config.toolchain_image,
            "cpu-wrap",
            "/work/candidate_src/Bin/Release/SvtAv1EncApp",
            "-i",
            "/input/src.y4m",
            "-b",
            "/work/streams/clip-39.ivf",
            "--preset",
            "6",
            "-n",
            "64",
            "--crf",
            "39",
        ]
    ]
    mount_targets = [
        value.rsplit(":", 1)[0].rsplit(":", 1)[-1]
        for index, value in enumerate(calls[0])
        if index > 0 and calls[0][index - 1] == "-v"
    ]
    assert mount_targets == ["/work", "/input/src.y4m"]
    assert "/corpus" not in mount_targets
    assert "/harness" not in mount_targets


def test_cgroup_cpu_parser_requires_exactly_one_marker():
    assert sandbox._parse_cgroup_cpu("noise\nCGROUP_CPU_USEC 2500000\n") == 2.5
    # Tolerate a stray literal backslash-n after the count (marker-format wrinkle)
    assert sandbox._parse_cgroup_cpu("CGROUP_CPU_USEC 2105572\\n") == 2.105572
    for stderr in (
        "no accounting marker\n",
        "CGROUP_CPU_USEC 1\nCGROUP_CPU_USEC 2500000\n",
    ):
        with pytest.raises(RuntimeError, match="exactly one CGROUP_CPU_USEC"):
            sandbox._parse_cgroup_cpu(stderr)


def test_time_v_parser_uses_last_labeled_values():
    stderr = (
        "User time (seconds): 99.0\n"
        "System time (seconds): 88.0\n"
        "\tUser time (seconds): 1.25\n"
        "\tSystem time (seconds): 0.50\n"
    )

    assert sandbox._parse_time_v(stderr) == 1.75


@pytest.mark.parametrize(
    "stderr",
    [
        "        2.00 real         1.25 user         0.50 sys\n",
        "1.25 user\n0.50 sys\n",
    ],
)
def test_time_l_parser_handles_compact_and_labeled_lines(stderr):
    assert sandbox._parse_time_l(stderr) == 1.75


def test_time_parsers_reject_missing_fields():
    with pytest.raises(RuntimeError, match="System time"):
        sandbox._parse_time_v("User time (seconds): 1.0\n")
    with pytest.raises(RuntimeError, match="sys"):
        sandbox._parse_time_l("1.0 user\n")


@pytest.mark.parametrize(
    ("platform", "flag", "format_name"),
    [("darwin", "-l", "time-l"), ("linux", "-v", "time-v")],
)
def test_native_time_command_uses_platform_format(
    monkeypatch, platform, flag, format_name
):
    monkeypatch.setattr(sandbox.sys, "platform", platform)
    monkeypatch.setattr(sandbox.Path, "is_file", lambda _path: True)
    monkeypatch.setattr(sandbox.os, "access", lambda _path, _mode: True)

    assert sandbox._native_time_command(["encoder", "--flag"]) == (
        ["/usr/bin/time", flag, "encoder", "--flag"],
        format_name,
    )


def test_native_darwin_encode_parses_per_process_cpu(tmp_path, monkeypatch):
    runner = sandbox.Runner("native", _config(tmp_path))
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult(
            "", "2.00 real         1.25 user         0.50 sys\n", 0
        )

    monkeypatch.setattr(runner, "_execute", fake_execute)
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.Path, "is_file", lambda _path: True)
    monkeypatch.setattr(sandbox.os, "access", lambda _path, _mode: True)

    cpu_seconds = runner.encode(
        tmp_path / "encoder",
        tmp_path / "clip.y4m",
        tmp_path / "out.ivf",
        39,
        1.0,
    )

    assert cpu_seconds == 1.75
    assert calls[0][:3] == ["/usr/bin/time", "-l", str(tmp_path / "encoder")]


def test_native_encode_unavailable_timer_warns_once(tmp_path, monkeypatch, capsys):
    runner = sandbox.Runner("native", _config(tmp_path))
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult("", "", 0)

    monkeypatch.setattr(runner, "_execute", fake_execute)
    monkeypatch.setattr(sandbox.sys, "platform", "unsupported")
    monkeypatch.setattr(sandbox, "_native_cpu_warning_printed", False)
    binary = tmp_path / "encoder"
    source = tmp_path / "clip.y4m"

    assert runner.encode(binary, source, tmp_path / "one.ivf", 39, 1.0) is None
    assert runner.encode(binary, source, tmp_path / "two.ivf", 39, 1.0) is None
    assert calls[0][0] == str(binary)
    assert capsys.readouterr().out.count("WARNING per-encode CPU accounting unavailable") == 1


def test_docker_encode_requires_cpu_wrap_output(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    binary = repo / "work/encoder"
    binary.parent.mkdir(parents=True)
    source = repo / "cache/corpus/clip.y4m"
    source.parent.mkdir(parents=True)
    runner = sandbox.Runner("docker", _config(repo))
    monkeypatch.setattr(
        runner,
        "_execute",
        lambda argv, *, cwd, timeout_s: sandbox._CommandResult("", "", 0),
    )

    with pytest.raises(RuntimeError, match="CGROUP_CPU_USEC"):
        runner.encode(binary, source, repo / "work/out.ivf", 39, 1.0)


def test_docker_timing_adds_cpuset_and_cpu_wrapper(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    binary = repo / "work/anchor_src/Bin/Release/SvtAv1EncApp"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    source = repo / "cache/corpus/clip.y4m"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"YUV4MPEG2")
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner("docker", config)
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult(
            "",
            "CGROUP_CPU_USEC 1750000\n",
            0,
        )

    monkeypatch.setattr(runner, "_execute", fake_execute)
    cpu_seconds = runner.timed_encode(
        binary, source, repo / "work/timing.ivf", 39, 30.0, cpuset_core=3
    )

    assert cpu_seconds == 1.75
    assert "--cpuset-cpus=3" in calls[0]
    image_index = calls[0].index(config.toolchain_image)
    assert calls[0][image_index + 1] == "cpu-wrap"


def test_docker_decode_mounts_only_work(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    stream = repo / "work/clip.ivf"
    stream.parent.mkdir(parents=True)
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner("docker", config)
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult("", "", 0)

    monkeypatch.setattr(runner, "_execute", fake_execute)
    runner.decode(stream, repo / "work/clip.y4m")

    assert f"{repo / 'work'}:/work:rw" in calls[0]
    assert all(":/corpus:" not in value for value in calls[0])
    assert all(":/harness:" not in value for value in calls[0])
    assert ["-i", "/work/clip.ivf", "-o", "/work/clip.y4m"] == calls[0][-4:]


def test_docker_psnr_mounts_harness_and_neutral_source(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    source = repo / "cache/corpus/descriptive-name.y4m"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"YUV4MPEG2")
    decoded = repo / "work/decoded.y4m"
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner("docker", config)
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult(
            '{"psnr_y":40,"psnr_cb":41,"psnr_cr":42,"psnr_yuv":40.375}',
            "",
            0,
        )

    monkeypatch.setattr(runner, "_execute", fake_execute)
    result = runner.compute_psnr(source, decoded, 64)

    assert result["psnr_yuv"] == 40.375
    assert f"{repo / 'work'}:/work:rw" in calls[0]
    assert f"{repo / 'harness'}:/harness:ro" in calls[0]
    assert f"{source}:/input/src.y4m:ro" in calls[0]
    source_option = calls[0].index("--src")
    assert calls[0][source_option + 1] == "/input/src.y4m"
    assert all(":/corpus:" not in value for value in calls[0])


def test_pending_toolchain_image_is_rejected(tmp_path):
    config = _config(tmp_path, image="registry/image@sha256:PENDING")
    try:
        sandbox.Runner("docker", config)
    except RuntimeError as exc:
        assert str(exc) == "toolchain image not yet published"
    else:
        raise AssertionError("PENDING Docker image was accepted")


def test_docker_bd_preserves_rd_validity_gate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner("docker", config)
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult("", "no overlap between RD quality ranges\n", 2)

    monkeypatch.setattr(runner, "_execute", fake_execute)
    points = [(100.0, 30.0), (200.0, 31.0), (300.0, 32.0), (400.0, 33.0)]
    with pytest.raises(GateError) as caught:
        runner.compute_bd(points, points)

    assert caught.value.gate == "rd-validity"
    assert caught.value.detail == "no overlap between RD quality ranges"
    anchor_option = calls[0].index("--anchor-json")
    candidate_option = calls[0].index("--candidate-json")
    expected = "[[100.0,30.0],[200.0,31.0],[300.0,32.0],[400.0,33.0]]"
    assert calls[0][anchor_option + 1] == expected
    assert calls[0][candidate_option + 1] == expected
    assert f"{repo / 'work'}:/work:rw" in calls[0]
    assert f"{repo / 'harness'}:/harness:ro" in calls[0]
    assert all(":/corpus:" not in value for value in calls[0])


def test_docker_candidate_mount_protects_anchor_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    work = repo / "work"
    anchor_tree = work / "anchor_src"
    anchor_tree.mkdir(parents=True)
    candidate = work / "candidate_src/Bin/Release/SvtAv1EncApp"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"binary")
    source = repo / "cache/corpus/clip.y4m"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"YUV4MPEG2")
    config = _config(repo)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox.os, "getgid", lambda: 20)
    runner = sandbox.Runner(
        "docker", config, protected_paths=(anchor_tree,)
    )
    calls: list[list[str]] = []

    def fake_execute(argv, *, cwd, timeout_s):
        calls.append(list(argv))
        return sandbox._CommandResult(
            "",
            "CGROUP_CPU_USEC 120000\n",
            0,
        )

    monkeypatch.setattr(runner, "_execute", fake_execute)
    runner.encode(candidate, source, work / "clip.ivf", 39, 10.0)

    mount = f"{anchor_tree}:/work/anchor_src:ro"
    mount_index = calls[0].index(mount)
    assert calls[0][mount_index - 1 : mount_index + 1] == ["-v", mount]


def test_docker_timeout_kills_container_by_cid(tmp_path, monkeypatch):
    image = "toolchain@example"
    command = sandbox.docker_run_argv(image, ["encoder"], uid=501, gid=20)
    killed: list[list[str]] = []

    class FakeProcess:
        pid = 1234
        returncode = -9
        calls = 0

        def __init__(self, argv, **_kwargs):
            self.argv = argv

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                cidfile = Path(self.argv[self.argv.index("--cidfile") + 1])
                cidfile.write_text("container-id", encoding="utf-8")
                raise subprocess.TimeoutExpired(self.argv, timeout)
            return "", ""

    monkeypatch.setattr(sandbox.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(sandbox.os, "killpg", lambda *_args: None)

    def fake_run(argv, **_kwargs):
        killed.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        sandbox._execute(command, cwd=None, timeout_s=0.01)

    assert killed == [["docker", "kill", "container-id"]]


def test_vmaf_tempfile_failure_is_non_gating(tmp_path, monkeypatch, capsys):
    runner = sandbox.Runner("native", _config(tmp_path))

    def fail_mkstemp(**_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(sandbox.tempfile, "mkstemp", fail_mkstemp)

    assert runner.vmaf(tmp_path / "src.y4m", tmp_path / "dec.y4m", "model") is None
    assert "WARNING vmaf guard unavailable" in capsys.readouterr().out
