"""A JSGF command grammar for pocketsphinx, mirroring what `parser.py` understands.

Constraining Sphinx to this grammar is what makes it usable without a model download: it can
only hear sentences the parser can execute, so a misrecognition becomes a *different valid
command* far less often than it becomes no match at all. Words are limited to CMUdict.
"""
from __future__ import annotations

from pathlib import Path

JSGF = """#JSGF V1.0;
grammar duck;

public <utterance> = [<wake>] <action> [(and | then | and then) <action>];

<wake> = [hey] micro duck | duck;
<action> = <walk> | <turn> | <stop> | <sim> | <env> | <camera> | <panel> | <skill> | <nav> | <exp> | <report> | <push>;

<num> = one | two | three | four | five | six | seven | eight | nine | ten | fifteen | twenty | thirty
      | forty five | sixty | ninety | one hundred eighty | three hundred sixty;
<speed> = slowly | quickly | slow | fast;

<walk> = (walk | go | move | step) [<speed>] (forward | backward | ahead | back | sideways left | sideways right)
         [<speed>] [for <num> (seconds | second)] [<num> meters];
<turn> = (turn | rotate | spin) [<speed>] [(left | right | around)] [<speed>] [by <num> degrees | <num> degrees];
<stop> = stop [moving] [immediately | now] | emergency stop | freeze | halt | clear [the] emergency stop;
<sim> = (pause | resume | reset | restart) [the] [simulation | robot] [and start again];
<env> = (switch | go | change) to [the] (laboratory | lab | garden | arena | apartment)
        [with [the] (slalom | corridor | scatter | open) layout];
<camera> = [switch] [the] camera [to] (follow | orbit | top | first person) [mode] | track the robot | reset the camera;
<panel> = (show | hide) [me] [the] (joint angles | joint velocities | telemetry | contacts | logs | experiments);
<skill> = sit down | stand up | get up | kick [with] [the] (left | right) [foot] | do a roll | roll | pick it up;
<nav> = (go | walk | navigate) to [the] (goal (one | two | three) | start) | return to the start
        | follow the way points | visit all the goals;
<exp> = (start | run) [the | a] (walking | turning | push | navigation | slalom) test [<num> times]
        | run the experiment [again] [<num> times] | save this experiment | compare the last two runs;
<report> = what is the status | status report | explain the status | why did it fall | help;
<push> = (push | nudge) [it] [gently | hard] [from the (left | right | front | back)];
"""


def write_grammar(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "duck.gram"
    if not path.exists() or path.read_text() != JSGF:
        path.write_text(JSGF)
    return path
