from __future__ import annotations

import os
import sys
from pathlib import Path


def _same_commit(candidate: str, target: str) -> bool:
    return bool(candidate) and (candidate.startswith(target) or target.startswith(candidate))


def rewrite_sequence(text: str, target: str) -> str:
    output: list[str] = []
    replaced = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        fields = stripped.split()
        if fields and not stripped.startswith("#"):
            command = fields[0]
            if command in {"pick", "p", "edit", "e", "reword", "r"} and len(fields) >= 2:
                if _same_commit(fields[1], target):
                    indentation = line[: len(line) - len(line.lstrip())]
                    ending = "\n" if line.endswith("\n") else ""
                    remainder = " ".join(fields[2:])
                    line = f"{indentation}reword {fields[1]}{f' {remainder}' if remainder else ''}{ending}"
                    replaced = True
            elif command == "merge":
                for index in range(1, len(fields) - 1):
                    if fields[index] == "-C" and _same_commit(fields[index + 1], target):
                        line = line.replace("-C", "-c", 1)
                        replaced = True
                        break
        output.append(line)
    if not replaced:
        raise ValueError(f"Commit {target} was not found in the rebase plan")
    return "".join(output)


def write_message(path: Path, message: str) -> None:
    path.write_text(message.rstrip() + "\n", encoding="utf-8", newline="\n")


def main(arguments: list[str]) -> int:
    if len(arguments) != 2 or arguments[0] not in {"sequence", "message"}:
        print("Usage: litegit_reword.py sequence|message <path>", file=sys.stderr)
        return 2
    mode, raw_path = arguments
    path = Path(raw_path)
    try:
        if mode == "sequence":
            target = os.environ["LITEGIT_REWORD_TARGET"]
            text = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(rewrite_sequence(text, target), encoding="utf-8", newline="\n")
        else:
            write_message(path, os.environ["LITEGIT_REWORD_MESSAGE"])
    except (KeyError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
