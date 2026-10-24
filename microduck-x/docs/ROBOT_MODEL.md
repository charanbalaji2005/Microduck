# The robot model Microduck X uses — and does not change

Source: `microduck_rl/src/mjlab_microduck/robot/microduck/` (Onshape export via onshape-to-robot),
compiled with MuJoCo 3.10.0 — the version `microduck_rl/uv.lock` pins. Numbers below were read
from the compiled model, not from the XML by eye.

## Bodies, joints, actuators

* Floating base `trunk_base` (free joint `trunk_base_freejoint`), total mass **0.737 kg**.
* **14 actuated hinge joints**, named and ordered as `duck_ipc_proto::JOINT_NAMES` minus `mouth`:

| # (wire) | joint | range (deg) | # (wire) | joint | range (deg) |
|---|---|---|---|---|---|
| 0 | left_hip_yaw | −25 … 30 | 10 | right_hip_yaw | −30 … 25 |
| 1 | left_hip_roll | −22 … 22 | 11 | right_hip_roll | −22 … 22 |
| 2 | left_hip_pitch | −90 … 90 | 12 | right_hip_pitch | −90 … 90 |
| 3 | left_knee | −90 … 90 | 13 | right_knee | −90 … 90 |
| 4 | left_ankle | −90 … 90 | 14 | right_ankle | −90 … 90 |
| 5 | neck_pitch | −90 … 60 | 9 | *mouth* | not in the walking model |
| 6 | head_pitch | −90 … 90 | | | |
| 7 | head_yaw | −170 … 170 | | | |
| 8 | head_roll | −25 … 25 | | | |

* Actuators: MJCF position actuators, `kp = 0.55`, force range ±0.96 N·m, ctrl range ±10 rad
  (wide on purpose: "low-kp servos need overshoot", `microduck_rl/AGENTS.md`). The body server
  scales the gain by robotd's Dynamixel kp register ÷ 200 and zeroes it for torque-off.
* Sites: `imu`, `head_imu`, `head_camera`, `tof`, `left_foot`, `right_foot`, `mouth_tip`.
  Camera `head_camera` (the viewer's "head cam" uses its exact pose and fovy).
* MJCF sensors: `orientation`, `angular-velocity`, `imu_ang_vel`, `imu_lin_vel`, `imu_accel`,
  `root_angmom`. robotd's IMU input is computed by the body server from the trunk quaternion
  (projected gravity) and the free joint's angular velocity — not from these sensors.

## Collision families

| Scene file | Robot model | Collision geoms on the robot |
|---|---|---|
| `scene.xml` (`bare`, the body server's default) | `robot_groundcontact.xml` | 11 — a curated floor-contact set |
| `scene_apartment.xml` (`apartment`) | `robot_allcollisions.xml` | 70 — every part |
| `scene_allcollisions.xml` (base of `lab`, `garden`, `arena`) | `robot_allcollisions.xml` | 70 |

Visual meshes are group 2 (no contacts), collision meshes group 3. The dashboard shows group 3 as
an orange wire cage with the *collision* overlay.

## Timing

The scene files say `timestep 0.002`; the body server overrides it to **0.005 s** (the value the
BAM fit, contact `solref` and armature were tuned at, per `body_server.py`) and steps four of them
per 20 ms batch, paced to wall-clock real time. robotd ticks at 50 Hz on its own clock. Hence
"real time matters" (`docs/robot/simulation.md`): below ~0.9× real time the policies see a slower
world than they were trained on.

## Keyframes (from the scene files)

`SIT` (default start, trunk 0.07 m, stable while waiting; the standing behaviour rises from it),
`STAND` (trunk 0.12 m), `HOME` (body server placement: home pose, trunk 0.125 m), `FOLD`, `INIT`.
A duck nobody has enabled is held in place every step (`Body.restore`), because no static pose of
this biped is stable without a policy balancing it.

## What the viewer draws

The dashboard downloads the compiled model from the body (`op: model`): every geom's type, size,
local pose, group, material and texture, and every mesh's **compiled** vertices and faces (MuJoCo
recentres meshes, so the STL files are not in the geom's frame). Each frame it places the moving
bodies from MuJoCo's `xpos`/`xquat`. There is no hand-made duck anywhere in the frontend. The
hangar's paint shop recolours these meshes by name; it changes nothing in the model ([`HANGAR.md`](HANGAR.md)).

## A caveat about actuators (read from the code, not tested)

Training uses the BAM voltage-controlled XL330 model (`actuator/friction_dr_bam.py`); the body
server steps the MJCF's position actuators above. `infer_policy.py` offers both (`--no-bam` = the
XML PD), which suggests the two are known to differ. This is a sim-to-sim gap of the original
stack, not something Microduck X adds or fixes.
