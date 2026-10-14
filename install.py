#!/usr/bin/env python3
"""Install xbt-skills-pack into this computer's Claude Code folder.

What it does with no switches is written in install-defaults.json beside this file (its four keys: doctrine,
plugins, doc_check, skill_checks), so each copy of the pack says what its own setup installs. The switches turn a step on or off
from there:

  python install.py                         what install-defaults.json says
  python install.py --with-doctrine         also the doctrine: its SessionStart hook (the layer) and SubagentStart hook
                                            (every agent's key rules), a template ~/.claude/CLAUDE.md, and (with
                                            --profile) the PowerShell `cc` command
  python install.py --with-plugins          also the plugins listed in plugins.json
  python install.py --skip-plugins          not the plugins, whatever the defaults say
  python install.py --no-doc-check          not the doc-guardrails end-of-turn check (a Stop hook)
  python install.py --no-skill-checks       not eli5's two hooks: the skill freshness notice (UserPromptSubmit, and
                                            SessionStart on resume) and the live receipt check (Stop)
  python install.py --dry-run               print what would change, change nothing
  python install.py --print-options         print the options that would apply, as JSON, and install nothing
  python install.py --defaults PATH         read the defaults from PATH instead
  python install.py --claude-dir DIR        install into DIR (default: CLAUDE_CONFIG_DIR, else ~/.claude)

A missing or malformed install-defaults.json stops the install before anything is written: a copy that lost its
defaults must never quietly install less than it says. setup.ps1 runs this on Windows and passes --profile (the
PowerShell profile to add `cc` to). Safe to run again: each step replaces its own earlier result and nothing else,
and every file it would overwrite is first copied to <name>.bak-<timestamp>.

Stdlib only, Python 3.9 or newer.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
DEFAULTS = KIT / "install-defaults.json"
OPTIONS = ("doctrine", "plugins", "doc_check", "skill_checks")   # the keys install-defaults.json holds, beside "_about"


def shipped_skills(root=KIT):
    """The skills this copy ships: the folders under skills/ that hold a SKILL.md. There is no second list, so a
    copy that ships fewer skills (or more) installs exactly those."""
    folder = Path(root) / "skills"
    return tuple(sorted(p.name for p in folder.iterdir() if (p / "SKILL.md").is_file())) if folder.is_dir() else ()


SKILLS = shipped_skills()
# doc-guardrails checks documents in three layers. This installer registers the end-of-turn check,
# skills/doc-guardrails/turn_check.py (a Stop hook in Python, so it runs on Windows too): it checks every linked-kind
# document the session changed since its previous turn, however it was written. The per-write hook, hook.sh, is a zsh
# script for macOS and Linux and is left for you to add by hand (README: "The per-write hook"). The third layer, the
# commit gate, is added per project by /closeout.
DOCTRINE_FILES = ("gate.py", "opus-layer.md", "opus-5-5-layer.md", "fable-layer.md", "desktop-architect.md", "SEATS.md",
                  "brief.py", "key-rules.md")
PROFILE_BEGIN = "# >>> xbt-skills-pack >>>"
PROFILE_END = "# <<< xbt-skills-pack <<<"
# The markers an older installer wrote. Its block is replaced where it stands, so a line the user put above it (such
# as $CcEngineerModel) stays above the new one.
LEGACY_MARKERS = [("# >>> claude starter kit >>>", "# <<< claude starter kit <<<")]
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


class DefaultsError(Exception):
    pass


def load_defaults(path=DEFAULTS):
    """{doctrine, plugins, doc_check, skill_checks} from the defaults file, or DefaultsError naming what is wrong. Never a silent
    default: a missing file, invalid JSON, a missing or unknown key or a value that is not true or false stops."""
    path = Path(path)

    def bad(why):
        return DefaultsError(f"{path.name}: {why}; run git pull (or restore the file) and run setup again")
    try:
        raw = path.read_text(encoding="utf-8-sig")        # a BOM from a Windows editor is not an error
    except FileNotFoundError:
        raise bad(f"not found at {path}")
    except OSError as e:
        raise bad(f"cannot be read ({e})")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise bad(f"not valid JSON ({e})")
    if not isinstance(data, dict):
        raise bad("not a JSON object")
    unknown = sorted(set(data) - set(OPTIONS) - {"_about"})
    if unknown:
        raise bad("unknown key " + ", ".join(unknown))
    for key in OPTIONS:
        if key not in data:
            raise bad(f"{key} is missing")
        if not isinstance(data[key], bool):
            raise bad(f"{key} is not true or false")
    return {key: data[key] for key in OPTIONS}


class Installer:
    def __init__(self, claude_dir, dry_run=False, claude="claude", out=print):
        self.dir = Path(claude_dir)
        self.dry = dry_run
        self.claude = claude
        self.say = out
        self.problems = []

    # ------------------------------------------------------------------------------------------------ files

    def backup(self, path):
        if path.exists():
            # A skill folder is backed up outside skills/: a copy left beside the skills would load as a second skill.
            in_skills = path.parent == self.dir / "skills"
            dest = (self.dir / "skill-backups" if in_skills else path.parent) / f"{path.name}.bak-{STAMP}"
            self.say(f"  backup  {path} -> {dest.name}")
            if not self.dry:
                dest.parent.mkdir(parents=True, exist_ok=True)
                (shutil.copytree if path.is_dir() else shutil.copy2)(path, dest)

    def copy_tree(self, src, dest):
        if dest.exists() and same_tree(src, dest):
            self.say(f"  same    {dest}")
            return
        self.backup(dest)
        self.say(f"  write   {dest}")
        if not self.dry:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    def doctrine(self):
        self.say("Doctrine")
        dest = self.dir / "doctrine"
        for name in DOCTRINE_FILES:
            src, target = KIT / "doctrine" / name, dest / name
            if target.is_file() and target.read_bytes() == src.read_bytes():
                self.say(f"  same    {target}")
                continue
            self.backup(target)
            self.say(f"  write   {target}")
            if not self.dry:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        cc = KIT / "powershell" / "cc.ps1"
        target = dest / "cc.ps1"
        if target.is_file() and target.read_bytes() == cc.read_bytes():
            self.say(f"  same    {target}")
        else:
            self.backup(target)
            self.say(f"  write   {target}")
            if not self.dry:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cc, target)

    def skills(self):
        self.say("Skills")
        for name in SKILLS:
            self.copy_tree(KIT / "skills" / name, self.dir / "skills" / name)

    def claude_md(self):
        self.say("Personal CLAUDE.md")
        target = self.dir / "CLAUDE.md"
        if target.exists():
            self.say(f"  keep    {target} (already exists; templates/CLAUDE.md is there to compare against)")
            return
        self.say(f"  write   {target}")
        if not self.dry:
            self.dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(KIT / "templates" / "CLAUDE.md", target)

    # ------------------------------------------------------------------------------------------------ settings

    def settings(self, python_exe, doctrine=False, doc_check=True, skill_checks=True):
        self.say("Settings: " + " and ".join(n for n, on in (("the doctrine hook", doctrine),
                                                              ("the doc-guardrails turn check", doc_check),
                                                              ("eli5's skill checks", skill_checks)) if on))
        path = self.dir / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError as e:
            self.problems.append(f"{path} is not valid JSON ({e}); fix it by hand and run the installer again")
            self.say(f"  STOP    {self.problems[-1]}")
            return
        merged = registered(data, python_exe, self.dir, doctrine=doctrine, doc_check=doc_check,
                            skill_checks=skill_checks)
        if merged == data:
            self.say(f"  same    {path}")
            return
        self.backup(path)
        self.say(f"  write   {path}")
        if not self.dry:
            self.dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------------------------------------ plugins

    def run_claude(self, *args):
        cmd = [self.claude, *args]
        self.say("  run     " + " ".join(cmd))
        if self.dry:
            return 0, ""
        try:
            # stdin closed: a plugin that wants to ask a question fails here instead of hanging the install.
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                               errors="replace", timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)
        return r.returncode, (r.stdout + r.stderr).strip()

    def plugins(self, manifest):
        self.say("Plugins")
        code, listing = self.run_claude("plugin", "marketplace", "list")
        for name, repo in manifest["marketplaces"].items():
            if code == 0 and name in listing:
                self.say(f"  have    marketplace {name}")
                continue
            rc, out = self.run_claude("plugin", "marketplace", "add", repo)
            if rc != 0 and "already" not in out.lower():
                self.problems.append(f"marketplace {name} ({repo}) did not add: {last_line(out)}")
        for p in manifest["plugins"]:
            # --scope user everywhere: left to auto-detect, a switch can land in the settings of whatever folder
            # setup was started from instead of the user's own.
            rc, out = self.run_claude("plugin", "install", p["id"], "--scope", "user")
            if rc != 0 and "already" not in out.lower():
                self.problems.append(f"plugin {p['id']} did not install: {last_line(out)}")
                continue
            rc, out = self.run_claude("plugin", "enable" if p["enabled"] else "disable", p["id"], "--scope", "user")
            if rc != 0 and "already" not in out.lower():
                self.problems.append(f"plugin {p['id']} could not be switched "
                                     f"{'on' if p['enabled'] else 'off'}: {last_line(out)}")

    # ------------------------------------------------------------------------------------------------ powershell

    def profile(self, profile_path):
        self.say("PowerShell: the cc command")
        path = Path(profile_path)
        block = f'{PROFILE_BEGIN}\n. "{self.dir / "doctrine" / "cc.ps1"}"\n{PROFILE_END}\n'
        raw = path.read_bytes() if path.is_file() else None
        text = raw.decode("utf-8-sig", "replace") if raw is not None else ""
        # Windows PowerShell 5.1 reads a BOM-less profile as ANSI. A new profile gets a BOM so its UTF-8 is read as
        # UTF-8; an existing one keeps the encoding it had, because adding or dropping a BOM rewrites how every
        # accented character already in it is read.
        encoding = "utf-8-sig" if raw is None or raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        # Decoding bytes leaves CRLF standing, so compare in LF and put the file's own ending back on write:
        # otherwise a CRLF profile never equals the LF block below and every run rewrites (and backs up) the profile.
        newline = "\r\n" if "\r\n" in text else ("\n" if raw is not None else None)  # a new file: the platform's own
        text = text.replace("\r\n", "\n")
        new = replace_block(text, block)
        if new == text:
            self.say(f"  same    {path}")
            return
        self.backup(path)
        self.say(f"  write   {path}")
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding=encoding, newline=newline) as fh:  # newline=: 3.9 has no Path kwarg
                fh.write(new)


def same_tree(a, b):
    def files(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts}
    return files(a) == files(b)


def last_line(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1][:200] if lines else "(no output)"


# The tolerant launcher: every hook entry this installer writes runs its script through this short program, passed
# inline with -c, so it can never be the missing file. A script that is not there is one stderr line and exit 0
# (Python itself would exit 2, which Claude Code reads as "block" on PreToolUse, UserPromptSubmit and Stop). Otherwise
# the script runs in this process as its own __main__, with its own arguments, folder, stdin, stdout, stderr and exit
# code. Only when XBT_EVAL_HOOK_LOG names a file (only the evals set it) does the launcher read stdin first and hash
# what the script prints, then append one JSON line {event, script, exit, stdout_sha256, t} to that file in one
# O_APPEND write; a line it cannot write costs one stderr line and changes nothing else. On Windows the descriptor is
# opened binary (text mode would write \r\n) and the write holds a lock on one byte 1 GiB in: Windows' O_APPEND is a
# seek to the end and then a write, so two hooks finishing together could write at one offset and lose a line.
LAUNCHER = r"""import os, runpy, sys
script = sys.argv[1] if len(sys.argv) > 1 else ""
if not os.path.isfile(script):
    sys.stderr.write("hook script absent: %s; nothing run\n" % script)
    sys.exit(0)
