"""Retro Engine report checks: the scrub (secrets, home paths, emails, links, project names) and the copy check.

Used by scripts/retro-check. A hit names the rule and the line, never the matched text: a scrub must not
echo what it found, or it would leak the secret, path or name into the agent's own output. Stdlib only.
"""

import re

import overlay

HOME_PATH = re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+", re.I)
TILDE_PATH = re.compile(r"(?<!\w)~/\S")
HOME_VARIABLE = re.compile(r"\$\{?HOME\b")
WINDOWS_USER_PATH = re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
                   r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|npm_[A-Za-z0-9]{36}|(?:[sr]k|pk)_live_[A-Za-z0-9]{24,})")
JWT = re.compile(r"\beyJ[\w-]+\.eyJ[\w-]+\.")
GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{35}")
PASSWORD_PAIR = re.compile(r"\b(?:passw(?:or)?d|pwd)\s*[=:]\s*\S+", re.I)
TEMP_PATH = re.compile(r"/private/var/folders/\S*")
PRIVATE_KEY = re.compile(r"-----BEGIN")
KEY_ASSIGNMENT = re.compile(r"(?:key|token|secret|passw(?:or)?d)\s*[=:]\s*[\"']?[A-Za-z0-9+/=]{24,}", re.I)
SCRUB_PATTERNS = (("home path", HOME_PATH), ("tilde path", TILDE_PATH), ("home variable", HOME_VARIABLE),
                  ("windows user path", WINDOWS_USER_PATH), ("temp path", TEMP_PATH), ("email", EMAIL),
                  ("token", TOKEN), ("jwt", JWT), ("google api key", GOOGLE_API_KEY), ("private key", PRIVATE_KEY),
                  ("key assignment", KEY_ASSIGNMENT), ("password pair", PASSWORD_PAIR))
# A report holds no links or images: a viewer fetches an image URL, which would send data out (#201).
LINK = re.compile(r"\]\(|<img\b|<a\s|\b(?:src|href)\s*=|://|\bdata:[\w.+-]+/[\w.+-]+[;,]", re.I)
LINK_PATTERNS = (("link", LINK),)
COPY_WORDS = 8  # a report may not share this many words in a row with raw scan text


def scrub_patterns(strict, words=()):
    """The rules to run. Strict adds links and the given private words (whole words only)."""
    patterns = list(SCRUB_PATTERNS)
    if strict:
        patterns += LINK_PATTERNS
        patterns += [("project name", re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", re.I)) for w in words]
    return patterns


def scrub_text(label, text, patterns):
    """One hit {file, line, pattern} per match. The matched text is never part of a hit."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        seen = overlay.squash(line)  # NFKC and zero-width marks gone, so a split path still matches
        for name, rx in patterns:
            hits += [{"file": str(label), "line": n, "pattern": name} for _ in rx.finditer(seen)]
    return hits


def plain_words(line):
    """Lower-case words with punctuation and a leading list marker dropped, so quotes and bullets hide no copy."""
    text = re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s+", " ", overlay.squash(line).lower())
    return re.sub(r"[\W_]+", " ", text).split()


def word_runs(text):
    words = plain_words(text)
    return {" ".join(words[i:i + COPY_WORDS]) for i in range(len(words) - COPY_WORDS + 1)}


def copied_lines(text, raw_texts):
    """Line numbers of the report that share COPY_WORDS or more words in a row with any raw text (N1)."""
    raw = set().union(*map(word_runs, raw_texts))
    words = [(n, w) for n, line in enumerate(text.splitlines(), 1) for w in plain_words(line)]
    hit = set()
    for i in range(len(words) - COPY_WORDS + 1):  # one word stream, so a copy wrapped over two lines still matches
        window = words[i:i + COPY_WORDS]
        if " ".join(w for _, w in window) in raw:
            hit.update(n for n, _ in window)
    return sorted(hit)
