"""Validated initialized guest bytes for original-code comparison fixtures only."""
from pathlib import Path
import base64
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
RAW_ID = "371fc97d0500b2bb20d23a82e604630d52ec9eb994d7bf32aba34eeae08571f1"
INITIALIZED_ID = "8f0c773e4f8bc1fcee3fda5665440af689aae7e19d5e1b97c1c915d54885e457"
CONTAINER_ID = "4f0838db4c2523d2884bd01a2bf653e642c5fa1de43de300288a6af427f6e50c"


def comparison_bytes(sample, raw, manifest_path=None):
    """Return exact guest bytes and explicit provenance without changing inputs.

    Unknown builds are read unchanged. A missing development reference remains
    explicit; a present but invalid reference fails instead of falling back.
    """
    if sample["exe_sha256"] != RAW_ID:
        return raw, None
    if hashlib.sha256(raw).hexdigest() != RAW_ID:
        raise ValueError("original raw reference hash changed")
    manifest_path = Path(manifest_path) if manifest_path else ROOT / "reports/happy_wrapper_recovery.json"
    if not manifest_path.is_file():
        return raw, {"status": "initialized_reference_unavailable",
                     "raw_exe_sha256": RAW_ID, "manifest": str(manifest_path),
                     "initialized_guest_executed": False}
    proof = json.loads(manifest_path.read_text(encoding="utf8"))
    if (proof.get("status") != "passed" or proof.get("raw_embedded_sha256") != RAW_ID
            or proof.get("container_sha256") != CONTAINER_ID
            or proof.get("initialized_reference_sha256") != INITIALIZED_ID
            or proof.get("native_block_comparisons") != 227):
        raise ValueError("initialized original reference proof does not match the verified identities")
    cases = proof.get("native_minimum_cases", [])
    if len(cases) != 3 or any(row.get("status") != "passed" for row in cases):
        raise ValueError("initialized original reference has no complete native validation")
    for key, name in (("script", "recover_happy_wrapper.py"), ("fixture", "test_new_graphics_abi.py")):
        live = hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest()
        if proof.get("sources_sha256", {}).get(key) != live:
            raise ValueError("initialized original reference validation source changed: " + name)
    reference = Path(proof["initialized_reference"]).resolve()
    if not reference.is_relative_to((ROOT / "archive").resolve()):
        raise ValueError("initialized guest must remain a development archive artifact")
    initialized = reference.read_bytes()
    if hashlib.sha256(initialized).hexdigest() != INITIALIZED_ID:
        raise ValueError("initialized original guest bytes changed")
    container = Path(proof["original_container"]).read_bytes()
    if hashlib.sha256(container).hexdigest() != CONTAINER_ID:
        raise ValueError("original wrapper container changed")
    import pefile
    outer = pefile.PE(data=container)
    image = outer.get_memory_mapped_image()
    pointer = int.from_bytes(image[0x46148:0x4614C], "little")
    encoded = image[pointer - outer.OPTIONAL_HEADER.ImageBase:].split(b"\0", 1)[0]
    key = base64.b64encode(encoded)[:16]
    if (len(encoded) != 20 or len(key) != 16
            or hashlib.sha256(key).hexdigest() != proof.get("key_sha256")):
        raise ValueError("original wrapper key provenance changed")
    inner = pefile.PE(data=raw)
    section = inner.sections[0]
    start, size = section.PointerToRawData, 4096
    if (section.Name.rstrip(b"\0") != b".text" or section.VirtualAddress != 0x1000
            or proof.get("modified_file_offset") != start
            or proof.get("modified_virtual_address") != inner.OPTIONAL_HEADER.ImageBase + 0x1000
            or proof.get("modified_bytes") != size):
        raise ValueError("initialized original reference changed its validated range")
    expected = bytearray(raw[start:start + size])
    for offset in range(0, len(expected) - 20, 18):
        expected[offset:offset + 16] = bytes(a ^ b for a, b in zip(expected[offset:offset + 16], key))
    if (len(initialized) != len(raw) or initialized[:start] != raw[:start]
            or initialized[start + size:] != raw[start + size:]
            or initialized[start:start + size] != bytes(expected)):
        raise ValueError("initialized guest contains changes outside the native-validated transformation")
    return initialized, {
        "status": "validated_initialized_guest", "raw_exe_sha256": RAW_ID,
        "initialized_exe_sha256": INITIALIZED_ID, "container_sha256": CONTAINER_ID,
        "reference_path": str(reference), "manifest": str(manifest_path),
        "native_block_comparisons": 227, "modified_file_offset": start, "modified_bytes": size,
        "production_configuration_modified": False,
    }