sys.argv = sys.argv[1:]
sys.path[0] = os.path.dirname(os.path.abspath(script))
log = os.environ.get("XBT_EVAL_HOOK_LOG", "")
if not log:
    runpy.run_path(script, run_name="__main__")
    sys.exit(0)
import datetime, hashlib, io, json
data = sys.stdin.buffer.read() if sys.stdin is not None else b""
try:
    event = json.loads(data.decode("utf-8")).get("hook_event_name") or ""
except Exception:
    event = ""
real, sha = sys.stdout, hashlib.sha256()
class Tee(io.RawIOBase):
    def writable(self):
        return True
    def write(self, b):
        sha.update(b)
        real.buffer.write(bytes(b))
        return len(b)
    def flush(self):
        real.buffer.flush()
sys.stdin = io.TextIOWrapper(io.BytesIO(data), encoding=real.encoding if sys.stdin is None else sys.stdin.encoding)
sys.stdout = io.TextIOWrapper(io.BufferedWriter(Tee()), encoding=real.encoding, errors=real.errors, line_buffering=True)
code = 0
try:
    runpy.run_path(script, init_globals={"__xbt_launched__": True}, run_name="__main__")
except SystemExit as e:
    if e.code is None:
        code = 0
    elif isinstance(e.code, int):
        code = int(e.code)
    else:
        sys.stderr.write("%s\n" % (e.code,))
        code = 1
