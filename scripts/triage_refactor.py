"""Triage des suggestions `remove` du code-review-graph.

Pour chaque symbole declare "non utilise", compte les references reelles dans
les sources (hors sa propre definition, hors __pycache__, hors node_modules) et
classe la cause probable du faux positif.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
SUG = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

SRC_DIRS = ["app", "tests", "scripts", "strategies", "recipes", "frontend/src", "frontend/e2e"]
EXTRA_FILES = ["cli.py", "optimize_runner.py", "config.yaml"]

corpus: list[tuple[str, str]] = []
for d in SRC_DIRS:
    p = ROOT / d
    if not p.exists():
        continue
    for f in p.rglob("*"):
        if not f.is_file():
            continue
        if any(x in f.parts for x in ("__pycache__", "node_modules", ".next")):
            continue
        if f.suffix not in {".py", ".ts", ".tsx", ".yaml", ".yml", ".json", ".js", ".mjs"}:
            continue
        try:
            corpus.append((str(f.relative_to(ROOT)).replace("\\", "/"), f.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            pass
for f in EXTRA_FILES:
    p = ROOT / f
    if p.exists():
        corpus.append((f, p.read_text(encoding="utf-8", errors="ignore")))

rows = []
for r in SUG["removes"]:
    name = r["name"].split(".")[-1]          # Classe.methode -> methode
    owner = r["file"]
    pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    defpat = re.compile(rf"^\s*(?:async\s+)?(?:def|class|function|const|export\s+function)\s+{re.escape(name)}\b")

    hits, self_calls, reexport, decorated, callback, in_yaml, in_tests = 0, 0, False, False, False, False, 0
    for path, text in corpus:
        for line in text.splitlines():
            if not pat.search(line):
                continue
            if path == owner and defpat.match(line):
                continue
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", '"""')):
                continue           # commentaire : ne compte pas comme usage
            hits += 1
            if re.search(rf"self\.{re.escape(name)}\b", line):
                self_calls += 1
            if path.endswith("__init__.py") or stripped.startswith(("from ", "import ", "export ")):
                reexport = True
            if stripped.startswith("@"):
                decorated = True
            if re.search(rf"=\s*{re.escape(name)}\s*[,)]|\(\s*{re.escape(name)}\s*[,)]", line):
                callback = True
            if path.endswith((".yaml", ".yml", ".json")):
                in_yaml = True
            if "/tests/" in path or path.startswith("tests/"):
                in_tests += 1

    # cause probable
    if hits == 0:
        cause = "AUCUNE REFERENCE"
    elif self_calls:
        cause = "mixin / self.x()"
    elif reexport:
        cause = "re-export __init__"
    elif callback:
        cause = "passe en callback"
    elif in_yaml:
        cause = "reference en YAML/JSON"
    elif decorated:
        cause = "decorateur framework"
    else:
        cause = "autre reference"
    rows.append({"name": r["name"], "file": owner, "hits": hits, "tests": in_tests, "cause": cause})

by_cause: dict[str, int] = {}
for x in rows:
    by_cause[x["cause"]] = by_cause.get(x["cause"], 0) + 1

print(f"corpus: {len(corpus)} fichiers | suggestions 'remove' analysees: {len(rows)}")
print("\n=== CAUSE PROBABLE ===")
for c, n in sorted(by_cause.items(), key=lambda kv: -kv[1]):
    print(f"  {n:3}  {c}")

real = [x for x in rows if x["cause"] == "AUCUNE REFERENCE"]
print(f"\n=== CANDIDATS REELS ({len(real)}/{len(rows)} = {100*len(real)//len(rows)}%) ===")
back = [x for x in real if not x["file"].startswith("frontend/")]
front = [x for x in real if x["file"].startswith("frontend/")]
print(f"  backend: {len(back)} | frontend: {len(front)}")
print("\n-- backend, par fichier --")
byf: dict[str, list[str]] = {}
for x in back:
    byf.setdefault(x["file"], []).append(x["name"])
for f, names in sorted(byf.items(), key=lambda kv: -len(kv[1])):
    print(f"  {len(names):2}  {f}: {', '.join(sorted(names)[:6])}{' …' if len(names) > 6 else ''}")

Path(sys.argv[3]).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"\n[detail -> {sys.argv[3]}]")
