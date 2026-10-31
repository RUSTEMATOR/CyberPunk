"""The Unity client's build line on the owner's Mac (owner request 31.10, docs/PLAN_UNITY.md, phase 1).

The bot looks for `data/unity.request` every few seconds: {"id": "<any>", "job": "probe" | "build"}. Only these fixed
jobs run, nothing from the request reaches a command line except the id (used in file names after a strict filter).

- probe: is the editor there, its WebGL module, a licence, the project; answer in data/unity/probe.json.
- build: create the project if it is missing (Unity -createProject), copy the sources from unity/src/Assets into it,
  build WebGL with Unity in batchmode into webapp/unity/<version>/, point webapp/unity/current.json at it and keep the
  last three versions. The log goes to data/unity/build-<version>.log, the outcome to data/unity/result.json.

While a job runs the restart flag waits (app/main.py::_restart_watch asks `busy()`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import BASE_DIR

log = logging.getLogger(__name__)

REQUEST = BASE_DIR / "data" / "unity.request"
OUT = BASE_DIR / "data" / "unity"
SRC = BASE_DIR / "unity" / "src" / "Assets"
PROJECT = BASE_DIR / "unity" / "NightCity3D"
WEB = BASE_DIR / "webapp" / "unity"
EDITOR = Path(os.environ.get("UNITY_EDITOR")
              or "/Applications/Unity/Hub/Editor/6000.6.7f1/Unity.app/Contents/MacOS/Unity")
BUILD_METHOD = "NC.EditorTools.NcBuild.WebGL"
KEEP_VERSIONS = 3
CREATE_TIMEOUT_S = 20 * 60
BUILD_TIMEOUT_S = 60 * 60
# the folders of the sources the build line owns inside the project: removed and copied again on every build
OWNED = ("NC", "WebGLTemplates")

_task: asyncio.Task | None = None


def busy() -> bool:
    return _task is not None and not _task.done()


def _write(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT / name)


def _tail(path: Path, lines: int = 60) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


async def watch() -> None:
    """The scheduler job: pick up a request, start its job in the background, return at once."""
    global _task
    if busy() or not REQUEST.exists():
        return
    try:
        req = json.loads(REQUEST.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        req = {}
    try:
        REQUEST.unlink()
    except OSError as exc:
        log.warning("unity: cannot remove the request, ignoring it: %s", exc)
        return
    job = str(req.get("job") or "")
    rid = re.sub(r"[^A-Za-z0-9_-]", "", str(req.get("id") or ""))[:40] or str(int(time.time()))
    if job not in ("probe", "build", "blender_install", "blender_models"):
        _write("result.json", {"id": rid, "job": job, "ok": False, "error": "unknown job"})
        return
    _write("status.json", {"id": rid, "job": job, "state": "running", "since": time.time()})
    _task = asyncio.create_task(_run(rid, job))


async def _run(rid: str, job: str) -> None:
    t0 = time.time()
    try:
        res = await (_probe() if job == "probe" else _blender_install() if job == "blender_install"
                     else _blender_models() if job == "blender_models" else _build())
    except Exception as exc:  # the job must never take the bot down
        log.exception("unity: %s failed", job)
        res = {"ok": False, "error": repr(exc)}
    res.update(id=rid, job=job, secs=int(time.time() - t0), at=time.time())
    _write("result.json", res)
    _write("status.json", {"id": rid, "job": job, "state": "done", "ok": res.get("ok")})
    log.warning("unity: %s %s ok=%s in %ss", job, rid, res.get("ok"), res["secs"])


def _licence() -> list[str]:
    home = Path.home()
    spots = [home / "Library/Application Support/Unity/licenses", Path("/Library/Application Support/Unity/licenses"),
             Path("/Library/Application Support/Unity/Unity_lic.ulf"), home / "Library/Unity/licenses"]
    return [str(p) for p in spots if p.exists()]


async def _probe() -> dict[str, Any]:
    root = EDITOR.parents[3] if len(EDITOR.parents) > 3 else EDITOR.parent
    engines = root / "PlaybackEngines"
    return {
        "ok": EDITOR.exists(),
        "editor": str(EDITOR), "editor_exists": EDITOR.exists(),
        "engines": sorted(p.name for p in engines.iterdir()) if engines.is_dir() else [],
        "licence_spots": _licence(),
        "project": PROJECT.joinpath("ProjectSettings").is_dir(),
        "sources": SRC.is_dir(),
        "versions": _versions(),
        "blender": str(BLENDER) if BLENDER.exists() else None,
        "brew": str(BREW) if BREW.exists() else None,
    }


# -- Blender (owner request 31.10): installed by the bot with Homebrew, later run in the background for models ---------
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
BREW = Path("/opt/homebrew/bin/brew")
BREW_TIMEOUT_S = 40 * 60


async def _blender_install() -> dict[str, Any]:
    """`brew install --cask blender`, the output into data/unity/blender-install.log. Nothing to do when it is there."""
    if BLENDER.exists():
        return {"ok": True, "already": True, "blender": str(BLENDER)}
    if not BREW.exists():
        return {"ok": False, "error": f"no Homebrew at {BREW}"}
    OUT.mkdir(parents=True, exist_ok=True)
    logfile = OUT / "blender-install.log"
    env = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOMEBREW_NO_AUTO_UPDATE": "1",
           "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_ENV_HINTS": "1", "NONINTERACTIVE": "1"}
    with logfile.open("w", encoding="utf-8") as f:
        proc = await asyncio.create_subprocess_exec(str(BREW), "install", "--cask", "blender", stdout=f, stderr=f,
                                                    stdin=asyncio.subprocess.DEVNULL, env=env, start_new_session=True)
        try:
            code = await asyncio.wait_for(proc.wait(), BREW_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            code = -9
    return {"ok": code == 0 and BLENDER.exists(), "code": code, "blender": str(BLENDER) if BLENDER.exists() else None,
            "log": str(logfile), "tail": _tail(logfile, 25)}


async def _exec(args: list[str], logfile: Path, timeout: int) -> int:
    """Run the editor detached from the bot's process group (a restart of the bot must not kill a build)."""
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    try:
        return await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with logfile.open("a", encoding="utf-8") as f:
            f.write(f"\n[unity_jobs] killed after {timeout} s\n")
        return -9