except BaseException:
    import traceback
    traceback.print_exc()
    code = 1
try:
    sys.stdout.flush()
except Exception:
    pass
sys.stdout = real
t = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
line = json.dumps({"event": event if isinstance(event, str) else "", "script": os.path.realpath(script),
                   "exit": code, "stdout_sha256": sha.hexdigest(), "t": t}, ensure_ascii=False) + "\n"
try:
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 1 << 30, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        os.write(fd, line.encode("utf-8"))
        if os.name == "nt":
            os.lseek(fd, 1 << 30, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)
except OSError as e:
    sys.stderr.write("hook log not written: %s\n" % e)
sys.exit(code)
"""


def launched(python_exe, script, timeout, args=()):
    """The hook entry that runs `script` (with its own `args`) through the tolerant launcher: exactly the keys type,
    command, args and timeout. No matcher: a matcher belongs to the group its caller builds."""
    return {"type": "command", "command": python_exe, "args": ["-c", LAUNCHER, str(script), *args], "timeout": timeout}


def hook_files(hook):
    """The file names a hook runs. A launcher entry runs exactly one file, args[2]; any other shape: every word of its
    command and every argument, quotes and paths stripped."""
    import re as _re
    args = [str(a) for a in hook.get("args") or []]
    if args[:1] == ["-c"]:
        return {_re.split(r"[\\/]", args[2])[-1]} if len(args) > 2 else set()
    words = _re.findall(r'"[^"]*"|\'[^\']*\'|\S+', hook.get("command", "")) + [str(a) for a in hook.get("args", [])]
    return {_re.split(r"[\\/]", w.strip("\"'"))[-1] for w in words}


def is_gate_hook(hook):
    return "gate.py" in hook_files(hook)


def add_hook_group(settings, event, hook, matcher=None):
    """The one entry-merge step: `hook` appended to `event` in a group of its own (with its matcher, if any). Every
    registration here uses it, and so does the evals' sealed builder for the hooks this installer does not write."""
    groups = settings.setdefault("hooks", {}).setdefault(event, [])
    groups.append(dict({"matcher": matcher} if matcher else {}, hooks=[hook]))
    return settings


