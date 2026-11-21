// 3D view of the robot: loads xiaozhi_robot.glb, shows the real head angles on the solid
// model and the target angles on a translucent "ghost", and lets the user drag the head.
//
// Model conventions (web_assets/xiaozhi_robot.glb, the assembled CAD model): units are
// mm; node "robot" turns design Z-up into three.js Y-up; pan_pivot.rotation.z = +pan
// (robot turns to its own left), tilt_pivot.rotation.x = -tilt (head looks up). A model
// may override the signs with glTF extras {"sign": 1|-1} on the pivots (the simple model
// from scripts/build_robot_glb.py does). Which way the face looks is measured from the
// "bezel" (screen frame) node, so views and the floor arrow follow the model.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const DEG = Math.PI / 180;
const DEFAULT_PAN_SIGN = 1;
const DEFAULT_TILT_SIGN = -1;
const DRAG_DEG_PER_PX = 0.4;
// Colors by node name (the CAD export has no materials)
const PART_COLORS = [
  [/^(base|base_top)$/, 0x2a2e35, 0.7],
  [/gear/, 0x3b82f6, 0.5],
  [/turntable|rotor/, 0xf97316, 0.5],
  [/bezel/, 0x111318, 0.35],
  [/webcam/, 0x1c1f24, 0.4],
  [/cable/, 0x3a3f47, 0.6],
  [/bracket/, 0x9aa1ad, 0.5],
  [/arm/, 0xd9dce1, 0.5],
  [/head/, 0xf1f2f4, 0.45],
];

