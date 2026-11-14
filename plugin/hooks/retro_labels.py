"""Labels for the retro scan: an error class for a tool error, and a path-free shape for a command. Stdlib only."""

import os
import re

RUNNERS = ("python", "python3", "bash", "sh", "zsh", "node")
ERR_CLASS = [
    ("hook-block", re.compile(r"hook|blocked by|PreToolUse", re.I)),
    ("user-rejected", re.compile(r"doesn't want to proceed|user rejected|rejected by user", re.I)),
    ("classifier-denied", re.compile(r"auto mode classifier", re.I)),
    ("sandbox", re.compile(r"Operation not permitted|sandbox_violations|x509|LSOpenURLs|-10822|Read-only file system", re.I)),
    ("edit-mismatch", re.compile(r"String to replace not found|old_string|File has not been read|modified since read", re.I)),
    ("file-missing", re.compile(r"does not exist|No such file|ENOENT", re.I)),
    ("timeout", re.compile(r"timed out|timeout", re.I)),
    ("gh-api", re.compile(r"HTTP 4\d\d|GraphQL|gh: |rate limit", re.I)),
    ("test-or-build-fail", re.compile(r"FAIL|failed|AssertionError|Error:|exit code [1-9]", re.I)),
]


def classify(text):
    for name, rx in ERR_CLASS:
        if rx.search(text[:3000]):
            return name
    return "other"


def shape(cmd):
    """A short, path-free label for a command: program and subcommand, '<script>' for scripts, '(chain)' for chains."""
    s = cmd.strip()
    if re.match(r"cd\s+\S+\s*(&&|;)", s):
        return "cd-chain"
    s = re.sub(r"^([A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", s)
    words = s.split()
    if not words:
        return "empty"
    prog = os.path.basename(words[0])
    if prog in RUNNERS and len(words) > 1 and "/" in words[1]:
        label = f"{prog} <script>"
    elif len(words) > 1 and re.match(r"^[a-z][\w-]*$", words[1]):
        label = f"{prog} {words[1]}"
    else:
        label = prog
    return label + (" (chain)" if re.search(r"&&|;|\$\(|\bdo\b", s) else "")