def without_hooks(settings, event, is_ours):
    """A copy of settings with every hook `is_ours` picks removed from `event` (a group left empty goes too)."""
    merged = json.loads(json.dumps(settings))
    groups = merged.setdefault("hooks", {}).setdefault(event, [])
    for group in groups:
        group["hooks"] = [h for h in group.get("hooks", []) if not is_ours(h)]
    groups[:] = [g for g in groups if g.get("hooks")]
    return merged


def merge_hook(settings, python_exe, gate):
    """Settings with exactly one doctrine hook on SessionStart, in exec form through the tolerant launcher: Claude
    Code starts python directly with the gate's path as an argument, so no shell (Git Bash, PowerShell, sh) is
    involved on any platform. Every other setting and every other hook is kept as it was."""
    return add_hook_group(without_hooks(settings, "SessionStart", is_gate_hook), "SessionStart",
                          launched(python_exe, gate, 10))


def is_brief_hook(hook):
    return "brief.py" in hook_files(hook)


BRIEF_EVENT = "SubagentStart"
OLD_BRIEF_EVENTS = ("PreToolUse",)   # where an earlier install registered it (for the Agent tool only)


def merge_brief_hook(settings, python_exe, script):
    """Settings with exactly one doctrine brief hook, on SubagentStart with no matcher (every agent, whatever started
    it: the Agent tool, a Workflow script, a skill), in exec form through the tolerant launcher (a missing script never
    blocks an agent); an earlier install's PreToolUse entry for it is taken out (an event it leaves empty goes too), so
    no agent gets the rules twice; every other hook is kept. It hands each agent the doctrine's key rules
    (key-rules.md) as it starts."""
    merged = without_hooks(settings, BRIEF_EVENT, is_brief_hook)
    for event in OLD_BRIEF_EVENTS:
        groups = (settings.get("hooks") or {}).get(event)
        if groups and any(is_brief_hook(h) for g in groups for h in g.get("hooks") or []):
            merged = without_hooks(merged, event, is_brief_hook)
            if not merged["hooks"][event]:
                del merged["hooks"][event]
    return add_hook_group(merged, BRIEF_EVENT, launched(python_exe, script, 5))


