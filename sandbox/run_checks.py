#!/usr/bin/env python3
"""VolumetricLight3D sandbox test runner.

Examples
--------
  python3 run_checks.py                     # run every check
  python3 run_checks.py --only hlsl         # only checks whose name contains "hlsl"
  python3 run_checks.py --tags core,occl    # only checks with these tags
  python3 run_checks.py --list              # list discovered checks
  python3 run_checks.py -v                  # verbose failure traces
Exit code: 0 when nothing FAILED, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vl3dsb.harness import FAIL, PASS, SKIP, WARN, OUT_DIR, discover, run_check, summarize


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="comma separated substrings matched against check names")
    ap.add_argument("--tags", default="", help="comma separated tags")
    ap.add_argument("--list", action="store_true", help="list discovered checks and exit")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json", default=str(OUT_DIR / "results.json"), help="write machine readable results here")
    ap.add_argument("--allow-fail", default="", help="comma separated check-name substrings allowed to fail")
    ap.add_argument("--repo", default=os.environ.get("VL3DSB_REPO", ""),
                    help="repository to test (script, Language and src/VL3DBridge)")
    ap.add_argument("--script", default=os.environ.get("VL3DSB_SCRIPT", ""),
                    help="override only the .obj2 script; other files come from --repo")
    args = ap.parse_args(argv)

    # discover() imports the check modules (and hence the target paths), so set
    # the environment only after argument parsing and before calling discover.
    if args.repo:
        target_repo = Path(args.repo).expanduser().resolve()
        required = (target_repo / "Script" / "VolumetricLight3D.obj2",
                    target_repo / "Language", target_repo / "src" / "VL3DBridge")
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            print("--repo is missing required paths: " + ", ".join(missing), file=sys.stderr)
            return 2
        os.environ["VL3DSB_REPO"] = str(target_repo)
        print(f"== target repository: {target_repo}")
    if args.script:
        target = Path(args.script).expanduser().resolve()
        if not target.is_file():
            print(f"--script {target} does not exist or is not a file", file=sys.stderr)
            return 2
        os.environ["VL3DSB_SCRIPT"] = str(target)
        print(f"== target script: {target} (other files from target repository)")
    only = [x.strip() for x in args.only.split(",") if x.strip()]
    tags = [x.strip() for x in args.tags.split(",") if x.strip()]
    specs = discover(only=only, tags=tags)
    if not specs:
        print("no checks matched", file=sys.stderr)
        return 2
    if args.list:
        for s in specs:
            print(f"{s.module:26s} {s.name}")
        return 0

    allow = [x.strip() for x in args.allow_fail.split(",") if x.strip()]
    results = []
    t0 = time.time()
    print(f"== VolumetricLight3D sandbox: {len(specs)} checks ==")
    for spec in specs:
        r = run_check(spec, verbose=args.verbose)
        results.append(r)
        line = f"[{r.status:4s}] {r.seconds:6.2f}s  {r.name}"
        if r.detail and r.status in (FAIL, WARN, SKIP):
            line += f"\n         -> {r.detail.splitlines()[0][:300]}"
        print(line, flush=True)

    total = time.time() - t0
    counts = summarize(results)
    out_json = Path(args.json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(
        {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "target_repo": os.environ.get("VL3DSB_REPO", str(HERE.parent)),
         "target_script": os.environ.get("VL3DSB_SCRIPT", "Script/VolumetricLight3D.obj2"),
         "seconds": round(total, 2), "counts": counts,
         "results": [r.to_dict() for r in results]},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== summary ==")
    print(f"PASS {counts[PASS]}   FAIL {counts[FAIL]}   SKIP {counts[SKIP]}   WARN {counts[WARN]}"
          f"   ({total:.1f}s)")
    print(f"results: {out_json}")
    failures = [r for r in results if r.status == FAIL]
    if failures:
        print("\nfailed checks:")
        for r in failures:
            print(f"  - {r.name}: {r.detail.splitlines()[0] if r.detail else ''}")
        if allow and all(any(a.lower() in r.name.lower() for a in allow) for r in failures):
            print("\n(all failures are allow-listed)")
            return 0
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
