"""The round-box tap rule lives in a DOM-free JS module; run it under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROUNDS = Path(__file__).resolve().parents[1] / (
    "src/shadowdark_bot/web/static/assets/rounds.js"
)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_tap_rule():
    script = (
        f"import {{ usedAfterTap }} from {json.dumps(ROUNDS.as_uri())};"
        "const out = {};"
        # [max, used, tapped box] -> new used
        "for (const [m, u, n] of %s) out[[m, u, n]] = usedAfterTap(m, u, n);"
        "console.log(JSON.stringify(out));"
    )
    cases = [
        (5, 0, 1), (5, 0, 5), (5, 0, 3),  # all filled: any tap spends one round
        (5, 2, 3), (5, 2, 1),             # 3 left: tapping a filled box spends one
        (5, 4, 1),                         # last round spent -> 0 filled (ended)
        (5, 5, 1), (5, 5, 5), (5, 3, 4),   # empty box: refill up to and including it
        (5, 3, 2),                         # 2 left, box 2 is filled: spend one
    ]
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script % json.dumps(cases)],
        capture_output=True, text=True, check=True,
    )
    got = json.loads(res.stdout)
    assert got == {
        "5,0,1": 1, "5,0,5": 1, "5,0,3": 1,
        "5,2,3": 3, "5,2,1": 3,
        "5,4,1": 5,
        "5,5,1": 4, "5,5,5": 0, "5,3,4": 1,
        "5,3,2": 4,
    }
