#!/usr/bin/env python3
"""Step parity of a GitHub data workflow and the function of pipeline-run.sh that replaces it.

Used by test-units.sh. Standard library only (Python 3.8 or newer). It reads files and prints;
it runs none of the commands it reads.

    step_parity.py --workflow now.yml --runner pipeline-run.sh --lib pipeline-lib.sh \
        --function job_now --job now

Both sides are reduced to an ordered list of "entry points": the commands that do the work
(`py -m live.nisar_stack --now-dir data/now`, `lib restore-fallback data/now/history`, `uv-sync`,
`detect-data`). Workflow side: every command line of every `run:` step. Runner side: the commands
the named function executes, in order, with each helper function it calls resolved to the commands
in its own body. The two lists must be equal: a call that is missing, a call that is only a
function name whose body is empty, a different order and an extra step all fail.

A command line that starts a tool (python, uv, pytest, curl, bash, git ...) in a form this file
does not understand fails too, on either side, so a new form cannot pass unnoticed. Shell glue
(echo, set, if, cat ...) and the steps that only exist on one side by design are listed below.
Exit 0: equal. Exit 1: not equal or not understood. Exit 2: bad arguments.
"""
import argparse
import re
import shlex
import sys

# a command that starts one of these and is not understood is an error
TOOLS = {
    "uv", "uvx", "python", "python3", "pytest", "pip", "pip3", "curl", "wget", "bash", "sh",
    "node", "npm", "npx", "bun", "bunx", "make", "gh", "git", "ruff", "mypy", "gdalinfo",
    "ogr2ogr", "docker", "ssh", "scp", "rsync", "eval", "xargs", "find", "parallel", "su",
    "runuser", "sudo", "setsid", "ionice", "nice",
}
# wrappers that run the command after them: the checker looks at that command
WRAPPERS = {"env", "exec", "command", "nohup"}
# shell glue and commands that start no tool
GLUE = {
    "echo", "printf", "set", "export", "unset", "cd", "cat", "test", "[", "[[", "true", "false", ":",
    "exit", "return", "local", "declare", "readonly", "shift", "read", "mkdir", "rm", "cp", "mv",
    "chmod", "touch", "sleep", "fi", "esac", "done", "trap", "wait", "source", ".", "case", "in",
    "for", "select", "function",
    # the workflows activate gcloud; the runner does not need it (run_gcp_credentials, below)
    "gcloud",
}
# helpers of the runner with no workflow `run:` counterpart: log markers, the GCP key file, the
# single-writer guard, and the actions/cache steps (`uses:` steps, not `run:` lines)
HOST_ONLY = {
    "begin", "run_gcp_credentials", "run_publish_guard", "pl_cache_restore", "pl_cache_save",
    "pl_notice", "pl_log", "pl_die", "run_status",
}
KEYWORDS = {"then", "else", "do", "elif", "if", "while", "until", "!", "{", "time"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class Unknown(Exception):
    """A tool command in a form this checker does not understand."""


def join_continuations(lines):
    """Yield (number, text) with backslash-continued lines joined."""
    buf, start = "", 0
    for number, raw in lines:
        text = raw.rstrip("\n")
        if not buf:
            start = number
        if text.rstrip().endswith("\\"):
            buf += text.rstrip()[:-1] + " "
            continue
        yield start, buf + text
        buf = ""
    if buf:
        yield start, buf


def tokens(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def segments(line):
    """Split one command line into command segments (lists of words)."""
    segs, cur = [], []
    toks = tokens(line)
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[();&|]+", t):
            if cur:
                segs.append(cur)
            cur = []
        elif re.fullmatch(r"[<>&]+", t) and ("<" in t or ">" in t):
            i += 1  # a redirection and its target
        else:
            cur.append(t)
        i += 1
    if cur:
        segs.append(cur)
    return segs


def strip_prefix(words):
    """Drop leading assignments (`NAME=value`) and shell keywords (`then`, `if` ...)."""
    i = 0
    while i < len(words) and (ASSIGN.match(words[i]) or words[i] in KEYWORDS):
        i += 1
    return words[i:]


def py_entry(args):
    """The entry point of `python ARGS`: the arguments, minus options that do not change it."""
    args = [a for a in args if a != "--no-push"]
    return "py " + " ".join(args)


def lib_entry(args):
    """pipeline-lib.sh COMMAND ARGS (the workflow form)."""
    if not args:
        raise Unknown("pipeline-lib.sh without a command")
    cmd, rest = args[0], args[1:]
    if cmd == "static-inputs" and rest:
        return "lib static-inputs " + rest[0]  # the cache directory is a host detail
    if cmd == "restore-fallback" and rest:
        return "lib restore-fallback " + " ".join(rest)
    if cmd == "prev-sqlite" and not rest:
        return "lib prev-sqlite"
    raise Unknown("pipeline-lib.sh " + " ".join(args))


def uv_run_entry(words):
    """words = ['uv', 'run', ...]"""
    i = 2
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in ("--python", "-p") else 1
    if i < len(words) and words[i] in ("python", "python3"):
        return py_entry(words[i + 1:])
    raise Unknown(" ".join(words))


class Runner:
    """The functions of pipeline-run.sh and pipeline-lib.sh, and how to flatten a call."""

    def __init__(self, runner_text, job):
        self.functions = {}
        self.job = job
        name = None
        for line in runner_text.splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{", line)
            if m:
                name = m.group(1)
                self.functions[name] = []
            elif line.startswith("}"):
                name = None
            elif name is not None:
                self.functions[name].append(line)

    def arm_selected(self, pattern):
        for alt in pattern.split("|"):
            alt = alt.strip().strip("\"'")
            if alt in ("*", self.job, "$job"):
                return True
        return False

    def lines_of(self, name):
        """The commands of a function body: comments out, case arms filtered to the job."""
        out = []
        for line in self.functions.get(name, []):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            arm = re.match(r"^([A-Za-z0-9_*\"'|$ -]+)\)\s*(.*?)\s*(;;)?$", text)
            if arm and not text.startswith("(") and arm.group(1).strip() not in ("if",):
                # `pattern) command ;;` (a case arm); a bare `pattern)` has no command here
                if self.arm_selected(arm.group(1)) and arm.group(2):
                    out.append(arm.group(2))
                continue
            out.append(text)
        return out

    def expand(self, name, depth=0):
        if depth > 6:
            raise Unknown("functions call each other too deep at " + name)
        entries = []
        for _, text in join_continuations(enumerate(self.lines_of(name))):
            entries.extend(self.line_entries(text, depth))
        # if/else variants of the same call count once
        dedup = []
        for e in entries:
            if not dedup or dedup[-1] != e:
                dedup.append(e)
        return dedup

    def line_entries(self, line, depth):
        if "git status --porcelain -- data/" in line:
            return ["detect-data"]
        entries = []
        try:
            segs = segments(line)
        except ValueError:
            segs = [line.split()]
        for seg in segs:
            entries.extend(self.command_entries(strip_prefix(seg), depth))
        return entries

    def command_entries(self, w, depth):
        if not w:
            return []
        cmd = w[0]
        if cmd in GLUE or cmd in HOST_ONLY or cmd in KEYWORDS:
            return []
        if cmd in WRAPPERS:
            return self.command_entries(strip_prefix([a for a in w[1:] if not a.startswith("-")]), depth)
        if cmd == "timeout":
            i = 1
            while i < len(w) and w[i].startswith("-"):
                i += 2 if w[i] in ("-k", "-s") else 1
            return self.command_entries(strip_prefix(w[i + 1:]), depth)  # w[i] is the duration
        if cmd == "run_with_push_auth":
            return self.command_entries(strip_prefix(w[1:]), depth)
        if cmd == "uv":
            if w[1:3] == ["sync", "--frozen"] and len(w) == 3:
                return ["uv-sync"]
            if w[1:2] == ["run"]:
                return [uv_run_entry(w)]
            raise Unknown(" ".join(w))
        if cmd == "bash" and len(w) >= 3 and w[1] == "deploy/vps/pipeline-lib.sh":
            return [lib_entry(w[2:])]
        if cmd == "pl_static_inputs" and len(w) >= 2:
            return ["lib static-inputs " + w[1]]
        if cmd == "pl_restore_fallback" and len(w) >= 2:
            return ["lib restore-fallback " + " ".join(w[1:])]
        if cmd == "pl_prev_sqlite":
            return ["lib prev-sqlite"]
        if cmd == "be":
            if len(w) < 3:
                raise Unknown(" ".join(w))
            return self.command_entries(w[2:], depth)
        if cmd in self.functions:
            return self.expand(cmd, depth + 1)
        if cmd in TOOLS or cmd.startswith("./") or cmd.startswith("/"):
            raise Unknown(" ".join(w))
        return []


def workflow_lines(text):
    """(line number, command line) of every `run:` step of a workflow file."""
    lines = text.splitlines()
    i = 0
    out = []
    while i < len(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        key_col = lines[i].index("run:")
        value = m.group(2).strip()
        if value and not re.match(r"^[|>][+-]?\s*(#.*)?$", value):
            out.append((i + 1, value))
            i += 1
            continue
        i += 1
        while i < len(lines):
            line = lines[i]
            if line.strip() and (len(line) - len(line.lstrip())) <= key_col:
                break
            if line.strip():
                out.append((i + 1, line.strip()))
            i += 1
    return out


def workflow_entries(text, runner):
    """The ordered entry points of the run steps of a workflow."""
    entries = []
    pending = list(workflow_lines(text))
    for number, line in join_continuations((n, t) for n, t in pending):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            entries.extend(runner.line_entries(stripped, 0))
        except Unknown as exc:
            raise Unknown("line %d: %s" % (number, exc))
    return entries


def first_difference(a, b):
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else "(nothing)"
        y = b[i] if i < len(b) else "(nothing)"
        if x != y:
            return i, x, y
    return None


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workflow", required=True)
    p.add_argument("--runner", required=True)
    p.add_argument("--lib", required=True)
    p.add_argument("--function", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--min-entries", type=int, default=1)
    args = p.parse_args(argv)
    texts = []
    for path in (args.workflow, args.runner, args.lib):
        with open(path, encoding="utf-8", errors="replace") as fh:
            texts.append(fh.read())
    runner = Runner(texts[1] + "\n" + texts[2], args.job)
    if args.function not in runner.functions:
        print("step parity: %s has no function %s" % (args.runner, args.function))
        return 1
    try:
        wf = workflow_entries(texts[0], runner)
    except Unknown as exc:
        print("step parity: %s has a command the parity check does not understand: %s" % (args.workflow, exc))
        return 1
    try:
        host = runner.expand(args.function)
    except Unknown as exc:
        print("step parity: %s (%s) has a command the parity check does not understand: %s"
              % (args.runner, args.function, exc))
        return 1
    if len(wf) < args.min_entries:
        print("step parity: only %d entry points found in %s (parser broken?)" % (len(wf), args.workflow))
        return 1
    diff = first_difference(wf, host)
    if diff:
        i, x, y = diff
        print("step parity: %s and %s (%s) differ at entry %d:" % (args.workflow, args.runner, args.function, i + 1))
        print("  workflow: %s" % x)
        print("  runner:   %s" % y)
        print("  workflow order: " + " | ".join(wf))
        print("  runner order:   " + " | ".join(host))
        return 1
    print("step parity: %s matches %s (%s): %d entry points in the same order" % (args.workflow, args.runner, args.function, len(wf)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