def is_turn_check_hook(hook):
    return "turn_check.py" in hook_files(hook)


def merge_turn_check(settings, python_exe, script):
    """Settings with exactly one doc-guardrails turn check on Stop, in exec form through the tolerant launcher (a
    missing script never blocks a stop); every other Stop hook is kept."""
    return add_hook_group(without_hooks(settings, "Stop", is_turn_check_hook), "Stop", launched(python_exe, script, 30))


def merge_script_hook(settings, event, python_exe, script, timeout, matcher=None):
    """Settings with exactly one entry on `event` that runs a script of this file name, through the tolerant launcher,
    in a group of its own (with the matcher, if one is given); every other hook on that event, and the same script on
    another event, is kept. The parameters follow launched()'s order, so no required one follows a default."""
    name = Path(str(script).replace("\\", "/")).name
    return add_hook_group(without_hooks(settings, event, lambda h: name in hook_files(h)), event,
                          launched(python_exe, script, timeout), matcher)


def registered(base, python_exe, claude_dir, doctrine=False, doc_check=True, skill_checks=True):
    """`base` (a settings value) with the pack's hooks merged in, for the options given; reads and writes no file.
    Every script path is built under `claude_dir`, the Claude Code folder the entries will run from. The installer
    passes the user's settings and its own folder; the evals' sealed builder passes {} and the sealed folder, so the
    hooks every user gets and the hooks the evals measure come from this one function."""
    merged = json.loads(json.dumps(base))
    if doctrine:
        merged = merge_hook(merged, python_exe, str(Path(claude_dir) / "doctrine" / "gate.py"))
        merged = merge_brief_hook(merged, python_exe, str(Path(claude_dir) / "doctrine" / "brief.py"))
    if doc_check:
        merged = merge_turn_check(merged, python_exe, str(Path(claude_dir) / "skills" / "doc-guardrails" / "turn_check.py"))
    if skill_checks:
        # eli5's two hooks: a session running on skill text that changed on disk since it loaded it is told which file
        # to Read (on every prompt, and when it resumes); with eli5 on, a tool-using turn that ends without the receipt
        # block is held once
        eli5 = Path(claude_dir) / "skills" / "eli5"
        merged = merge_script_hook(merged, "UserPromptSubmit", python_exe, str(eli5 / "skill_fresh.py"), 5)
        merged = merge_script_hook(merged, "SessionStart", python_exe, str(eli5 / "skill_fresh.py"), 5, matcher="resume")
        merged = merge_script_hook(merged, "Stop", python_exe, str(eli5 / "receipt_check.py"), 10)
    return merged


def _block_span(text, begin, end):
    """(start, stop) of the block from `begin` to `end`, its trailing newline included, or None."""
    if begin not in text:
        return None
    start = text.index(begin)
    if end not in text[start:]:
        return None
    stop = text.index(end, start) + len(end)
    if stop < len(text) and text[stop] == "\n":
        stop += 1
    return start, stop


