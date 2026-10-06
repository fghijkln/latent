"""Latent lexer: indent-aware, errors carry line/col."""

KEYWORDS = {
    "fn", "if", "elif", "else", "while", "for", "in", "and", "or", "not",
    "true", "false", "nil", "py", "java", "say", "return", "break", "continue",
    "class", "try", "catch", "throw", "import", "as", "super", "global",
}


class Tok:
    def __init__(self, kind, value, line, col):
        self.kind = kind      # e.g. 'NAME', 'NUM', 'STR', '+', 'INDENT', ...
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Tok({self.kind!r},{self.value!r},{self.line}:{self.col})"


class LexError(Exception):
    pass


def _err(msg, line, col):
    raise LexError(f"{line}:{col}: {msg}")


def lex(src):
    toks = []
    indents = [0]
    lines = src.split("\n")
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue  # blank / comment lines don't affect indentation
        # tab check
        for i, ch in enumerate(line):
            if ch == "\t":
                _err("tab indentation is not allowed, use spaces", lineno, i + 1)
            if ch != " ":
                break
        indent = len(line) - len(line.lstrip(" "))
        if indent > indents[-1]:
            indents.append(indent)
            toks.append(Tok("INDENT", None, lineno, 1))
        else:
            while indent < indents[-1]:
                indents.pop()
                toks.append(Tok("DEDENT", None, lineno, 1))
            if indent != indents[-1]:
                _err("inconsistent indentation", lineno, 1)
        toks.extend(_lex_line(line, lineno, indent))
        toks.append(Tok("NEWLINE", None, lineno, len(line) + 1))
    while len(indents) > 1:
        indents.pop()
        toks.append(Tok("DEDENT", None, len(lines), 1))
    toks.append(Tok("EOF", None, len(lines), 1))
    return toks


def _lex_line(line, lineno, base):
    toks = []
    i = base
    n = len(line)
    while i < n:
        c = line[i]
        col = i + 1
        if c == " ":
            i += 1
            continue
        if c == "#":
            break
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            kind = word.upper() if word in KEYWORDS else "NAME"
            toks.append(Tok(kind, word, lineno, col))
            i = j
            continue
        if c.isdigit() or (c == "." and i + 1 < n and line[i + 1].isdigit()):
            j = i
            while j < n and (line[j].isdigit() or line[j] == "."):
                j += 1
            try:
                val = float(line[i:j])
            except ValueError:
                _err(f"bad number {line[i:j]!r}", lineno, col)
            toks.append(Tok("NUM", val, lineno, col))
            i = j
            continue
        if c in "\"'":
            val, i = _lex_string(line, i, lineno)
            toks.append(Tok("STR", val, lineno, col))
            continue
        two = line[i:i + 2]
        if two in ("==", "!=", "<=", ">=", "**", "=>"):
            toks.append(Tok(two, two, lineno, col))
            i += 2
            continue
        if c in "+-*/%<>=():,[].{}":
            toks.append(Tok(c, c, lineno, col))
            i += 1
            continue
        _err(f"unexpected character {c!r}", lineno, col)
    return toks


def _lex_string(line, i, lineno):
    quote = line[i]
    col = i + 1
    i += 1
    out = []
    n = len(line)
    while i < n:
        c = line[i]
        if c == quote:
            return "".join(out), i + 1
        if c == "\\":
            if i + 1 >= n:
                break
            esc = line[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r",
                        "\\": "\\", '"': '"', "'": "'",
                        "0": "\0"}.get(esc, esc))
            i += 2
            continue
        out.append(c)
        i += 1
    _err("unterminated string", lineno, col)
