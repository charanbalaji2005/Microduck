import * as THREE from "three";

// The Microduck's paint job, by part. Parts are found by the *mesh names of the original
// Onshape export* (microduck_rl/robot/microduck/assets), so recolouring changes the real
// model's materials — it never swaps in a different duck.

export type PartName = "shell" | "face" | "trim" | "beak" | "eye" | "feet";
export type DuckLook = Record<PartName, string>;

export const DEFAULT_LOOK: DuckLook = {
  shell: "#f2efe9", face: "#a7a69f", trim: "#ff6a1a", beak: "#ffa41b", eye: "#ffb11c", feet: "#ff6a1a",
};

export const PART_LABELS: Record<PartName, string> = {
  shell: "Shell", face: "Face", trim: "Trim", beak: "Beak", eye: "Eye", feet: "Feet",
};

type Finish = "shell" | "matte" | "plastic" | "lens" | "frame" | "metal" | "pcb";
interface Rule { part: PartName | null; finish: Finish; color?: string }

const RULES: [RegExp, Rule][] = [
  [/^(top_head_shell|left_shell|right_shell|upper_leg_left|upper_leg_right)$/, { part: "shell", finish: "shell" }],
  [/^face_part$/, { part: "face", finish: "matte" }],
  [/^(bottom_head_shell|ankle_left|ankle_right|ankle_l_v1|ankle_r_v1)$/, { part: "trim", finish: "plastic" }],
  [/^(jaw|jaw_soft|soft_mouth_top)$/, { part: "beak", finish: "plastic" }],
  [/^noenoeil$/, { part: "eye", finish: "plastic" }],
  [/^(sole_left|sole_right)$/, { part: "eye", finish: "plastic" }],
  [/^(foot_left|foot_right)$/, { part: "feet", finish: "plastic" }],
  [/^(lens|m12_lens_holder)$/, { part: null, finish: "lens", color: "#0b0c0e" }],
  [/^seeed_bearing/, { part: null, finish: "metal", color: "#c9ced6" }],
  [/(pcb|raspberry|speaker|np_f970)/, { part: null, finish: "pcb", color: "#2c3a33" }],
  [/^xl330$/, { part: null, finish: "frame", color: "#1c1e22" }],
  [/.*/, { part: null, finish: "frame", color: "#24262b" }],
];

export function ruleFor(meshName: string): Rule {
  return RULES.find(([re]) => re.test(meshName))![1];
}

export function robotMaterial(rule: Rule, look: DuckLook): THREE.MeshPhysicalMaterial {
  const color = new THREE.Color(rule.part ? look[rule.part] : rule.color!);
  const m = new THREE.MeshPhysicalMaterial({ color });
  switch (rule.finish) {
    case "shell": Object.assign(m, { roughness: 0.34, clearcoat: 0.6, clearcoatRoughness: 0.28, metalness: 0 }); break;
    case "matte": Object.assign(m, { roughness: 0.62, clearcoat: 0.1, metalness: 0 }); break;
    case "plastic": Object.assign(m, { roughness: 0.38, clearcoat: 0.4, clearcoatRoughness: 0.3, metalness: 0 }); break;
    case "lens": Object.assign(m, { roughness: 0.05, clearcoat: 1, clearcoatRoughness: 0.02, metalness: 0.3 }); break;
    case "metal": Object.assign(m, { roughness: 0.28, metalness: 1 }); break;
    case "pcb": Object.assign(m, { roughness: 0.5, metalness: 0.1 }); break;
    default: Object.assign(m, { roughness: 0.52, clearcoat: 0.15, clearcoatRoughness: 0.5, metalness: 0.05 });
  }
  m.userData.part = rule.part;
  return m;
}

const KEY = "mdx-duck-look";
export function loadLook(): DuckLook {
  try {
    const hash = new URLSearchParams(location.hash.slice(1)).get("duck");
    if (hash) return { ...DEFAULT_LOOK, ...JSON.parse(atob(hash)).look };
  } catch { /* bad share link */ }
  try { return { ...DEFAULT_LOOK, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return { ...DEFAULT_LOOK }; }
}
export function saveLook(look: DuckLook) {
  try { localStorage.setItem(KEY, JSON.stringify(look)); } catch { /* storage blocked */ }
}
export function shareLink(look: DuckLook, name: string) {
  const code = btoa(JSON.stringify({ look, name }));
  return `${location.origin}${location.pathname}#duck=${code}`;
}