export class Robot3D {
  constructor(canvas, { onDrag, onDragEnd, onLoaded, onError } = {}) {
    this.canvas = canvas;
    this.onDrag = onDrag;
    this.onDragEnd = onDragEnd;
    this.real = { pan: 0, tilt: 0 };   // Angles shown on the solid model (smoothed)
    this.realGoal = { pan: 0, tilt: 0 };
    this.target = { pan: 0, tilt: 0 };
    this.limits = { pan: 90, tilt: 45 };
    this.ready = false;
    this.panSign = DEFAULT_PAN_SIGN;
    this.tiltSign = DEFAULT_TILT_SIGN;
    this.forward = new THREE.Vector3(0, 0, -1);  // Where the face looks at pan 0
    this.center = new THREE.Vector3(0, 130, 0);   // Orbit target
    this.size = 330;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer = renderer;

    const scene = new THREE.Scene();
    this.scene = scene;
    const camera = new THREE.PerspectiveCamera(35, 1, 10, 10000);
    this.camera = camera;
    this.setView("front", false);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(-300, 700, -500);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    Object.assign(sun.shadow.camera, { left: -400, right: 400, top: 400, bottom: -400, near: 100, far: 2000 });
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.6);
    fill.position.set(500, 300, 400);
    scene.add(fill);

    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(700, 64),
      new THREE.ShadowMaterial({ opacity: 0.18 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    const grid = new THREE.PolarGridHelper(520, 8, 5, 64, 0x888888, 0x888888);
    grid.material.transparent = true;
    grid.material.opacity = 0.18;
    scene.add(grid);
    // Arrow on the floor: the robot's front
    const arrow = this.arrow = new THREE.Mesh(
      new THREE.ConeGeometry(16, 40, 3),
      new THREE.MeshBasicMaterial({ color: 0xf97316, transparent: true, opacity: 0.5 }),
    );
    arrow.rotation.x = -Math.PI / 2;
    arrow.position.set(0, 1, -150);
    scene.add(arrow);

    const controls = new OrbitControls(camera, canvas);
    controls.target.set(0, 130, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.minDistance = 350;
    controls.maxDistance = 2200;
    controls.maxPolarAngle = Math.PI * 0.495;
    controls.enablePan = false;
    controls.update();
    this.controls = controls;
    this._dirty = true;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.drag = null;
    this._bindPointer();

    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();

    new GLTFLoader().load(
      "/static/xiaozhi_robot.glb",
      (gltf) => { this._setupModel(gltf.scene); onLoaded && onLoaded(); },
      undefined,
      (err) => { console.error("GLB load failed", err); onError && onError(err); },
    );

    this._last = performance.now();
    renderer.setAnimationLoop(() => this._frame());
  }

  _setupModel(root) {
    root.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = true; o.receiveShadow = true;
      // CAD exports often carry no normals: lighting would render them black
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      const name = (o.name || o.parent?.name || "").toLowerCase();
      const hit = PART_COLORS.find(([re]) => re.test(name));
      if (hit && (!o.material || !o.material.map)) {
        o.material = new THREE.MeshStandardMaterial({ color: hit[1], roughness: hit[2], metalness: 0.05, flatShading: true });
      }
    });
    this.scene.add(root);
    this.robot = root.getObjectByName("robot");
    this.pan = root.getObjectByName("pan_pivot");
    this.tilt = root.getObjectByName("tilt_pivot");
    if (!this.pan || !this.tilt) throw new Error("GLB has no pan_pivot/tilt_pivot");
    if (this.pan.userData.sign === 1 || this.pan.userData.sign === -1) this.panSign = this.pan.userData.sign;
    if (this.tilt.userData.sign === 1 || this.tilt.userData.sign === -1) this.tiltSign = this.tilt.userData.sign;

    // Stand the model on the floor, wherever its origin is
    root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(root);
    root.position.y -= box.min.y;
    root.updateMatrixWorld(true);
    box.setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    this.size = Math.max(size.x, size.y, size.z);
    const pivot = new THREE.Vector3();
    this.pan.getWorldPosition(pivot);
    this.center.set(pivot.x, size.y * 0.55, pivot.z);

    // Facing: from the tilt axis toward the screen frame, flattened
    const bezel = root.getObjectByName("bezel");
    if (bezel) {
      const head = new THREE.Vector3();
      this.tilt.getWorldPosition(head);
      const face = new THREE.Box3().setFromObject(bezel).getCenter(new THREE.Vector3());
      const f = face.sub(head).setY(0);
      if (f.lengthSq() > 1e-6) this.forward.copy(f.normalize());
    }
    // Floor arrow in front of the base, pointing the way the robot looks
    this.arrow.position.copy(pivot).addScaledVector(this.forward, Math.max(size.x, size.z) * 0.5 + 45).setY(1);
    this.arrow.rotation.set(-Math.PI / 2, 0, Math.atan2(-this.forward.x, -this.forward.z));

    if (bezel) this._addScreen(bezel);

    // Ghost: a translucent copy of everything that moves, at the target angles
    const ghostMat = new THREE.MeshBasicMaterial({
      color: 0xf97316, transparent: true, opacity: 0.22, depthWrite: false,
    });
    const ghost = this.pan.clone(true);
    ghost.name = "ghost_pan";
    ghost.traverse((o) => { if (o.isMesh) { o.material = ghostMat; o.castShadow = false; o.receiveShadow = false; o.renderOrder = 2; } });
    this.pan.parent.add(ghost);
    this.ghostPan = ghost;
    this.ghostTilt = ghost.getObjectByName("tilt_pivot");
    // Hit targets for dragging: the real head (and the ghost head, so a drag can resume on it)
    this.headMeshes = [];
    this.tilt.traverse((o) => { if (o.isMesh) this.headMeshes.push(o); });
    this.ghostTilt.traverse((o) => { if (o.isMesh) this.headMeshes.push(o); });
    this.ready = true;
    this.controls.target.copy(this.center);
    this.setView("front", false);
    this._apply();
    this._dirty = true;
  }

