"""Imported first by every test module that makes git repositories. Git (2.55 on the build machine) may start its own
background upkeep after a commit (`gc.auto`, `maintenance.auto`), which writes `.git/objects/maintenance.lock` while a
test reads or deletes the fixture: a test that asserts "nothing was written" then sees the lock, and an `rmtree` of the
fixture trips over it. Setting both keys through git's environment turns that upkeep off for every git the tests start,
without touching any repository's or the user's config."""
import os

_QUIET = (("gc.auto", "0"), ("maintenance.auto", "false"))


def _apply(env):
    n = int(env.get("GIT_CONFIG_COUNT") or 0)
    have = {env.get(f"GIT_CONFIG_KEY_{i}") for i in range(n)}
    for key, value in _QUIET:
        if key not in have:
            env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = key, value
            n += 1
    env["GIT_CONFIG_COUNT"] = str(n)


_apply(os.environ)
