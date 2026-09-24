"""Application workflows via official scripting APIs (Blender bpy, Unreal Python,
Playwright for browsers, VS Code CLI) — preferred over blind clicking."""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

from ..computer_control.system import run_command

BUILTINS = {"jarvis-files": "jarvis.desktop.files", "jarvis-edit": "jarvis.desktop.editor",
            "jarvis-settings": "jarvis.desktop.settings", "jarvis-hud": "jarvis.desktop.hud"}


def launch_builtin(name: str, *args: str) -> dict:
    mod = BUILTINS[name]
    p = subprocess.Popen([sys.executable, "-m", mod, *args], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "pid": p.pid, "name": name}


# ------------------------------------------------------------------ Blender
def blender_path() -> str | None:
    return shutil.which("blender") or next(iter(glob.glob("/opt/blender*/blender")), None)


def run_blender(script: str, blend: str | None = None, background: bool = True, timeout: float = 600) -> dict:
    exe = blender_path()
    if not exe:
        return {"ok": False, "error": "Blender is not installed",
                "install_hint": "sudo apt install blender  (or official build from https://www.blender.org/download/)"}
    marker = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    wrapped = (
        "import bpy, json, sys, traceback, os\n"
        f"_M = {marker!r}\n_created = []\n"
        "def jarvis_saved(p):\n    _created.append(os.path.abspath(p))\n"
        "try:\n" + "\n".join("    " + l for l in script.splitlines()) + "\n"
        "    json.dump({'ok': True, 'files': _created}, open(_M, 'w'))\n"
        "except Exception as e:\n"
        "    json.dump({'ok': False, 'error': traceback.format_exc()}, open(_M, 'w'))\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(wrapped)
    argv = [exe] + (["-b"] if background else []) + ([blend] if blend else []) + ["--python", f.name]
    if not background:
        subprocess.Popen(argv, start_new_session=True)
        return {"ok": True, "interactive": True, "argv": argv}
    import shlex
    r = run_command(shlex.join(argv), timeout=timeout)
    try:
        res = json.loads(Path(marker).read_text() or "{}")
    except (OSError, json.JSONDecodeError):
        res = {"ok": False, "error": "Blender exited before the script finished:\n" + r["stderr"][-1500:]}
    res["files"] = [p for p in res.get("files", []) if Path(p).exists()]  # verify files really exist
    res["log"] = r["stdout"][-1500:]
    return res


BLENDER_VOXEL_ASSETS = r'''
# Realistic-Minecraft style asset kit: bevelled PBR voxel blocks + a small terrain scene.
import bpy, random, os
out_dir = os.path.expanduser(OUT_DIR)
os.makedirs(out_dir, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
def mat(name, base, rough, noise_scale):
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; bsdf = nt.nodes["Principled BSDF"]
    noise = nt.nodes.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = noise_scale
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = [c * 0.6 for c in base[:3]] + [1]
    ramp.color_ramp.elements[1].color = base
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.4
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"]); nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = rough
    return m
mats = {"grass": mat("Grass", (0.20, 0.45, 0.12, 1), 0.8, 18), "dirt": mat("Dirt", (0.35, 0.22, 0.12, 1), 0.95, 25),
        "stone": mat("Stone", (0.45, 0.45, 0.47, 1), 0.7, 12), "wood": mat("Wood", (0.42, 0.28, 0.14, 1), 0.6, 40),
        "leaves": mat("Leaves", (0.12, 0.35, 0.08, 1), 0.9, 30)}
def block(kind, loc):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object; o.name = f"{kind}_block"
    bev = o.modifiers.new("bevel", "BEVEL"); bev.width = 0.04; bev.segments = 2
    o.data.materials.append(mats[kind]); return o
random.seed(7)
for x in range(-6, 7):
    for y in range(-6, 7):
        hgt = 1 + int(1.5 + 1.5 * random.random())
        for z in range(hgt):
            block("grass" if z == hgt - 1 else ("dirt" if z > 0 else "stone"), (x, y, z))
for z in range(3, 7): block("wood", (2, 2, z))
for dx in (-1, 0, 1):
    for dy in (-1, 0, 1):
        for dz in (6, 7): block("leaves", (2 + dx, 2 + dy, dz))
bpy.ops.object.light_add(type="SUN", location=(10, -10, 20)); bpy.context.active_object.data.energy = 4
bpy.ops.object.camera_add(location=(16, -16, 14), rotation=(1.05, 0, 0.785)); bpy.context.scene.camera = bpy.context.active_object
blend = os.path.join(out_dir, "minecraft_realistic.blend"); bpy.ops.wm.save_as_mainfile(filepath=blend); jarvis_saved(blend)
fbx = os.path.join(out_dir, "minecraft_realistic.fbx"); bpy.ops.export_scene.fbx(filepath=fbx, apply_scale_options="FBX_SCALE_ALL"); jarvis_saved(fbx)
'''


def voxel_asset_script(out_dir: str = "~/JarvisProjects/minecraft") -> str:
    return BLENDER_VOXEL_ASSETS.replace("OUT_DIR", repr(out_dir))


# ------------------------------------------------------------------ Unreal
def unreal_path() -> str | None:
    for pat in ("UnrealEditor-Cmd", "~/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd",
                "/opt/UnrealEngine*/Engine/Binaries/Linux/UnrealEditor-Cmd",
                "~/Linux_Unreal_Engine*/Engine/Binaries/Linux/UnrealEditor-Cmd"):
        w = shutil.which(pat) or next(iter(glob.glob(os.path.expanduser(pat))), None)
        if w:
            return w
    return None


UNREAL_IMPORT = r'''
import unreal, os
fbx = os.path.expanduser(FBX)
task = unreal.AssetImportTask(); task.filename = fbx; task.destination_path = "/Game/Jarvis"
task.automated = True; task.save = True
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
unreal.EditorLevelLibrary.new_level("/Game/Jarvis/JarvisScene")
for p in task.imported_object_paths:
    a = unreal.EditorAssetLibrary.load_asset(p)
    if isinstance(a, unreal.StaticMesh):
        unreal.EditorLevelLibrary.spawn_actor_from_object(a, unreal.Vector(0, 0, 0))
unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1000), unreal.Rotator(-45, 0, 0))
unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 500))
unreal.EditorLevelLibrary.save_current_level()
print("JARVIS_IMPORTED", task.imported_object_paths)
'''


def run_unreal(project: str, script: str, timeout: float = 1800) -> dict:
    exe = unreal_path()
    if not exe:
        return {"ok": False, "error": "Unreal Engine is not installed",
                "install_hint": "Download Unreal Engine for Linux from https://www.unrealengine.com/linux (Epic account, ~100 GB, dedicated GPU recommended; not viable on a 4 GB RAM laptop)."}
    if not Path(project).expanduser().exists():
        return {"ok": False, "error": f"Unreal project {project} not found — create one from the editor first"}
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
    r = run_command(f"{exe!r} {str(Path(project).expanduser())!r} -run=pythonscript -script={f.name!r} -unattended -nosplash",
                    timeout=timeout)
    r["ok"] = r["ok"] and "JARVIS_IMPORTED" in r["stdout"]
    return r


# ------------------------------------------------------------------ Browser
def browser(url: str | None = None, actions: list | None = None) -> dict:
    """Open a URL (default browser) or run Playwright actions if installed.
    actions: [{"goto": url}, {"click": "text=Sign in"}, {"fill": ["#q", "hello"]}, {"press": ["#q","Enter"]},
              {"wait": ms}, {"text": "selector"}]"""
    if not actions:
        if not url:
            return {"ok": False, "error": "url required"}
        ok = webbrowser.open(url)
        return {"ok": ok, "url": url}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error": "Playwright not installed",
                "install_hint": "pip install --user playwright && python3 -m playwright install chromium"}
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        page = b.new_page()
        if url:
            page.goto(url)
        for a in actions:
            if "goto" in a:
                page.goto(a["goto"])
            elif "click" in a:
                page.click(a["click"])
            elif "fill" in a:
                page.fill(*a["fill"])
            elif "press" in a:
                page.press(*a["press"])
            elif "wait" in a:
                page.wait_for_timeout(a["wait"])
            elif "text" in a:
                out.append(page.inner_text(a["text"]))
        title = page.title()
        b.close()
    return {"ok": True, "title": title, "text": out}


