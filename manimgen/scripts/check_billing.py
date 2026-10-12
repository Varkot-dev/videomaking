"""Prove that manimgen cannot spend money, using one tiny real Claude call.

Run from the manimgen/ folder:  python scripts/check_billing.py

It makes one minimal call through the same claude_cli path the pipeline uses
(this counts as a tiny amount of plan usage) and reports:
  - how Claude Code authenticated (must be the subscription login, not an API key)
  - whether "extra usage" (overage) billing is off for the account
  - how much of the 5-hour and 7-day allowances is used
Exit code 0 means no per-token or overage charges are possible right now.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ["LLM_PROVIDER"] = "claude_cli"
os.environ.pop("MANIMGEN_ALLOW_PAID_API", None)  # test the guard, never bypass it

from manimgen import llm  # noqa: E402


def _when(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch)) if epoch else "unknown"


def main() -> int:
    try:
        llm.chat(system="Reply with the single word OK.", user="ping")
    except llm.PaidApiBlockedError as exc:
        print(f"FAIL (guard stopped the call): {exc}")
        return 1
    except Exception as exc:  # not logged in, CLI missing, network, ...
        print(f"FAIL (could not make the call): {exc}")
        return 2

    info = llm._last_plan_info
    overage = info.get("overageStatus", "unknown")
    print("Authentication : Claude subscription login (no API key was used)")
    print(f"Extra usage    : {overage} ({info.get('overageDisabledReason', 'n/a')})")
    for name, (used, resets) in llm._plan_windows.items():
        print(f"{name:<15}: {used:.0%} used, resets {_when(resets)}")

    if overage == "rejected":
        print("PASS: extra usage is off, so hitting your plan limit only pauses work.")
        return 0
    print(
        "WARNING: extra usage is NOT off for this account. manimgen will stop at "
        f"{llm._plan_utilization_limit():.0%} of your allowance to avoid overage "
        "charges, but turning extra usage off in your Claude settings is safer."
    )
    return 3


if __name__ == "__main__":
    sys.exit(main())
