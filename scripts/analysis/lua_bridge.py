"""Run the same Lua 5.1 runtime used by Artemis; Python is only an I/O boundary."""
from __future__ import annotations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT


def create_lua():
    from lupa.lua51 import LuaRuntime
    return LuaRuntime(unpack_returned_tuples=True)


def table_value(value):
    if not hasattr(value, "items"):
        return value
    keys = list(value.keys())
    if keys and all(isinstance(k, (int, float)) and k >= 1 and k % 1 == 0 for k in keys):
        if sorted(keys) == list(range(1, len(keys) + 1)):
            return [table_value(value[i]) for i in range(1, len(keys) + 1)]
    return {str(k): table_value(v) for k, v in value.items()}


def setup_runtime(lua, allow_prepare=True, save_root=None):
    catalogue_paths = {}

    def read_script(name):
        record = catalogue_paths[name.casefold()]
        return lua.execute((ROOT / "resources/scripts" / (record + ".lua")).read_text(encoding="utf-8"))

    vm_module = lua.execute((ROOT / "runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    sys_module = lua.execute((ROOT / "runtime/majiro_syscalls.lua").read_text(encoding="utf-8"))
    opcodes = lua.execute((ROOT / "resources/scripts/opcodes.lua").read_text(encoding="utf-8"))
    names = lua.execute((ROOT / "resources/scripts/syscall_names.lua").read_text(encoding="utf-8"))
    cfg = lua.execute((ROOT / "resources/scripts/config_files.lua").read_text(encoding="utf-8"))
    image_metadata = lua.execute((ROOT / "resources/scripts/image_metadata.lua").read_text(encoding="utf-8"))
    catalogue = lua.execute((ROOT / "resources/scripts/catalogue.lua").read_text(encoding="utf-8"))
    catalogue_paths.update({k.casefold(): v["id"] for k, v in catalogue["scripts"].items()})
    options = lua.table_from({"names": names, "config_files": cfg, "width": 1280, "height": 720})
    options["image_metadata"] = image_metadata
    options["environment"] = lua.execute((ROOT / "resources/scripts/environment.lua").read_text(encoding="utf-8"))
    options["config_lines"] = lua.execute((ROOT / "resources/scripts/config_lines.lua").read_text(encoding="utf-8"))
    options["resource_names"] = lua.execute((ROOT / "resources/scripts/resource_names.lua").read_text(encoding="utf-8"))
    clock=lua.table_from({"ms": 0})
    options["now"] = lua.eval("function(clock)return function()return clock.ms end end")(clock)
    options["modules"] = lambda path: lua.execute((ROOT / path).read_text(encoding="utf-8"))
    options["cp932"] = lambda text: text.encode("cp932")
    from majiro_image import Decoder
    from port_common import atomic_bytes, lua_value, read_json, write_json
    decoder = Decoder()
    options["audio_aliases"]=lua.table_from(read_json(ROOT/"config/resource_aliases.json")["audio"])
    translation=lua.execute((ROOT/"resources/scripts/translation.lua").read_text(encoding="utf-8"))
    localization=lua.execute((ROOT/"runtime/majiro_localization.lua").read_text(encoding="utf-8"))
    localized=localization["new"](translation,options["cp932"])
    options["cp932"]=localized["cp932"]
    options["display_text"]=localized["display"]
    options["font_face"]="Source Han Sans SC"
    # Model dialog cancellation; actual chooser is a Windows-native boundary.
    options["font_dialog"]=lambda face,proportional:face
    source_manifest=read_json(ROOT/"resources/source_manifest.json")
    identity=source_manifest.get("save_identity",source_manifest["source_identity"])
    options["source_identity"]=identity
    options["set_clock"]=lua.eval("function(clock)return function(value)clock.ms=value end end")(clock)
    if save_root is not None:
        import hashlib
        import os
        import re
        save_root=Path(save_root).resolve()
        save_root.mkdir(parents=True,exist_ok=True)
        def store_host(request):
            action,_,rest=request.partition(" ")
            if request.startswith("sha256\n"):
                return "OK "+hashlib.sha256(request[7:].encode("utf-8")).hexdigest()
            name,_,data=rest.partition("\n")
            if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}",name):
                raise ValueError("invalid test save filename")
            path=save_root/name
            if action=="save_exists":return "OK "+str(int(path.exists()))
            if action=="save_read":return "OK data\n"+path.read_bytes().decode("utf-8")if path.exists()else"OK absent"
            if action=="save_write":
                temporary=path.with_name(path.name+".writing")
                temporary.write_text(data,encoding="utf-8",newline="")
                os.replace(temporary,path);return "OK"
            if action=="save_delete":
                path.unlink(missing_ok=True);return "OK"
            raise ValueError("unsupported test storage operation: "+action)
        codec=lua.execute((ROOT/"runtime/majiro_codec.lua").read_text(encoding="utf-8"))
        store=lua.execute((ROOT/"runtime/majiro_store.lua").read_text(encoding="utf-8"))["new"](
            codec,store_host,identity,lua.table_from(source_manifest.get("save_compatible_identities",[])))
        store["path"]=lambda name:str(save_root/name)
        persisted=store["load_index"](store)["profile"]
        if persisted:
            options["registry"]=persisted["registry"];options["progress"]=persisted["progress"]
            options["saved_settings"]=persisted["settings"]
            # A full persistent bank already contains these values. Applying
            # legacy tokens on the later reset-defaults read would undo reset.
            if persisted["persistent_globals"] is None:
                options["preference_tokens"]=persisted["preference_tokens"]
        options["store"]=store
    assets_path = ROOT / "resources/image_catalogue.json"
    assets = read_json(assets_path) if assets_path.exists() else {}

    def asset(name, alpha):
        member = decoder.catalogue.resolve(name, (".rct", ".rc8"))
        if member.name not in assets or "source_depth" not in assets[member.name]:
            if assets_path.exists():assets.update(read_json(assets_path))
        if member.name not in assets or "source_depth" not in assets[member.name]:
            if not allow_prepare:
                raise RuntimeError("image was not prepared: " + member.name)
            # The same source and transformed output are shared by every host.
            assets[member.name] = decoder.convert(member.name)
            write_json(assets_path, assets)
            atomic_bytes(ROOT / "resources/scripts/image_catalogue.lua",
                         ("return " + lua_value(assets) + "\n").encode("utf-8"))
        return lua.table_from(assets[member.name], recursive=True)

    options["asset"] = asset
    from majiro_audio import Audio
    audio_decoder=Audio(decoder.catalogue)
    audio_path=ROOT/"resources/audio_catalogue.json"
    audio_assets=read_json(audio_path) if audio_path.exists() else {}

    def media_asset(name):
        member=audio_decoder.catalogue.resolve(name,(".ogg",".wav"))
        if member.name not in audio_assets:
            if audio_path.exists():audio_assets.update(read_json(audio_path))
        if member.name not in audio_assets:
            if not allow_prepare:
                raise RuntimeError("audio was not prepared: "+member.name)
            audio_assets[member.name]=audio_decoder.prepare(member.name)
            write_json(audio_path,audio_assets)
            atomic_bytes(ROOT/"resources/scripts/audio_catalogue.lua",
                         ("return "+lua_value(audio_assets)+"\n").encode("utf-8"))
        return lua.table_from(audio_assets[member.name],recursive=True)

    def audio(action, spec):
        if action in ("play","stop","fadeout","volume","pan","pause","resume"):
            return True
        if action=="playing":
            return spec["paused_at"]is not None or spec["loop"] or clock["ms"]-spec["started"]<spec["duration_ms"]
        raise ValueError("unsupported model audio action: "+action)

    options["media_asset"]=media_asset
    options["audio"]=audio
    from majiro_movie import catalogue as movie_catalogue
    options["movies"]=lua.table_from(movie_catalogue(),recursive=True)
    options["movie"]=lambda action,spec: (
        int(clock["ms"]<spec["started"]+spec["duration_ms"])if action=="status"else True)
    from native_pixels import Pixels
    pixels = Pixels(port=True)
    options["graphics"] = pixels
    system = sys_module["new"](options)
    vm_options = lua.table_from({"opcodes": opcodes, "syscall": system["invoke"]})
    if save_root is not None and persisted:
        vm_options["persistent_globals"] = persisted["persistent_globals"]
    vm_options["persistent_changed"] = system["persistent_changed"]
    vm_options["script_loaded"] = system["script_loaded"]
    vm_options["after_init"] = system["after_init"]
    vm_options["restart_on_return"] = True
    vm_options["resume_action"] = system["resume_action"]
    vm_options["wall_deadline"] = system["wall_deadline"]
    vm_options["string_ops"] = system["strings"]
    vm_options["text_ops"] = system["text"]
    vm_options["line_changed"]=system["line_changed"]
    vm_options["loader"] = read_script
    vm = vm_module["new"](vm_options)
    return vm_module, vm, system, clock
