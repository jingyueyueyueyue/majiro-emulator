"""Strict MJO decoder. Reads pinned opcode definitions as AST data, never executes them."""
from __future__ import annotations
import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import struct
from pathlib import Path
from port_common import ROOT

PINNED = ROOT / "references/majiro-py"


def opcode_table(path=None):
    tree = ast.parse((path or PINNED / "opcodes.py").read_text(encoding="utf-8-sig"))
    result = {}
    suffixes = (".i", ".r", ".s", ".iarr", ".rarr", ".sarr")
    definitions = {"define_opcode", "define_binary_operator", "define_assignment_operator",
                   "define_array_assignment_operator"}
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id not in definitions:
            continue
        kind = call.func.id
        value, name = (ast.literal_eval(arg) for arg in call.args[:2])
        if kind == "define_opcode":
            result[value] = (name, ast.literal_eval(call.args[3]))
        else:
            mask = call.args[3].attr
            count = {"INT": 1, "NUMERIC": 2, "PRIMITIVE": 3, "ALL": 6}[mask]
            for i in range(count):
                result[value + i] = (name + (suffixes[i] if count > 1 else ""),
                                     "" if kind == "define_binary_operator" else "fho")
    if len(result) < 100:
        raise ValueError("pinned opcode table incomplete")
    return result


def make_key():
    result = bytearray()
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
        result.extend(struct.pack("<I", value))
    return bytes(result)


KEY = make_key()


def crypt(data: bytes):
    return bytes(value ^ KEY[i % 1024] for i, value in enumerate(data))


@dataclass(frozen=True)
class Instruction:
    offset: int
    size: int
    opcode: int
    name: str
    encoding: str
    operands: tuple
    targets: tuple


