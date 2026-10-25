"""Jianwei's local Python setup and launcher; no research or data logic."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "skills/tanyuan/requirements.txt"


def state_home() -> Path:
    value = os.environ.get("JIANWEI_HOME")
    path = Path(value).expanduser() if value else Path.home() / ".local/share/jianwei"
    if not path.is_absolute():
        raise ValueError("JIANWEI_HOME 必须是绝对路径。")
    if path.is_symlink():
        raise ValueError("JIANWEI_HOME 不能是符号链接。")
    return path


def dependency_id() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()[:16]


def managed_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("运行环境目录不能是符号链接。")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("运行环境位置不是目录。")


def read_pointer(path: Path) -> str | None:
    if path.is_symlink():
        raise ValueError("运行环境记录不能是符号链接。")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("运行环境记录无效。")
    value = path.read_text(encoding="utf-8").strip()
    if not value or not Path(value).is_absolute():
        raise ValueError("运行环境记录必须是非空绝对路径。")
    return value


def write_pointer(path: Path, value: str) -> None:
    if path.is_symlink():
        raise ValueError("不能覆盖链接记录。")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".jianwei-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(value + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def interpreter(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("Python 必须是可执行文件的绝对路径。")
    # Keep the venv path: resolving its symlink would select the base interpreter.
    return candidate.absolute()


def verify_python(python: Path) -> None:
    pinned = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==")
            pinned.extend((name, version))
    check = (
        "import sys, importlib.metadata as m; "
        "assert sys.version_info >= (3, 10), 'Python 3.10+ required'; "
        "pairs=list(zip(sys.argv[1::2],sys.argv[2::2])); "
        "assert all(m.version(n)==v for n,v in pairs), 'Dependency version mismatch'; "
        "import requests, pypdf"
    )
    result = subprocess.run([str(python), "-c", check, *pinned], capture_output=True,
                            text=True, timeout=60, check=False)
    if result.returncode:
        raise ValueError("Python 需要 3.10+，且已安装 requirements.txt 中的精确依赖版本。")


def setup(python: str | None, env_file: str | None) -> None:
    home = state_home()
    managed_dir(home)
    runtimes = home / "runtimes"
    managed_dir(runtimes)
    lock = home / ".setup-lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("另一个初始化正在运行；若上次意外中断，请确认没有初始化进程后移除 .setup-lock 再试。") from None
    try:
        pointer = runtimes / (dependency_id() + ".python")
        selected = python or read_pointer(pointer)
        if selected:
            executable = interpreter(selected)
            verify_python(executable)
        else:
            target = runtimes / (dependency_id() + f"-py{sys.version_info.major}.{sys.version_info.minor}")
            if target.is_symlink():
                raise ValueError("虚拟环境位置不能是符号链接。")
            if target.exists() and not (target / ".jianwei-runtime").is_file():
                raise ValueError("虚拟环境位置已占用，未覆盖。请另选 JIANWEI_HOME。")
            if not target.exists():
                target.mkdir(mode=0o700)
                (target / ".jianwei-runtime").write_text(dependency_id() + "\n", encoding="utf-8")
            subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
            executable = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([str(executable), "-m", "pip", "install", "--disable-pip-version-check",
                            "--index-url", "https://pypi.org/simple", "-r", str(REQUIREMENTS)], check=True)
            verify_python(executable)

        config_pointer = home / "env-file"
        previous = read_pointer(config_pointer)
        if env_file:
            config = Path(env_file).expanduser().resolve()
            if not config.is_file():
                raise ValueError("--env-file 必须指向已有的凭据文件；不会读取或复制其中的值。")
        elif previous:
            config = Path(previous)
            if not config.is_file():
                raise ValueError("已选凭据文件不存在；使用 --env-file 选择现有文件。")
        else:
            config = home / "config.env"
            if config.is_symlink():
                raise ValueError("默认凭据文件不能是符号链接。")
            if not config.exists():
                fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write((ROOT / ".env.example").read_text(encoding="utf-8"))
            if not config.is_file():
                raise ValueError("默认凭据位置不是文件，未覆盖。")
        write_pointer(config_pointer, str(config))
        write_pointer(pointer, str(executable))
        print("Jianwei Python 环境已就绪。凭据文件：" + str(config))
        print("请在本地编辑所需凭据；不要在聊天中粘贴。重新运行初始化不会覆盖已有凭据。")
    finally:
        lock.rmdir()


def launch() -> None:
    home = state_home()
    selected = os.environ.get("TANYUAN_PYTHON") or read_pointer(home / "runtimes" / (dependency_id() + ".python"))
    if not selected:
        command = "python3 " + shlex.quote(str(ROOT / "scripts/setup.py"))
        raise ValueError("首次使用请准备 Python 环境：\n" + command + "\n初始化完成后，在当前工作区重试原命令。")
    executable = interpreter(selected)
    env = dict(os.environ)
    env["TANYUAN_PYTHON"] = str(executable)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if not env.get("TANYUAN_ENV_FILE"):
        configured = read_pointer(home / "env-file")
        if configured:
            if not Path(configured).is_file():
                raise ValueError("所选凭据文件不存在；请重新运行 setup.py --env-file 选择文件。")
            env["TANYUAN_ENV_FILE"] = configured
    upstream = ROOT / "skills/tanyuan/bin/ty-upstream"
    os.execve(str(executable), [str(executable), str(upstream), *sys.argv[1:]], env)