def replace_block(text, block):
    """The profile with the pack's block in it once. The current block is replaced where it stands; else an older
    installer's block (LEGACY_MARKERS) is; else the block is appended. Any other block of either kind is removed, so
    the profile ends with exactly one."""
    current = _block_span(text, PROFILE_BEGIN, PROFILE_END)
    legacy = [s for s in (_block_span(text, b, e) for b, e in LEGACY_MARKERS) if s]
    found = ([current] if current else []) + legacy
    if not found:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + ("\n" if text else "") + block
    keep, out, pos = found[0], [], 0
    for start, stop in sorted(found):
        out.append(text[pos:start])
        if (start, stop) == keep:
            out.append(block)
        pos = stop
    return "".join(out) + text[pos:]


def claude_dir():
    return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() if os.environ.get("CLAUDE_CONFIG_DIR") \
        else Path.home() / ".claude"


def effective_options(defaults, args):
    """The options that apply: the defaults file's, turned on or off by the switches."""
    return {"doctrine": defaults["doctrine"] or args.with_doctrine,
            "plugins": (defaults["plugins"] or args.with_plugins) and not args.skip_plugins,
            "doc_check": defaults["doc_check"] and not args.no_doc_check,
            "skill_checks": defaults["skill_checks"] and not args.no_skill_checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-doctrine", action="store_true",
                    help="also install the doctrine hook, a template CLAUDE.md and (with --profile) the cc command")
    ap.add_argument("--with-plugins", action="store_true", help="also install the plugins in plugins.json")
    ap.add_argument("--skip-plugins", action="store_true", help="do not install the plugins, whatever the defaults say")
    ap.add_argument("--no-doc-check", action="store_true",
                    help="do not register the doc-guardrails end-of-turn check (a Stop hook in settings.json)")
    ap.add_argument("--no-skill-checks", action="store_true",
                    help="do not register eli5's skill freshness notice and live receipt check (hooks in settings.json)")
    ap.add_argument("--profile", help="PowerShell profile to add the cc command to, with the doctrine "
                                      "(setup.ps1 passes it)")
    ap.add_argument("--claude-dir", help="Claude Code folder (default: CLAUDE_CONFIG_DIR, else ~/.claude)")
    ap.add_argument("--defaults", help="the defaults file to read (default: install-defaults.json beside this file)")
    ap.add_argument("--print-options", action="store_true",
                    help="print the options that would apply, as JSON, and install nothing")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is needed; this is " + sys.version.split()[0])
        return 2
    if args.skip_plugins and args.with_plugins:
        print("--skip-plugins and --with-plugins say opposite things; pass one of them")
        return 2
    try:
        options = effective_options(load_defaults(Path(args.defaults) if args.defaults else DEFAULTS), args)
    except DefaultsError as e:
        print(e)
        return 2
    if args.print_options:
        print(json.dumps(options))
        return 0
    claude = shutil.which("claude")
    if not claude and options["plugins"]:
        print("Claude Code is not on PATH. Install it first (PowerShell: irm https://claude.ai/install.ps1 | iex),\n"
              "open a new terminal, and run this again.")
        return 2
    if not shutil.which("git"):
        print("Note: git is not on PATH. /closeout and /project-update need it; install git.\n")
    inst = Installer(Path(args.claude_dir) if args.claude_dir else claude_dir(), args.dry_run, claude or "claude")
    print(f"Installing into {inst.dir}{'  (dry run: nothing is changed)' if args.dry_run else ''}\n")
    inst.skills()
    if options["doctrine"]:
        inst.doctrine()
        inst.claude_md()
        if args.profile:
            inst.profile(args.profile)
    else:
        print("Doctrine\n  skipped (add --with-doctrine to install it)")
    if options["doctrine"] or options["doc_check"] or options["skill_checks"]:
        inst.settings(sys.executable, doctrine=options["doctrine"], doc_check=options["doc_check"],
                      skill_checks=options["skill_checks"])
    if options["plugins"]:
        inst.plugins(json.loads((KIT / "plugins.json").read_text(encoding="utf-8")))
    else:
        print("Plugins\n  skipped (--skip-plugins)" if args.skip_plugins else
              "Plugins\n  skipped (add --with-plugins to install them)")
    print()
    if inst.problems:
        print(f"Finished with {len(inst.problems)} problem(s):")
        for p in inst.problems:
            print("  - " + p)
        return 1
    print("Finished. Open a NEW terminal, then check it worked: see 'Check it worked' in README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
