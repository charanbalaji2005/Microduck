// Wire types. These mirror microduck-x/sim/xbody.py (model + frames), robotd's robot.state
// (duck-ipc-proto RobotState), and the control service's own status/execution records.

export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number]; // MuJoCo order: w, x, y, z

export interface Marker { name: string; x: number; y: number; kind: string }

export interface EnvDescription {
  name: string; title: string; description: string; original: boolean; base_scene?: string;
  bounds: [number, number, number, number];
  look: { sky?: string; ground?: string; sun?: Vec3; fog?: [number, number]; indoor?: boolean };
  layouts: string[]; layout: string; markers: Marker[];
}

export interface ModelBody { id: number; name: string; parent: number; duck: number | null; dynamic: boolean; pos: Vec3; quat: Quat }
export interface ModelGeom {
  id: number; name: string; body: number; type: number; size: Vec3; pos: Vec3; quat: Quat;
  rgba: [number, number, number, number]; group: number; mesh: number; material: number; collides: boolean;
}
export interface ModelMesh { id: number; name: string; vert_offset: number; vert_count: number; face_offset: number; face_count: number }
export interface ModelMaterial { id: number; name: string; rgba: [number, number, number, number]; texture: number; texrepeat: [number, number]; texuniform: boolean; reflectance: number; specular: number; shininess: number }
export interface ModelTexture { id: number; name: string; width: number; height: number; offset: number; length: number }
export interface ModelJoint { name: string; body: number; type: number; pos: Vec3; axis: Vec3; range: [number, number]; limited: boolean }
export interface ModelCamera { name: string; body: number; pos: Vec3; quat: Quat; fovy: number }
export interface ModelDuck { index: number; name: string; prefix: string; trunk_body: number; feet: Record<string, number> }

export interface ModelHeader {
  protocol: string; mock?: boolean; env: EnvDescription; timestep: number; joint_names: string[];
  dynamic_bodies: number[]; bodies: ModelBody[]; geoms: ModelGeom[]; meshes: ModelMesh[];
  materials: ModelMaterial[]; textures: ModelTexture[]; joints: ModelJoint[]; cameras: ModelCamera[];
  ducks: ModelDuck[]; mesh_bytes: number; texture_bytes: number; source: Record<string, unknown>;
}

export interface SimDuck {
  trunk: Vec3; quat: Quat; gravity: Vec3; yaw: number; linvel: Vec3; angvel: Vec3;
  q: (number | null)[]; qd: (number | null)[]; ctrl: (number | null)[]; force: (number | null)[];
  torque_on: boolean; released: boolean; kp: number; feet: { left: boolean; right: boolean }; external_contacts: number;
}

export interface SimFrame {
  seq: number; t: number; wall: number; paused: boolean; rtf: number; mock?: boolean;
  xpos: number[]; xquat: number[]; ducks: SimDuck[];
  contacts: [number, number, number, number, number, number][]; // x y z geom1 geom2 normal-force
}

export interface RobotState {
  t: number; move: { requested: Vec3; applied: Vec3; limited_by?: string[] }; head: number[]; policy: string;
  safety: { fallen: boolean; limp: boolean; gravity: Vec3; gain?: number; picked_up: boolean };
  loop: { hz: number; missed: number }; joints: number[]; targets: number[]; velocities?: number[]; currents_ma?: number[];
  odom: { position: Vec3; yaw: number }; imu?: { gyro: Vec3; quat: Quat }; mock?: boolean;
}

export interface Status {
  version: string; uptime_s: number; mock: boolean;
  robotd: { connected: boolean; socket: string; socket_present: boolean; error: string | null; age_s: number | null; rate_hz: number;
    policy: string | null; loop_hz: number | null; walk: string | null; stand: string | null; skills: string[]; policy_unavailable: string | null };
  sim: { connected: boolean; address: string; error: string | null; env: string | null; opts: Record<string, unknown> | null;
    age_s: number | null; rate_hz: number; rtf: number | null; paused: boolean | null; sim_time: number | null; managed: boolean };
  safety: { motion_allowed: boolean; blockers: string[]; estop: boolean; estop_reason: string | null; fallen: boolean;
    fallen_reason: string | null; robot_age_s: number | null; sim_age_s: number | null; falls: number };
  activity: Execution | null;
  speech: { engine: string | null; reason: string | null; preference: string; ffmpeg: boolean;
    whisper: { installed: boolean; model: string | null; cached: boolean; size_mb: number | null; download: Record<string, unknown> };
    pocketsphinx: { installed: boolean; grammar: boolean } };
  tts: { espeak: boolean };
  llm: { enabled: boolean; model: string | null };
  brain?: { enabled: boolean; provider: string; model: string | null; reasoning_effort: string; problem: string | null; retrieval: string | null };
  config: { local_only: boolean; auth: boolean; max_command_s: number; limits: { vx: number; vy: number; vyaw: number };
    stt_min_confidence: number; require_wake_word: boolean };
}

export interface Execution {
  id: string; action: string; command: Record<string, unknown>; source: string; interface: string;
  transcript: string | null; confidence: number | null; status: string; message: string;
  outcome: Record<string, unknown>; created: number; started: number | null; ended: number | null;
}

export interface BusEvent { id: number; t: number; kind: string; level: string; message: string; data: Record<string, any>; transient?: boolean }

export interface ExperimentDef {
  name: string; title: string; description: string; environment: string; layout: string | null; keyframe: string;
  settle_s: number; steps: Record<string, unknown>[]; success: Record<string, unknown>; trials: number;
}

export interface RunSummary { id: string; experiment: string; title: string; started: number; status: string; passed_trials: number; trials: number; saved: boolean; environment: string }

export type CameraMode = "orbit" | "follow" | "first_person" | "top";
