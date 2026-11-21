// 3D view of the robot: loads xiaozhi_robot.glb, shows the real head angles on the solid
// model and the target angles on a translucent "ghost", and lets the user drag the head.
//
// Model conventions (scripts/build_robot_glb.py): units are mm; the robot looks toward
// -Z; pan_pivot.rotation.z = +pan (robot turns to its own left), tilt_pivot.rotation.x =
// +tilt (head looks up).
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const DEG = Math.PI / 180;
const PAN_SIGN = 1;
const TILT_SIGN = 1;
const DRAG_DEG_PER_PX = 0.4;

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
    const arrow = new THREE.Mesh(
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
    root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    this.scene.add(root);
    this.robot = root.getObjectByName("robot");
    this.pan = root.getObjectByName("pan_pivot");
    this.tilt = root.getObjectByName("tilt_pivot");
    if (!this.pan || !this.tilt) throw new Error("GLB has no pan_pivot/tilt_pivot");

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
    this._apply();
  }

  setView(name, animate = true) {
    const views = {
      front: [400, 330, -680],
      back: [-330, 370, 700],
      top: [0, 1000, -200],
    };
    const p = views[name] || views.front;
    if (!animate || !this.controls) {
      this.camera.position.set(...p);
      this.camera.lookAt(0, 130, 0);
      return;
    }
    this._viewAnim = { from: this.camera.position.clone(), to: new THREE.Vector3(...p), t: 0 };
  }

  setLimits(limits) {
    if (limits) this.limits = { pan: limits.pan ?? 90, tilt: limits.tilt ?? 45 };
  }

  setReal(pan, tilt) { this.realGoal = { pan, tilt }; }

  setTarget(pan, tilt) { this.target = { pan, tilt }; }

  resize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    // Keep the whole robot in view on tall phone screens
    this.camera.zoom = w / h < 1.1 ? Math.max(0.6, (w / h) / 1.25) : 1;
    this.camera.updateProjectionMatrix();
  }

  _apply() {
    if (!this.ready) return;
    this.pan.rotation.z = PAN_SIGN * this.real.pan * DEG;
    this.tilt.rotation.x = TILT_SIGN * this.real.tilt * DEG;
    this.ghostPan.rotation.z = PAN_SIGN * this.target.pan * DEG;
    this.ghostTilt.rotation.x = TILT_SIGN * this.target.tilt * DEG;
    const apart = Math.abs(this.target.pan - this.real.pan) + Math.abs(this.target.tilt - this.real.tilt);
    this.ghostPan.visible = apart > 0.8;
  }

  _frame() {
    const now = performance.now();
    const dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;
    // Ease toward the reported angles so 20 Hz updates look smooth
    const k = 1 - Math.exp(-dt * 18);
    this.real.pan += (this.realGoal.pan - this.real.pan) * k;
    this.real.tilt += (this.realGoal.tilt - this.real.tilt) * k;
    if (this._viewAnim) {
      const a = this._viewAnim;
      a.t = Math.min(1, a.t + dt * 2.5);
      const e = 0.5 - 0.5 * Math.cos(Math.PI * a.t);
      this.camera.position.lerpVectors(a.from, a.to, e);
      if (a.t >= 1) this._viewAnim = null;
    }
    this._apply();
    this.controls.update();
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

  // Signs that make the head follow the pointer from wherever the camera is
  _dragSigns() {
    const pan = this.target.pan * DEG;
    // Robot's left in world space at the current pan (left is -X at pan 0)
    const left = new THREE.Vector3(-Math.cos(pan), 0, Math.sin(pan));
    const forward = new THREE.Vector3(-Math.sin(pan), 0, -Math.cos(pan));
    const screenRight = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    screenRight.y = 0;
    const toCam = this.camera.position.clone().sub(new THREE.Vector3(0, 0, 0));
    toCam.y = 0;
    return {
      pan: screenRight.dot(left) >= 0 ? 1 : -1,
      tilt: toCam.dot(forward) >= 0 ? 1 : -1,
    };
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
        pan: this.target.pan, tilt: this.target.tilt, signs: this._dragSigns(),
      };
    }, { capture: true });
    c.addEventListener("pointermove", (ev) => {
      if (this.drag && ev.pointerId === this.drag.id) {
        const d = this.drag;
        const dx = ev.clientX - d.x, dy = ev.clientY - d.y;
        const pan = clamp(d.pan + d.signs.pan * dx * DRAG_DEG_PER_PX, this.limits.pan);
        const tilt = clamp(d.tilt - d.signs.tilt * dy * DRAG_DEG_PER_PX, this.limits.tilt);
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
    const p = new THREE.Vector3();
    this.tilt.getWorldPosition(p);
    p.project(this.camera);
    const r = this.canvas.getBoundingClientRect();
    return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
  }
}

function clamp(v, lim) { return Math.max(-lim, Math.min(lim, v)); }
