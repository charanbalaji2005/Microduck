const http = require("http");
const path = require("path");

function get(p) {
  return new Promise((resolve, reject) => {
    http.get("http://127.0.0.1:8765" + p, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    }).on("error", reject);
  });
}

async function run() {
  const [headerBuf, meshBuf, texBuf] = await Promise.all([
    get("/api/model"),
    get("/api/model/meshes.bin"),
    get("/api/model/textures.bin")
  ]);
  const h = JSON.parse(headerBuf.toString());
  const THREE = require(path.join(__dirname, "microduck-x/frontend/node_modules/three"));

  // Emulate buildScene directly in node
  console.log("Bodies:", h.bodies.length);
  console.log("Geoms:", h.geoms.length);
  console.log("Meshes:", h.meshes.length);
  console.log("Textures:", h.textures.length);
  console.log("Materials:", h.materials.length);

  const PLANE = 0, SPHERE = 2, CAPSULE = 3, ELLIPSOID = 4, CYLINDER = 5, BOX = 6, MESH = 7;
  const root = new THREE.Group();
  const bodies = new Map();
  for (const b of h.bodies) {
    const g = new THREE.Group();
    g.name = b.name;
    g.position.set(b.pos[0], b.pos[1], b.pos[2]);
    bodies.set(b.id, g);
    root.add(g);
  }

  const meshGeo = new Map();
  const smoothGeo = new Map();
  const geoFor = (id, smooth = false) => {
    if (smooth) {
      let sg = smoothGeo.get(id);
      if (sg) return sg;
      const m = h.meshes.find((x) => x.id === id);
      if (!m) throw new Error("Mesh not found: " + id);
      const indexed = new THREE.BufferGeometry();
      const vArr = new Float32Array(meshBuf.buffer, meshBuf.byteOffset + m.vert_offset, m.vert_count * 3);
      const fArr = new Uint32Array(meshBuf.buffer, meshBuf.byteOffset + m.face_offset, m.face_count * 3);
      indexed.setAttribute("position", new THREE.BufferAttribute(vArr.slice(), 3));
      indexed.setIndex(new THREE.BufferAttribute(fArr.slice(), 1));
      indexed.computeVertexNormals();
      sg = indexed;
      sg.computeBoundingSphere();
      smoothGeo.set(id, sg);
      return sg;
    }
    let geo = meshGeo.get(id);
    if (geo) return geo;
    const m = h.meshes.find((x) => x.id === id);
    if (!m) throw new Error("Mesh not found: " + id);
    const verts = new Float32Array(meshBuf.buffer, meshBuf.byteOffset + m.vert_offset, m.vert_count * 3);
    const faces = new Uint32Array(meshBuf.buffer, meshBuf.byteOffset + m.face_offset, m.face_count * 3);
    const indexed = new THREE.BufferGeometry();
    indexed.setAttribute("position", new THREE.BufferAttribute(verts.slice(), 3));
    indexed.setIndex(new THREE.BufferAttribute(faces.slice(), 1));
    geo = indexed.toNonIndexed();
    geo.computeVertexNormals();
    geo.computeBoundingSphere();
    meshGeo.set(id, geo);
    return geo;
  };

  let countGeom = 0;
  for (const g of h.geoms) {
    if (g.group > 3 || g.rgba[3] === 0) continue;
    let geo = null;
    switch (g.type) {
      case PLANE: geo = new THREE.PlaneGeometry(10, 10); break;
      case SPHERE: geo = new THREE.SphereGeometry(g.size[0]); break;
      case BOX: geo = new THREE.BoxGeometry(2*g.size[0], 2*g.size[1], 2*g.size[2]); break;
      case CYLINDER: geo = new THREE.CylinderGeometry(g.size[0], g.size[0], 2*g.size[1]); break;
      case CAPSULE: geo = new THREE.CapsuleGeometry(g.size[0], 2*g.size[1]); break;
      case MESH: if (g.mesh >= 0) geo = geoFor(g.mesh, g.group !== 3 && h.bodies[g.body].duck !== null); break;
    }
    if (geo) {
      countGeom++;
      const mat = new THREE.MeshBasicMaterial();
      const mesh = new THREE.Mesh(geo, mat);
      bodies.get(g.body).add(mesh);
    }
  }
  console.log("Successfully built geoms:", countGeom, "Total groups in root:", root.children.length);
}

run().catch(console.error);