# ------------------------------------------------------------------ Blender→Unreal recipe
def blender_unreal_plan(out_dir: str = "~/JarvisProjects/minecraft", project: str | None = None) -> list[dict]:
    """Deterministic plan for 'Open Blender, create realistic Minecraft assets, build a scene in Unreal'."""
    steps = [
        {"tool": "blender", "args": {"script": voxel_asset_script(out_dir)},
         "description": "Generate PBR voxel assets + scene in Blender (bpy), save .blend and export FBX"},
        {"tool": "run_command", "args": {"command": f"ls -la {out_dir}/minecraft_realistic.blend {out_dir}/minecraft_realistic.fbx"},
         "description": "Verify exported files exist"},
    ]
    if unreal_path() and project:
        steps.append({"tool": "unreal", "args": {"project": project,
                                                 "script": UNREAL_IMPORT.replace("FBX", repr(f"{out_dir}/minecraft_realistic.fbx"))},
                      "description": "Import FBX into Unreal, build lit level, save"})
    else:
        steps.append({"tool": "ask_user", "args": {"question": "Blender assets are ready. Unreal Engine is not installed (or no project given): "
                                                   "install it from https://www.unrealengine.com/linux and tell me the .uproject path to continue."},
                      "description": "Unreal Engine setup required"})
    steps.append({"tool": "open_app", "args": {"app": "blender"}, "description": "Open Blender to inspect the result"})
    return steps
