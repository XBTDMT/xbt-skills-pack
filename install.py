#!/usr/bin/env python3
"""Install xbt-skills-pack into this computer's Claude Code folder.

  python install.py                         the skills, and the doc-guardrails end-of-turn check (a Stop hook)
  python install.py --no-doc-check          the skills only, no hook
  python install.py --with-doctrine         also the doctrine: its SessionStart hook, a template ~/.claude/CLAUDE.md,
                                            and (with --profile) the PowerShell `cc` command
  python install.py --with-plugins          also the plugins listed in plugins.json
  python install.py --dry-run               print what would change, change nothing

setup.ps1 runs this on Windows and passes --profile (the PowerShell profile to add `cc` to). Safe to run again:
each step replaces its own earlier result and nothing else, and every file it would overwrite is first copied
to <name>.bak-<timestamp>.

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
SKILLS = ("closeout", "project-update", "eli5", "redman", "greenman", "doc-guardrails", "axiom-macos", "axiom-swiftui")
# doc-guardrails checks documents in three layers. This installer registers the end-of-turn check,
# skills/doc-guardrails/turn_check.py (a Stop hook in Python, so it runs on Windows too): it checks every linked-kind
# document the session changed since its previous turn, however it was written. The per-write hook, hook.sh, is a zsh
# script for macOS and Linux and is left for you to add by hand (README: "The per-write hook"). The third layer, the
# commit gate, is added per project by /closeout.
DOCTRINE_FILES = ("gate.py", "opus-layer.md", "fable-layer.md", "desktop-architect.md", "SEATS.md")
PROFILE_BEGIN = "# >>> xbt-skills-pack >>>"
PROFILE_END = "# <<< xbt-skills-pack <<<"
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


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

    def settings(self, python_exe, doctrine=False, doc_check=True):
        self.say("Settings: " + " and ".join(n for n, on in (("the doctrine hook", doctrine),
                                                              ("the doc-guardrails turn check", doc_check)) if on))
        path = self.dir / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError as e:
            self.problems.append(f"{path} is not valid JSON ({e}); fix it by hand and run the installer again")
            self.say(f"  STOP    {self.problems[-1]}")
            return
        merged = data
        if doctrine:
            merged = merge_hook(merged, python_exe, str(self.dir / "doctrine" / "gate.py"))
        if doc_check:
            merged = merge_turn_check(merged, python_exe, str(self.dir / "skills" / "doc-guardrails" / "turn_check.py"))
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
        new = replace_block(text, block)
        if new == text:
            self.say(f"  same    {path}")
            return
        self.backup(path)
        self.say(f"  write   {path}")
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding=encoding)


def same_tree(a, b):
    def files(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts}
    return files(a) == files(b)


def last_line(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1][:200] if lines else "(no output)"


def hook_files(hook):
    """The file names a hook runs: every word of its command and every argument, quotes and paths stripped."""
    import re as _re
    words = _re.findall(r'"[^"]*"|\'[^\']*\'|\S+', hook.get("command", "")) + [str(a) for a in hook.get("args", [])]
    return {_re.split(r"[\\/]", w.strip("\"'"))[-1] for w in words}


def is_gate_hook(hook):
    return "gate.py" in hook_files(hook)


def merge_hook(settings, python_exe, gate):
    """Settings with exactly one doctrine hook on SessionStart, in exec form: Claude Code starts python directly
    with the gate's path as its argument, so no shell (Git Bash, PowerShell, sh) is involved on any platform.
    Every other setting and every other hook is kept as it was."""
    merged = json.loads(json.dumps(settings))
    groups = merged.setdefault("hooks", {}).setdefault("SessionStart", [])
    for group in groups:
        group["hooks"] = [h for h in group.get("hooks", []) if not is_gate_hook(h)]
    groups[:] = [g for g in groups if g.get("hooks")]
    groups.append({"hooks": [{"type": "command", "command": python_exe, "args": [gate], "timeout": 10}]})
    return merged


def is_turn_check_hook(hook):
    return "turn_check.py" in hook_files(hook)


def merge_turn_check(settings, python_exe, script):
    """Settings with exactly one doc-guardrails turn check on Stop, in exec form; every other Stop hook is kept."""
    merged = json.loads(json.dumps(settings))
    groups = merged.setdefault("hooks", {}).setdefault("Stop", [])
    for group in groups:
        group["hooks"] = [h for h in group.get("hooks", []) if not is_turn_check_hook(h)]
    groups[:] = [g for g in groups if g.get("hooks")]
    groups.append({"hooks": [{"type": "command", "command": python_exe, "args": [script], "timeout": 30}]})
    return merged


def replace_block(text, block):
    """The profile with the pack's block in it once: an earlier block is replaced where it stands."""
    if PROFILE_BEGIN in text and PROFILE_END in text:
        start = text.index(PROFILE_BEGIN)
        end = text.index(PROFILE_END, start) + len(PROFILE_END)
        if end < len(text) and text[end] == "\n":
            end += 1
        return text[:start] + block + text[end:]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + block


def claude_dir():
    return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() if os.environ.get("CLAUDE_CONFIG_DIR") \
        else Path.home() / ".claude"


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-doctrine", action="store_true",
                    help="also install the doctrine hook, a template CLAUDE.md and (with --profile) the cc command")
    ap.add_argument("--with-plugins", action="store_true", help="also install the plugins in plugins.json")
    ap.add_argument("--no-doc-check", action="store_true",
                    help="do not register the doc-guardrails end-of-turn check (a Stop hook in settings.json)")
    ap.add_argument("--profile", help="PowerShell profile to add the cc command to, with --with-doctrine "
                                      "(setup.ps1 passes it)")
    ap.add_argument("--claude-dir", help="Claude Code folder (default: CLAUDE_CONFIG_DIR, else ~/.claude)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is needed; this is " + sys.version.split()[0])
        return 2
    claude = shutil.which("claude")
    if not claude and args.with_plugins:
        print("Claude Code is not on PATH. Install it first (PowerShell: irm https://claude.ai/install.ps1 | iex),\n"
              "open a new terminal, and run this again.")
        return 2
    if not shutil.which("git"):
        print("Note: git is not on PATH. /closeout and /project-update need it; install git.\n")
    inst = Installer(Path(args.claude_dir) if args.claude_dir else claude_dir(), args.dry_run, claude or "claude")
    print(f"Installing into {inst.dir}{'  (dry run: nothing is changed)' if args.dry_run else ''}\n")
    inst.skills()
    if args.with_doctrine:
        inst.doctrine()
        inst.claude_md()
        if args.profile:
            inst.profile(args.profile)
    else:
        print("Doctrine\n  skipped (add --with-doctrine to install it)")
    if args.with_doctrine or not args.no_doc_check:
        inst.settings(sys.executable, doctrine=args.with_doctrine, doc_check=not args.no_doc_check)
    if args.with_plugins:
        inst.plugins(json.loads((KIT / "plugins.json").read_text(encoding="utf-8")))
    else:
        print("Plugins\n  skipped (add --with-plugins to install them)")
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