  // The CAD model has a screen frame but no screen: put a dark panel with the robot's
  // two eyes just behind the frame, so the front of the head is obvious. The frame is
  // thin along its local Y; its front side is the one away from the tilt axis.
  _addScreen(bezel) {
    const g = bezel.geometry;
    if (!g.boundingBox) g.computeBoundingBox();
    const b = g.boundingBox.clone().applyMatrix4(bezel.matrix);
    const c = b.getCenter(new THREE.Vector3()), s = b.getSize(new THREE.Vector3());
    if (s.y > Math.min(s.x, s.z)) return;  // Not the expected shape: leave it alone
    const dir = c.y < 0 ? -1 : 1;           // Front faces this way along local Y
    const backY = c.y - dir * s.y / 2;
    const face = new THREE.Group();
    face.name = "screen_face";
    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(s.x * 0.97, s.z * 0.97),
      new THREE.MeshStandardMaterial({ color: 0x050608, roughness: 0.25, metalness: 0.1 }),
    );
    face.add(panel);
    const eyeMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
    const eyeShape = new THREE.Shape();
    const ew = s.x * 0.17, eh = s.z * 0.42, r = ew * 0.3;
    eyeShape.moveTo(-ew / 2 + r, -eh / 2);
    eyeShape.lineTo(ew / 2 - r, -eh / 2); eyeShape.quadraticCurveTo(ew / 2, -eh / 2, ew / 2, -eh / 2 + r);
    eyeShape.lineTo(ew / 2, eh / 2 - r); eyeShape.quadraticCurveTo(ew / 2, eh / 2, ew / 2 - r, eh / 2);
    eyeShape.lineTo(-ew / 2 + r, eh / 2); eyeShape.quadraticCurveTo(-ew / 2, eh / 2, -ew / 2, eh / 2 - r);
    eyeShape.lineTo(-ew / 2, -eh / 2 + r); eyeShape.quadraticCurveTo(-ew / 2, -eh / 2, -ew / 2 + r, -eh / 2);
    for (const x of [-s.x * 0.2, s.x * 0.2]) {
      const eye = new THREE.Mesh(new THREE.ShapeGeometry(eyeShape, 6), eyeMat);
      eye.position.set(x, s.z * 0.06, 0.6);
      face.add(eye);
    }
    // Plane normal is +Z; turn it to point along dir * Y (the frame's front)
    face.rotation.x = dir < 0 ? Math.PI / 2 : -Math.PI / 2;
    face.position.set(c.x, backY + dir * 0.4, c.z);
    bezel.parent.add(face);
  }

  // Head box and base top in world space (for checks)
  layout() {
    if (!this.ready) return null;
    const head = new THREE.Box3().setFromObject(this.tilt);
    const base = this.scene.getObjectByName("base");
    const baseBox = base ? new THREE.Box3().setFromObject(base) : null;
    return {
      headCenterY: head.getCenter(new THREE.Vector3()).y,
      headMinY: head.min.y,
      baseTopY: baseBox ? baseBox.max.y : null,
      floorY: new THREE.Box3().setFromObject(this.robot || this.pan).min.y,
      forward: [this.forward.x, this.forward.z],
    };
  }

  // Camera positions relative to the robot: f = forward, r = the robot's right, up
  _viewPosition(name) {
    const f = this.forward, up = new THREE.Vector3(0, 1, 0);
    const r = new THREE.Vector3().crossVectors(f, up).normalize();
    const d = this.size * 2.1;
    const [ff, rr, uu] = { front: [0.85, 0.5, 0.55], back: [-0.85, -0.4, 0.6], top: [0.25, 0, 1.5] }[name] || [0.85, 0.5, 0.55];
    return this.center.clone().addScaledVector(f, ff * d).addScaledVector(r, rr * d).addScaledVector(up, uu * d);
  }

  setView(name, animate = true) {
    const p = this._viewPosition(name);
    if (!animate || !this.controls) {
      this.camera.position.copy(p);
      this.camera.lookAt(this.center);
      this._dirty = true;
      return;
    }
    this._viewAnim = { from: this.camera.position.clone(), to: p, t: 0 };
    this._dirty = true;
  }

  setLimits(limits) {
    if (limits) this.limits = { pan: limits.pan ?? 90, tilt: limits.tilt ?? 45 };
  }

  setReal(pan, tilt) {
    if (pan !== this.realGoal.pan || tilt !== this.realGoal.tilt) this._dirty = true;
    this.realGoal = { pan, tilt };
  }

  setTarget(pan, tilt) {
    if (pan !== this.target.pan || tilt !== this.target.tilt) this._dirty = true;
    this.target = { pan, tilt };
  }

  resize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    // Keep the whole robot in view on tall phone screens
    this.camera.zoom = w / h < 1.1 ? Math.max(0.6, (w / h) / 1.25) : 1;
    this.camera.updateProjectionMatrix();
    this._dirty = true;
  }

  _apply() {
    if (!this.ready) return;
    this.pan.rotation.z = this.panSign * this.real.pan * DEG;
    this.tilt.rotation.x = this.tiltSign * this.real.tilt * DEG;
    this.ghostPan.rotation.z = this.panSign * this.target.pan * DEG;
    this.ghostTilt.rotation.x = this.tiltSign * this.target.tilt * DEG;
    const apart = Math.abs(this.target.pan - this.real.pan) + Math.abs(this.target.tilt - this.real.tilt);
    this.ghostPan.visible = apart > 0.8;
  }

  _frame() {
    const now = performance.now();
    const dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;
    // Ease toward the reported angles so 20 Hz updates look smooth
    const k = 1 - Math.exp(-dt * 18);
    const ep = this.realGoal.pan - this.real.pan, et = this.realGoal.tilt - this.real.tilt;
    if (Math.abs(ep) + Math.abs(et) > 0.02) {
      this.real.pan += ep * k;
      this.real.tilt += et * k;
      this._dirty = true;
    } else {
      this.real = { ...this.realGoal };
    }
    if (this._viewAnim) {
      const a = this._viewAnim;
      a.t = Math.min(1, a.t + dt * 2.5);
      const e = 0.5 - 0.5 * Math.cos(Math.PI * a.t);
      this.camera.position.lerpVectors(a.from, a.to, e);
      if (a.t >= 1) this._viewAnim = null;
      this._dirty = true;
    }
    if (this.controls.update()) this._dirty = true;  // True while the view moves (damping)
    // Draw only when something moved: idle pages cost no GPU/CPU (phones, the Pi's LAN)
    if (!this._dirty) return;
    this._dirty = false;
    this._apply();
    this.renderer.render(this.scene, this.camera);
  }

  // ----- Dragging the head -----

  _hitHead(ev) {
    if (!this.ready) return null;
    const r = this.canvas.getBoundingClientRect();
    this.pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this.headMeshes, false);
    return hits.length ? hits[0] : null;
  }

  _bindPointer() {
    const c = this.canvas;
    c.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 && ev.pointerType === "mouse") return;
      if (!this._hitHead(ev)) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      this.controls.enabled = false;
      c.setPointerCapture(ev.pointerId);
      c.classList.add("dragging");
      this.drag = {
        id: ev.pointerId, x: ev.clientX, y: ev.clientY,
        pan: this.target.pan, tilt: this.target.tilt,
      };
    }, { capture: true });
    c.addEventListener("pointermove", (ev) => {
      if (this.drag && ev.pointerId === this.drag.id) {
        const d = this.drag;
        const dx = ev.clientX - d.x, dy = ev.clientY - d.y;
        // Same as the sliders and joystick: drag left = the robot turns to its own
        // left (pan +), drag up = it looks up (tilt +), from any viewing angle
        const pan = clamp(d.pan - dx * DRAG_DEG_PER_PX, this.limits.pan);
        const tilt = clamp(d.tilt - dy * DRAG_DEG_PER_PX, this.limits.tilt);
        this.target = { pan, tilt };
        this.onDrag && this.onDrag(pan, tilt);
        return;
      }
      if (ev.pointerType === "mouse") c.classList.toggle("over-head", !!this._hitHead(ev));
    });
    const end = (ev) => {
      if (!this.drag || ev.pointerId !== this.drag.id) return;
      this.drag = null;
      this.controls.enabled = true;
      c.classList.remove("dragging");
      this.onDragEnd && this.onDragEnd(this.target.pan, this.target.tilt);
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
  }

  get dragging() { return !!this.drag; }

  // Screen position of the head (for tests and hints)
  headScreenPoint() {
    if (!this.ready) return null;
    // Middle of the head (the pivot itself may sit on an arm, off the head shell)
    const p = new THREE.Box3().setFromObject(this.tilt).getCenter(new THREE.Vector3());
    p.project(this.camera);
    const r = this.canvas.getBoundingClientRect();
    return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
  }
}

function clamp(v, lim) { return Math.max(-lim, Math.min(lim, v)); }