class Script:
    def __init__(self, name: str, raw: bytes, opcodes=None, string_decoder=None, address_repairs=None,
                 inspect_invalid_targets=False, stack_repairs=None):
        self.name = name
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if len(raw) < 32 or raw[:16] not in (
            b"MajiroObjX1.000\0", b"MajiroObjV1.000\0", b"MjPlainBytecode\0"):
            raise ValueError(f"{name}: unknown or truncated MJO header")
        self.signature = raw[:16]
        self.main, self.line_count, count = struct.unpack_from("<III", raw, 16)
        self.code_offset = 32 + 8 * count
        if self.code_offset > len(raw):
            raise ValueError(f"{name}: truncated function table")
        self.functions = [struct.unpack_from("<II", raw, 28 + 8 * i) for i in range(count)]
        self.code_size, = struct.unpack_from("<I", raw, self.code_offset - 4)
        if self.code_offset + self.code_size != len(raw):
            raise ValueError(f"{name}: bytecode size mismatch or unaccounted trailing bytes")
        body = raw[self.code_offset:]
        self.code = crypt(body) if self.signature == b"MajiroObjX1.000\0" else body
        self.opcodes = opcodes or opcode_table()
        self.stack_repairs=stack_repairs or []
        self.string_decoder = string_decoder or (lambda data: data.decode("cp932", errors="strict"))
        self.instructions = []
        self._decode()
        if address_repairs:
            from dataclasses import replace
            for repair in address_repairs:
                index=next(i for i,row in enumerate(self.instructions) if row.offset==repair["offset"])
                row=self.instructions[index]
                if row.name!="br" or row.targets!=(repair["old_target"],):
                    raise ValueError(f"{name}: pinned translation relocation no longer matches")
                self.instructions[index]=replace(row,operands=(repair["new_target"],),
                                                 targets=(repair["new_target"],))
        boundaries = {instruction.offset for instruction in self.instructions}
        # Some function-only scripts (e.g. White's kosihata.mjo) have no
        # top-level entry. Keep the on-disk sentinel; never invent an entry.
        # Loading its functions is valid, but starting at this offset is not.
        self.has_main = self.main != 0xFFFFFFFF
        if (self.has_main and self.main not in boundaries) or any(
                offset not in boundaries for _, offset in self.functions):
            raise ValueError(f"{name}: invalid main/function entry")
        self.invalid_targets=[]
        for instruction in self.instructions:
            if any(target not in boundaries for target in instruction.targets):
                self.invalid_targets.append(instruction)
                if not inspect_invalid_targets:
                    raise ValueError(f"{name}@{instruction.offset:08x}: target not at instruction boundary")
        self.by_offset = {instruction.offset: instruction for instruction in self.instructions}

    def _decode(self):
        cursor = 0

        def read(fmt):
            nonlocal cursor
            length = struct.calcsize(fmt)
            if cursor + length > len(self.code):
                raise ValueError(f"{self.name}@{cursor:08x}: truncated operand")
            values = struct.unpack_from(fmt, self.code, cursor)
            cursor += length
            return values[0] if len(values) == 1 else values

        while cursor < len(self.code):
            offset = cursor
            opcode = read("<H")
            if opcode not in self.opcodes:
                raise ValueError(f"{self.name}@{offset:08x}: unknown opcode {opcode:04x}")
            name, encoding = self.opcodes[opcode]
            operands, targets = [], []
            for field in encoding:
                if field in ("s", "t"):
                    length = read("<H")
                    if cursor + length > len(self.code):
                        raise ValueError(f"{self.name}@{offset:08x}: truncated string/type list")
                    data = self.code[cursor:cursor + length]
                    cursor += length
                    if field == "s":
                        if not data.endswith(b"\0"):
                            raise ValueError(f"{self.name}@{offset:08x}: unterminated string")
                        value = self.string_decoder(data[:-1])
                    else:
                        value = list(data)
                elif field == "c":
                    count = read("<H")
                    value = []
                    for _ in range(count):
                        relative = read("<i")
                        value.append(cursor + relative)
                    targets.extend(value)
                else:
                    fmt = {"f": "<H", "h": "<I", "o": "<h", "0": "<I",
                           "i": "<i", "r": "<f", "a": "<H", "j": "<i", "l": "<H"}[field]
                    value = read(fmt)
                    if field == "0" and value != 0:
                        raise ValueError(f"{self.name}@{offset:08x}: nonzero call placeholder")
                    if field == "j":
                        value += cursor
                        targets.append(value)
                operands.append(value)
            self.instructions.append(Instruction(offset, cursor - offset, opcode, name, encoding,
                                                  tuple(operands), tuple(targets)))

    @property
    def id(self):
        return "m_" + hashlib.sha256(self.name.encode("utf-8")).hexdigest()[:16]

    def runtime_data(self):
        if self.invalid_targets:
            raise ValueError("cannot publish a script with unresolved jump targets: "+self.name)
        code=[]
        preludes={r["offset"]:r for r in self.stack_repairs}
        virtual=self.code_size
        for instruction in self.instructions:
            row=[instruction.offset,instruction.opcode,*instruction.operands]
            repair=preludes.get(instruction.offset)
            if repair:
                if instruction.name!=repair["next_opcode"]:
                    raise ValueError("translation numeric prelude anchor changed")
                offset=instruction.offset
                for value in repair["push_values"]:
                    code.append([offset,0x800,value])
                    offset=virtual;virtual+=1
                row[0]=offset
            code.append(row)
        return {
            "schema": 1, "name": self.name, "source_sha256": self.sha256,
            "main": self.main, "line_count": self.line_count, "functions": self.functions,
            "code_size": self.code_size,
            "code": code,
        }

    def summary(self):
        return {
            "name": self.name, "id": self.id, "sha256": self.sha256,
            "main": self.main, "functions": len(self.functions),
            "instructions": len(self.instructions), "bytecode_bytes": self.code_size,
            "opcodes": dict(Counter(i.name for i in self.instructions)),
        }