def _sync_sources() -> None:
    """unity/src/Assets/<owned folder> replace the same folders of the project (with their .meta files)."""
    assets = PROJECT / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name in OWNED:
        dst = assets / name
        if dst.exists():
            shutil.rmtree(dst)
        meta = assets / (name + ".meta")
        if meta.exists():
            meta.unlink()
        if (SRC / name).is_dir():
            shutil.copytree(SRC / name, dst)


def _versions() -> list[str]:
    if not WEB.is_dir():
        return []
    return sorted(p.name for p in WEB.iterdir() if p.is_dir() and re.fullmatch(r"u\d{10}", p.name))


def _prune(current: str) -> None:
    olds = [v for v in _versions() if v != current]
    for v in olds[:max(0, len(olds) - (KEEP_VERSIONS - 1))]:
        shutil.rmtree(WEB / v, ignore_errors=True)


def _build_info(folder: Path) -> dict[str, Any]:
    try:
        return json.loads((folder / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


async def _build() -> dict[str, Any]:
    if not EDITOR.exists():
        return {"ok": False, "error": f"no editor at {EDITOR}"}
    if not SRC.is_dir():
        return {"ok": False, "error": "no sources in unity/src/Assets"}
    OUT.mkdir(parents=True, exist_ok=True)
    version = "u" + datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%y%m%d%H%M")
    if not PROJECT.joinpath("ProjectSettings").is_dir():
        PROJECT.parent.mkdir(parents=True, exist_ok=True)
        clog = OUT / f"create-{version}.log"
        code = await _exec([str(EDITOR), "-batchmode", "-quit", "-createProject", str(PROJECT), "-logFile", str(clog)],
                           clog, CREATE_TIMEOUT_S)
        if code != 0 or not PROJECT.joinpath("ProjectSettings").is_dir():
            return {"ok": False, "stage": "create", "code": code, "log": str(clog), "tail": _tail(clog)}
    _sync_sources()
    # owner report 31.10 (first builds): an incremental build kept the old C# in the wasm; the build caches go every time
    for cache in ("Bee", "ScriptAssemblies", "PlayerDataCache", "Il2cppBuildCache"):
        shutil.rmtree(PROJECT / "Library" / cache, ignore_errors=True)
    out = WEB / version
    blog = OUT / f"build-{version}.log"
    code = await _exec([str(EDITOR), "-batchmode", "-quit", "-projectPath", str(PROJECT), "-buildTarget", "WebGL",
                        "-executeMethod", BUILD_METHOD, "-ncOut", str(out), "-ncVersion", version, "-logFile", str(blog)],
                       blog, BUILD_TIMEOUT_S)
    if code != 0 or not (out / "index.html").exists():
        shutil.rmtree(out, ignore_errors=True)
        return {"ok": False, "stage": "build", "code": code, "version": version, "log": str(blog), "tail": _tail(blog)}
    tmp = WEB / "current.json.tmp"
    tmp.write_text(json.dumps({"v": version, "at": time.time(), **_build_info(out)}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(WEB / "current.json")
    _prune(version)
    return {"ok": True, "version": version, "log": str(blog), "build": _build_info(out), "tail": _tail(blog, 15)}


MODELS_SCRIPT = BASE_DIR / "unity" / "blender" / "make_models.py"
MODELS_OUT = SRC / "NC" / "Models"


async def _blender_models() -> dict[str, Any]:
    """Blender in the background runs unity/blender/make_models.py: FBX models into unity/src/Assets/NC/Models, picked up
    by the next build (owner request 31.10, docs/PLAN_FIGHT3D.md phase 3)."""
    if not BLENDER.exists():
        return {"ok": False, "error": f"no Blender at {BLENDER}"}
    if not MODELS_SCRIPT.exists():
        return {"ok": False, "error": "no unity/blender/make_models.py"}
    OUT.mkdir(parents=True, exist_ok=True)
    MODELS_OUT.mkdir(parents=True, exist_ok=True)
    logfile = OUT / "blender-models.log"
    with logfile.open("w", encoding="utf-8") as f:
        proc = await asyncio.create_subprocess_exec(str(BLENDER), "-b", "--factory-startup", "--python", str(MODELS_SCRIPT),
                                                    "--", str(MODELS_OUT), stdout=f, stderr=f,
                                                    stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
        try:
            code = await asyncio.wait_for(proc.wait(), 10 * 60)
        except asyncio.TimeoutError:
            proc.kill()
            code = -9
    made = sorted(p.name for p in MODELS_OUT.glob("*.fbx"))
    return {"ok": code == 0 and bool(made), "code": code, "models": made, "log": str(logfile), "tail": _tail(logfile, 25)}
