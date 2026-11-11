"""Token stream for Majiro comma/newline configuration reads."""
from __future__ import annotations


def config_tokens(text: str):
    tokens = []
    for line in text.splitlines():
        quoted = False
        current = []
        index = 0
        while index < len(line):
            char = line[index]
            if char == '"':
                quoted = not quoted
                current.append(char)
            elif not quoted and line[index:index + 2] == "//":
                break
            else:
                current.append(char)
            index += 1
        content = "".join(current).strip()
        if not content:
            continue
        quoted = False
        start = 0
        for index, char in enumerate(content + ","):
            if char == '"':
                quoted = not quoted
            if char == "," and not quoted:
                value = content[start:index].strip()
                if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                tokens.append(value)
                start = index + 1
        if quoted:
            raise ValueError("unterminated quoted configuration token")
    return tokens


def environment_values(text: str):
    result = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            # Preserve URL tokens; only whitespace starts inline commentary.
            value = parts[1].split("\t//", 1)[0].strip()
            result[parts[0]] = value.replace("|", " ")
    return result


def line_remainders(text: str):
    """Map each token cursor to the remaining original text on that line."""
    result = {}
    cursor = 1
    for line in text.splitlines():
        quoted = False
        stop = len(line)
        for index, char in enumerate(line):
            if char == '"':
                quoted = not quoted
            if not quoted and line[index:index + 2] == "//":
                stop = index
                break
        line = line[:stop].strip()
        if not line:
            continue
        starts = [0]
        quoted = False
        for index, char in enumerate(line):
            if char == '"':
                quoted = not quoted
            if char == "," and not quoted:
                starts.append(index + 1)
        for token, start in enumerate(starts):
            result[cursor + token] = {"text": line[start:].strip(), "next": cursor + len(starts)}
        cursor += len(starts)
    return result
