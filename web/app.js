// app.js – toàn bộ logic của trang web.
//
// Luồng dữ liệu của trang:
//   1. Mở trang  -> GET /api/config   : lấy ngưỡng, nhãn, kích thước thùng (không viết cứng trong code)
//   2. WebSocket /ws mở được -> GET /api/history : vẽ sẵn 60 phút gần nhất lên biểu đồ
//   3. Sau đó mỗi tin từ /ws ({topic, data}) cập nhật: thẻ số đo, mô hình 3D, biểu đồ, nút điều khiển
//   4. Bấm nút -> POST /api/cmd/{thiết bị}; trạng thái chỉ đổi khi nhận pond1/state qua /ws
//
// Chạy trang qua server (http://localhost:8000), KHÔNG mở file index.html trực tiếp,
// vì trình duyệt chặn fetch/module khi mở bằng file://.

import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
// Chart là biến toàn cục do <script> Chart.js trong index.html tạo ra.

// =====================================================================
// 1. HẰNG SỐ CỦA GIAO DIỆN (không phải ngưỡng nghiệp vụ; ngưỡng đọc từ /api/config)
// =====================================================================
const HISTORY_MINUTES = 60;          // biểu đồ hiện 60 phút gần nhất
const RECONNECT_MS = 3000;           // mất WebSocket thì 3 giây sau thử nối lại
const PENDING_TIMEOUT_MS = 20000;    // chờ ESP32 báo lại tối đa 20 giây rồi báo "chưa phản hồi" (09/11: 10 → 20 s)
const NOTICE_SHOW_MS = 8000;         // thông báo lỗi tự ẩn sau 8 giây
const DO_UNIT = "mg/L";              // đơn vị DO (theo mô hình DO, xem docs/mo-ta-he-thong.md)
const WATER_FILL_DEFAULT = 0.8;      // chỉ để VẼ: nước đầy 80% độ sâu ao khi config chưa đặt water_level_fixed_m

// Tên hiển thị cho 3 mức cảnh báo. Luôn có biểu tượng + chữ, không chỉ dựa vào màu.
const LEVELS = {
  ok: { icon: "✔", text: "Bình thường" },
  warning: { icon: "▲", text: "Cảnh báo" },
  danger: { icon: "✖", text: "Nguy hiểm" },
  unknown: { icon: "–", text: "Chưa có dữ liệu" },
};

// Màu nước 3D theo mức cảnh báo
const WATER_COLORS = {
  ok: 0x3fa6a0,
  warning: 0xd1a93a,
  danger: 0xc0504d,
  unknown: 0x7fa6bd,
};

// =====================================================================
// 2. TRẠNG THÁI CHUNG CỦA TRANG
// =====================================================================
let config = null;               // kết quả /api/config
let wsConnected = false;
const live = { sensors: null, twin: null, state: null }; // tin mới nhất theo từng loại
let items = [];                  // danh sách thẻ/biểu đồ: các cảm biến trong config + DO
let charts = [];                 // [{item, chart}]
let model = null;                // mô hình 3D (null nếu trình duyệt không có WebGL)

// Lệnh đang chờ ESP32 xác nhận: device -> {want, timer} hoặc null
const pending = { aerator: null, pump: null };

// Giây còn tạm dừng tự động sau lệnh tay (từ /api/status, khoá auto_pause_s). 0 = đang tự động.
let autoPause = { aerator: 0, pump: 0 };
const PAUSE_POLL_MS = 5000;      // bao lâu hỏi lại /api/status một lần

const $ = (id) => document.getElementById(id);

// =====================================================================
// 3. HÀM TIỆN ÍCH
// =====================================================================

// Làm tròn 2 chữ số thập phân và bỏ số 0 thừa: 28.40 -> "28.4", 310 -> "310"
function fmt(v) {
  return String(Math.round(v * 100) / 100);
}

function fmtTime(ms, withSeconds) {
  const opts = { hour: "2-digit", minute: "2-digit", hour12: false };
  if (withSeconds) opts.second = "2-digit";
  return new Date(ms).toLocaleTimeString("vi-VN", opts);
}

// Hiện thông báo trên đầu trang (isError = true thì tô đỏ)
let noticeTimer = null;
function showNotice(text, isError) {
  const box = $("notice");
  box.textContent = text;
  box.className = isError ? "notice error" : "notice";
  box.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => (box.hidden = true), NOTICE_SHOW_MS);
}

// Số đo hợp lệ? Phải là số và nằm trong valid_range của config, ngược lại coi như không có.
// (Server cũng bỏ các giá trị ngoài khoảng này, nên web làm giống để hai bên khớp.)
function cleanValue(item, raw) {
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  if (item.validRange && (raw < item.validRange[0] || raw > item.validRange[1])) return null;
  return raw;
}

// Lấy một giá trị từ một dòng của /api/history. Số đo ngoài 3 cột riêng nằm trong row.extra.
function valueFromRow(item, row) {
  const raw = item.id === "do" ? row.do_est : row[item.id] ?? (row.extra || {})[item.id];
  return cleanValue(item, raw);
}

// =====================================================================
// 4. DANH SÁCH THẺ / BIỂU ĐỒ (lấy từ config, không viết cứng temp/ph/tds)
// =====================================================================
function buildItems(cfg) {
  const list = cfg.sensors
    .filter((s) => s.enabled !== false)
    .map((s) => ({
      id: s.id,
      label: s.label,
      unit: s.unit,
      validRange: s.valid_range,
      warnRange: s.warn_range,
    }));

  // Thêm DO (ước tính, không phải cảm biến nên không có trong mục sensors)
  list.push({
    id: "do",
    label: "Oxy hoà tan (DO) – ước tính",
    unit: DO_UNIT,
    validRange: null,
    warnRange: null,
  });
  return list;
}

// Mức cảnh báo của TỪNG thẻ (tính từ ngưỡng trong config, giống cách server tính)
function itemLevel(item, value) {
  if (value === null || value === undefined) return "unknown";
  if (item.id === "do") {
    // Dưới ngưỡng bật sục khí = nguy hiểm (server cũng tính "danger" theo ngưỡng này)
    return value < config.do_model.aerator_on_below ? "danger" : "ok";
  }
  const [low, high] = item.warnRange;
  return value >= low && value <= high ? "ok" : "warning";
}

// =====================================================================
// 5. THẺ SỐ ĐO
// =====================================================================
function buildCards() {
  const wrap = $("cards");
  wrap.innerHTML = "";

  for (const item of items) {
    const card = document.createElement("div");
    card.className = "card level-unknown";

    const label = document.createElement("div");
    label.className = "card-label";
    label.textContent = item.label;

    const value = document.createElement("div");
    value.className = "card-value";
    const num = document.createElement("span"); // chữ số, đổi mỗi lần có số đo mới
    num.textContent = "—";
    const unit = document.createElement("span");
    unit.className = "card-unit";
    unit.textContent = item.unit;
    value.append(num, unit);

    const status = document.createElement("div");
    status.className = "card-status";

    const hint = document.createElement("div");
    hint.className = "card-hint";
    if (item.id === "do") {
      const d = config.do_model;
      hint.textContent = `Bật sục khí khi < ${d.aerator_on_below}, tắt khi > ${d.aerator_off_above} ${item.unit}`;
    } else {
      hint.textContent = `Khoảng an toàn: ${item.warnRange[0]} – ${item.warnRange[1]} ${item.unit}`.trim();
    }

    const extra = document.createElement("div"); // dòng phụ (DO: độ bão hoà)
    extra.className = "card-hint";

    card.append(label, value, status, hint, extra);
    wrap.appendChild(card);
    Object.assign(item, { cardEl: card, numEl: num, statusEl: status, extraEl: extra });
  }
}

function updateCards() {
  for (const item of items) {
    let value = null;
    if (item.id === "do") {
      value = cleanValue(item, live.twin && live.twin.do);
    } else {
      value = cleanValue(item, live.sensors && live.sensors[item.id]);
    }

    const level = itemLevel(item, value);
    item.cardEl.className = `card level-${level}`;
    item.statusEl.textContent = `${LEVELS[level].icon} ${LEVELS[level].text}`;

    item.numEl.textContent = value === null ? "—" : fmt(value);

    if (item.id === "do" && live.twin && live.twin.do_sat !== undefined) {
      item.extraEl.textContent =
        `ƯỚC TÍNH từ mô hình · bão hoà: ${fmt(live.twin.do_sat)} ${item.unit}`;
    } else if (item.id === "do") {
      item.extraEl.textContent = "ƯỚC TÍNH từ mô hình, không phải cảm biến";
    }
  }
}

function updateOverallLevel() {
  const level = live.twin && LEVELS[live.twin.level] ? live.twin.level : "unknown";
  const badge = $("overall-level");
  badge.className = `badge level-${level}`;
  badge.textContent = `Mức cảnh báo: ${LEVELS[level].icon} ${LEVELS[level].text}`;

  if (live.twin && typeof live.twin.ts === "number") {
    $("last-update").textContent = `Cập nhật lúc ${fmtTime(live.twin.ts * 1000, true)}`;
  }
  if (model) model.setLevel(level);
}

// =====================================================================
// 6. MÔ HÌNH 3D (Three.js)
// =====================================================================
// =====================================================================
// CÔNG CỤ VẼ DÙNG CHUNG (chạy một lần khi dựng mô hình)
// =====================================================================

// Nhiễu giá trị (value noise) lặp liền mạch: trả về mảng size*size, mỗi số từ 0 tới 1.
// Dùng để vẽ "hạt" của đất, đá, mặt cắt... cho bề mặt không bị phẳng lì.
function makeValueNoise(size, octaves, seed) {
  let s = seed >>> 0;
  const rand = () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
  const out = new Float32Array(size * size);
  let amp = 1;
  let total = 0;
  for (let o = 0; o < octaves; o++) {
    const cells = 4 << o; // 4, 8, 16, 32 ô trên một cạnh
    const lattice = new Float32Array(cells * cells);
    for (let i = 0; i < lattice.length; i++) lattice[i] = rand();
    const step = cells / size;
    for (let y = 0; y < size; y++) {
      const fy = y * step;
      const y0 = Math.floor(fy);
      const ty = fy - y0;
      const sy = ty * ty * (3 - 2 * ty);
      const y1 = (y0 + 1) % cells;
      for (let x = 0; x < size; x++) {
        const fx = x * step;
        const x0 = Math.floor(fx);
        const tx = fx - x0;
        const sx = tx * tx * (3 - 2 * tx);
        const x1 = (x0 + 1) % cells;
        const a = lattice[y0 * cells + x0];
        const b = lattice[y0 * cells + x1];
        const c = lattice[y1 * cells + x0];
        const d = lattice[y1 * cells + x1];
        const top = a + (b - a) * sx;
        out[y * size + x] += amp * (top + (c + (d - c) * sx - top) * sy);
      }
    }
    total += amp;
    amp *= 0.5;
  }
  for (let i = 0; i < out.length; i++) out[i] /= total;
  return out;
}

// Ảnh xám "hạt" lặp được: map dùng nhân với màu gốc, bump dùng làm độ gồ ghề
function makeDetailTextures(size, seed, lo, hi) {
  const n = makeValueNoise(size, 5, seed);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(size, size);
  for (let i = 0; i < n.length; i++) {
    const v = Math.round((lo + (hi - lo) * n[i]) * 255);
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = v;
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  const bump = new THREE.CanvasTexture(canvas); // cùng ảnh nhưng là dữ liệu, không phải màu
  for (const t of [map, bump]) {
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.anisotropy = 4;
  }
  return { map, bump };
}

// Mặt cắt đất: lớp đất mặt sẫm ở trên, đất sét nâu, cát nhạt, rồi đất sâu; ranh giới lượn sóng, có hạt li ti
function makeStrataTexture(size, seed) {
  const warp = makeValueNoise(size, 3, seed);
  const grain = makeValueNoise(size, 6, seed + 7);
  const bands = [ // [điểm bắt đầu tính từ đáy (0..1), màu RGB]
    [0.0, [118, 98, 80]],
    [0.3, [170, 140, 100]],
    [0.55, [136, 100, 68]],
    [0.86, [66, 48, 34]],
    [1.0, [58, 42, 30]],
  ];
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    const v = 1 - y / size; // 1 = trên cùng
    for (let x = 0; x < size; x++) {
      const w = v + (warp[y * size + x] - 0.5) * 0.14;
      let c0 = bands[0];
      let c1 = bands[bands.length - 1];
      for (let b = 0; b < bands.length - 1; b++) {
        if (w >= bands[b][0] && w <= bands[b + 1][0]) { c0 = bands[b]; c1 = bands[b + 1]; break; }
      }
      const t = c1[0] === c0[0] ? 0 : Math.min(Math.max((w - c0[0]) / (c1[0] - c0[0]), 0), 1);
      const k = 0.75 + 0.5 * grain[y * size + x];
      for (let ch = 0; ch < 3; ch++) {
        img.data[(y * size + x) * 4 + ch] = Math.min(255, (c0[1][ch] + (c1[1][ch] - c0[1][ch]) * t) * k);
      }
      img.data[(y * size + x) * 4 + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  // vài viên sỏi nhỏ lẫn trong đất
  for (let i = 0; i < 70; i++) {
    const px = Math.random() * size;
    const py = Math.random() * size * 0.85 + size * 0.12;
    const r = 2 + Math.random() * 5;
    const g = 120 + Math.random() * 70;
    ctx.fillStyle = `rgba(${g},${g * 0.92},${g * 0.8},0.85)`;
    ctx.beginPath();
    ctx.ellipse(px, py, r, r * 0.65, Math.random() * 3, 0, Math.PI * 2);
    ctx.fill();
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 4;
  return tex;
}

function makeSkyTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 4;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0, "#7fb0de");
  g.addColorStop(0.55, "#bcd8ef");
  g.addColorStop(1, "#eef5fa");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 4, 256);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Bọt khí: vòng sáng ở rìa, trong suốt ở giữa, có một đốm phản chiếu
function makeBubbleTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(32, 32, 8, 32, 32, 30);
  g.addColorStop(0, "rgba(255,255,255,0.10)");
  g.addColorStop(0.75, "rgba(255,255,255,0.30)");
  g.addColorStop(1, "rgba(255,255,255,0.95)");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(32, 32, 30, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.beginPath();
  ctx.arc(23, 22, 5, 0, Math.PI * 2);
  ctx.fill();
  return new THREE.CanvasTexture(canvas);
}

function makeDropTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 32;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(16, 16, 2, 16, 16, 15);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(canvas);
}

// Giá trị dùng chung cho mọi vật liệu "dưới nước" và cây chuyển động
const UW = {
  uTime: { value: 0 },
  uSurfaceY: { value: 0.2 },
  uWaterColor: { value: new THREE.Color(0x3fa6a0) },
  uDensity: { value: 6.0 }, // nước càng đục thì số này càng lớn
};

// Đoạn mã GLSL tính vân sáng caustics (ánh sáng gợn sóng dưới đáy nước), dạng lưới sáng lung linh
const CAUSTIC_GLSL = `
float uwCaustic(vec2 uv, float time) {
  vec2 p = mod(uv * 6.28318530718, 6.28318530718) - 250.0;
  vec2 i = p;
  float c = 1.0;
  float inten = 0.005;
  for (int n = 0; n < 5; n++) {
    float t = time * (1.0 - (3.5 / float(n + 1)));
    i = p + vec2(cos(t - i.x) + sin(t + i.y), sin(t - i.y) + cos(t + i.x));
    c += 1.0 / length(vec2(p.x / (sin(i.x + t) / inten), p.y / (cos(i.y + t) / inten)));
  }
  c /= 5.0;
  c = 1.17 - pow(c, 1.4);
  return pow(abs(c), 8.0);
}
`;

// "Vá" vật liệu chuẩn của Three.js để thêm các hiệu ứng:
//   underwater: phần nằm dưới mặt nước bị nhuộm màu nước, càng sâu càng đậm
//   caustics:   thêm vân sáng gợn sóng ở phần dưới nước
//   sway:       lá cỏ / lá cây đung đưa (dùng cho InstancedMesh)
function patchMaterial(mat, opt) {
  const key = `uw${opt.underwater ? 1 : 0}c${opt.caustics ? 1 : 0}s${opt.sway ? 1 : 0}`;
  mat.customProgramCacheKey = () => key;
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = UW.uTime;
    if (opt.underwater) {
      shader.uniforms.uSurfaceY = UW.uSurfaceY;
      shader.uniforms.uWaterColor = UW.uWaterColor;
      shader.uniforms.uDensity = UW.uDensity;
    }

    let vDecl = "uniform float uTime;\n";
    let vBegin = "";
    if (opt.sway) {
      vBegin += `
        float swayPh = instanceMatrix[3][0] * 37.0 + instanceMatrix[3][2] * 51.0;
        transformed.x += sin(uTime * 1.4 + swayPh + transformed.y * 3.0) * 0.16 * transformed.y * transformed.y;
      `;
    }
    if (opt.underwater) vDecl += "varying vec3 vUwPos;\n";

    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\n" + vDecl)
      .replace("#include <begin_vertex>", "#include <begin_vertex>\n" + vBegin);
    if (opt.underwater) {
      shader.vertexShader = shader.vertexShader.replace(
        "#include <project_vertex>",
        `#include <project_vertex>
        vec4 uwP = vec4(transformed, 1.0);
        #ifdef USE_INSTANCING
          uwP = instanceMatrix * uwP;
        #endif
        vUwPos = (modelMatrix * uwP).xyz;`
      );
      shader.fragmentShader = shader.fragmentShader
        .replace(
          "#include <common>",
          `#include <common>
          uniform float uTime, uSurfaceY, uDensity;
          uniform vec3 uWaterColor;
          varying vec3 vUwPos;
          ${opt.caustics ? CAUSTIC_GLSL : ""}`
        )
        .replace(
          "#include <tonemapping_fragment>",
          `{
            float uwDepth = uSurfaceY - vUwPos.y;
            float uwUnder = smoothstep(-0.002, 0.004, uwDepth);
            float uwD = max(uwDepth, 0.0);
            ${opt.caustics ? `
            vec3 uwN = abs(inverseTransformDirection(normal, viewMatrix));
            vec3 uwW = pow(uwN, vec3(4.0));
            uwW /= (uwW.x + uwW.y + uwW.z + 1e-4);
            float uwC = uwW.y * uwCaustic(vUwPos.xz * 3.5, uTime * 0.5)
                      + uwW.x * uwCaustic(vUwPos.zy * 3.5, uTime * 0.5)
                      + uwW.z * uwCaustic(vUwPos.xy * 3.5, uTime * 0.5);
            gl_FragColor.rgb += vec3(0.75, 0.95, 1.0) * uwC * 0.38 * uwUnder * exp(-uwD * 5.0);` : ""}
            float uwK = (1.0 - exp(-uwD * uDensity)) * uwUnder;
            gl_FragColor.rgb = mix(gl_FragColor.rgb, uWaterColor * 0.55, uwK * 0.8);
          }
          #include <tonemapping_fragment>`
        );
    }
  };
}

// Một lá cỏ / lá cây: dải dài thon dần về ngọn, hơi cong, màu từ gốc sẫm lên ngọn sáng.
// Cao 1 đơn vị, khi dùng thì phóng đại đều theo chiều cao mong muốn.
function makeBladeGeometry(width, baseHex, tipHex) {
  const g = new THREE.PlaneGeometry(width, 1, 1, 6);
  g.translate(0, 0.5, 0);
  const p = g.attributes.position;
  const col = new Float32Array(p.count * 3);
  const base = new THREE.Color(baseHex);
  const tip = new THREE.Color(tipHex);
  const c = new THREE.Color();
  for (let i = 0; i < p.count; i++) {
    const y = p.getY(i);
    p.setX(i, p.getX(i) * (1 - 0.85 * y));
    p.setZ(i, 0.12 * y * y);
    c.copy(base).lerp(tip, y);
    col.set([c.r, c.g, c.b], i * 3);
  }
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  g.computeVertexNormals();
  return g;
}

// Tạo nhiều lá cùng lúc bằng InstancedMesh (một lần vẽ cho cả trăm lá)
function makeBlades(scene, geo, mat, count, hMin, hMax, place, colors) {
  const mesh = new THREE.InstancedMesh(geo, mat, count);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  for (let i = 0; i < count; i++) {
    const pos = place();
    dummy.position.set(pos.x, pos.y, pos.z);
    dummy.rotation.set((Math.random() - 0.5) * 0.25, Math.random() * Math.PI, (Math.random() - 0.5) * 0.25);
    dummy.scale.setScalar(hMin + Math.random() * (hMax - hMin));
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    color.set(colors[Math.floor(Math.random() * colors.length)]);
    mesh.setColorAt(i, color);
  }
  mesh.frustumCulled = false;
  mesh.castShadow = true;
  scene.add(mesh);
  return mesh;
}

// =====================================================================
// MÔ HÌNH AO
// =====================================================================
// createPondModel() là chỗ DUY NHẤT biết cách vẽ ao: đất, bờ cỏ, nước, cây, máy sục, bơm.
// Sau này muốn dùng file .glb làm bằng Blender: viết lại hàm này bằng GLTFLoader nhưng giữ nguyên
// các hàm trả về (setWaterLevel, setLevel, setAerator, setPump) + update,
// phần còn lại của trang không phải sửa.
// Đơn vị trong cảnh là MÉT, đúng như pond_geometry_m.
function createPondModel(container, geometry) {
  // Mặt cắt ngang một ao đất: đáy ao ở y = 0, mặt bờ ở y = G. Thành phía trước (gần người xem)
  // được cắt bỏ nên nhìn thẳng vào bên trong: mái dốc, đáy, nước, bọt khí.
  const L = geometry.length;   // dài miệng ao (trục x, chiều ngang màn hình)
  const W = geometry.width;    // rộng miệng ao (trục z, chiều sâu vào trong màn hình)
  const H = geometry.height;   // độ sâu ao (từ bờ xuống đáy)
  const G = H;                 // độ cao mặt bờ so với đáy ao
  const run = Math.min(H * 0.4, (Math.min(L, W) / 2) * 0.45); // mái dốc đi ngang bao nhiêu thì xuống hết độ sâu
  const B = Math.min(L, W) * 0.2;        // bề rộng dải bờ thấy được hai bên ao
  const Lg = L + 2 * B;                  // bề ngang cả mặt cắt
  const Wg = W + 2 * B;
  const soilThick = H * 0.25;            // lớp đất dưới đáy ao (thấy được ở mặt cắt)
  const zc = W / 2 - run;                // vị trí mặt phẳng cắt: ngay chân mái dốc phía trước
  const aBot = L / 2 - run;              // nửa chiều dài đáy ao (chỗ phẳng)
  const bBot = W / 2 - run;              // nửa chiều rộng đáy ao

  // --- Bộ vẽ: tông màu điện ảnh, có bóng đổ mềm ---
  const scene = new THREE.Scene();
  scene.background = makeSkyTexture();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 50);

  const renderer = new THREE.WebGLRenderer({ antialias: true }); // lỗi ở đây nếu máy không có WebGL
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.appendChild(renderer.domElement);

  // Ánh sáng: trời (xanh nhạt từ trên, nâu ấm từ dưới) + nắng chính có bóng + đèn bổ sung phía trước cho đỡ tối
  scene.add(new THREE.HemisphereLight(0xcfe6ff, 0x6b5a45, 0.75));
  const sun = new THREE.DirectionalLight(0xfff0dc, 2.6);
  sun.position.set(0.55, 1.4, 1.0);
  sun.target.position.set(0, G * 0.3, 0);
  scene.add(sun, sun.target);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -Lg * 0.8, right: Lg * 0.8, top: 0.7, bottom: -0.7, near: 0.1, far: 4 });
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.01;
  const fill = new THREE.DirectionalLight(0xdbe9ff, 0.55);
  fill.position.set(-0.6, 0.5, 1.5);
  scene.add(fill);

  // Môi trường phản chiếu (cho ống nhựa bóng)
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  // Camera cố định, đặt chính giữa phía trước, nhìn hơi chếch xuống, tự lùi cho vừa khung hình
  const lookAt = new THREE.Vector3(0, (G + 0.08 - soilThick) / 2, 0);
  const ELEVATION = (22 * Math.PI) / 180;
  function frameCamera() {
    const fovY = (camera.fov * Math.PI) / 180;
    const visibleH = (G + 0.1 + soilThick) * Math.cos(ELEVATION) + (Wg / 2 + zc) * Math.sin(ELEVATION);
    const visibleW = Lg;
    const distH = visibleH / 2 / Math.tan(fovY / 2);
    const distW = visibleW / 2 / (Math.tan(fovY / 2) * camera.aspect);
    const dist = Math.max(distH, distW) * 1.08;
    camera.position.set(0, lookAt.y + dist * Math.sin(ELEVATION), dist * Math.cos(ELEVATION));
    camera.lookAt(lookAt);
  }

  const waterCurrent = new THREE.Color(WATER_COLORS.unknown); // màu nước hiện tại (chuyển dần sang màu đích)
  UW.uWaterColor.value = waterCurrent;

  // --- Bề mặt có "hạt" ---
  const soilDetail = makeDetailTextures(256, 11, 0.6, 1.0);
  const rockDetail = makeDetailTextures(128, 29, 0.55, 1.0);

  // =====================================================================
  // ĐỊA HÌNH: một lưới điểm, mỗi điểm có độ cao.
  // Ngoài miệng ao = bờ cỏ phẳng (y = G). Trong miệng ao, đi vào trong thì dốc xuống dần
  // tới đáy (y = 0). "d" = khoảng cách từ điểm đó tới mép miệng ao (dương = nằm trong ao).
  // Lưới chỉ lấy tới mặt phẳng cắt z = zc, phần phía trước bị bỏ đi.
  // =====================================================================
  function distIntoPond(x, z) {
    return Math.min(L / 2 - Math.abs(x), W / 2 - Math.abs(z));
  }
  function terrainHeight(x, z) {
    const d = distIntoPond(x, z);
    return d <= 0 ? G : G * (1 - Math.min(d / run, 1));
  }

  // Các tọa độ lưới trên một trục: chắc chắn có đường lưới đúng chỗ mép ao và chân mái dốc
  function axisCoords(half, rimHalf) {
    const keys = [-half, -rimHalf, -rimHalf + run, rimHalf - run, rimHalf, half];
    const out = [keys[0]];
    for (let i = 1; i < keys.length; i++) {
      const n = Math.max(1, Math.round((keys[i] - keys[i - 1]) / 0.03));
      for (let k = 1; k <= n; k++) out.push(keys[i - 1] + ((keys[i] - keys[i - 1]) * k) / n);
    }
    return out;
  }

  const grassColor = new THREE.Color(0x5f9440);
  const bankColor = new THREE.Color(0x8b6b4a);   // đất mái dốc
  const mudColor = new THREE.Color(0x7a6046);    // bùn đáy ao
  const tmp = new THREE.Color();
  function terrainColor(x, z) {
    const d = distIntoPond(x, z);
    if (d > 0) return tmp.copy(bankColor).lerp(mudColor, Math.min(d / run, 1));
    // cỏ hơi loang màu cho tự nhiên, sát mép ao chuyển dần sang đất
    tmp.copy(grassColor).multiplyScalar(0.9 + 0.1 * (Math.sin(x * 91.7 + z * 47.3) * 0.5 + 0.5));
    return d > -0.025 ? tmp.lerp(bankColor, (d + 0.025) / 0.025) : tmp;
  }

  {
    const xs = axisCoords(Lg / 2, L / 2);
    const zs = axisCoords(Wg / 2, W / 2).filter((z) => z <= zc + 1e-9);
    const nx = xs.length, nz = zs.length;
    const pos = new Float32Array(nx * nz * 3);
    const col = new Float32Array(nx * nz * 3);
    const uv = new Float32Array(nx * nz * 2);
    for (let j = 0; j < nz; j++) {
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i;
        const c = terrainColor(xs[i], zs[j]);
        pos.set([xs[i], terrainHeight(xs[i], zs[j]), zs[j]], k * 3);
        col.set([c.r, c.g, c.b], k * 3);
        uv.set([xs[i] * 4, zs[j] * 4], k * 2); // họa tiết lặp lại mỗi 0.25 m
      }
    }
    const idx = [];
    for (let j = 0; j < nz - 1; j++) {
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
        // Ở các góc ao, đường gấp của mái dốc chạy chéo; chọn đường chéo cho khớp
        const crossesCorner = ((xs[i] + xs[i + 1]) / 2) * ((zs[j] + zs[j + 1]) / 2) < 0;
        if (!crossesCorner) idx.push(a, c, d, a, d, b);
        else idx.push(a, c, b, b, c, d);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, map: soilDetail.map, bumpMap: soilDetail.bump, bumpScale: 1.5, roughness: 1,
    });
    patchMaterial(mat, { underwater: true, caustics: true });
    const terrain = new THREE.Mesh(geo, mat);
    terrain.receiveShadow = true;
    terrain.castShadow = true;
    scene.add(terrain);
  }

  // Mặt cắt đất ở phía trước: hình chữ nhật đất trừ đi hình thang là lòng ao, có các lớp đất
  {
    const shape = new THREE.Shape();
    shape.moveTo(-Lg / 2, -soilThick);
    shape.lineTo(Lg / 2, -soilThick);
    shape.lineTo(Lg / 2, G);
    shape.lineTo(L / 2, G);
    shape.lineTo(L / 2 - run, 0);
    shape.lineTo(-L / 2 + run, 0);
    shape.lineTo(-L / 2, G);
    shape.lineTo(-Lg / 2, G);
    shape.closePath();
    const geo = new THREE.ShapeGeometry(shape);
    const p = geo.attributes.position;
    const uv = new Float32Array(p.count * 2);
    for (let i = 0; i < p.count; i++) {
      uv[i * 2] = ((p.getX(i) + Lg / 2) / Lg) * 2;
      uv[i * 2 + 1] = (p.getY(i) + soilThick) / (G + soilThick);
    }
    geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    const cap = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({ map: makeStrataTexture(512, 5), bumpMap: soilDetail.bump, bumpScale: 1.2, roughness: 1 })
    );
    cap.position.z = zc;
    cap.receiveShadow = true;
    scene.add(cap);
  }

  // --- Đá cuội dưới đáy ao: hình méo không đều, đủ cỡ ---
  const pebbleMats = [0x7d6a55, 0x9a8870, 0x6a5a48, 0x8b8579].map((hex) => {
    const m = new THREE.MeshStandardMaterial({ color: hex, roughness: 0.95, bumpMap: rockDetail.bump, bumpScale: 2 });
    patchMaterial(m, { underwater: true, caustics: true });
    return m;
  });
  for (let i = 0; i < 70; i++) {
    const geo = new THREE.IcosahedronGeometry(1, 1);
    const pp = geo.attributes.position;
    for (let v = 0; v < pp.count; v++) {
      const k = 0.82 + Math.random() * 0.3; // méo từng đỉnh cho hòn đá không tròn
      pp.setXYZ(v, pp.getX(v) * k, pp.getY(v) * k * 0.65, pp.getZ(v) * k);
    }
    geo.computeVertexNormals();
    const r = 0.004 + Math.pow(Math.random(), 2) * 0.012;
    const pebble = new THREE.Mesh(geo, pebbleMats[i % pebbleMats.length]);
    pebble.scale.setScalar(r);
    pebble.rotation.y = Math.random() * 6;
    pebble.position.set((Math.random() - 0.5) * 2 * aBot * 0.95, r * 0.15, -bBot * 0.95 + Math.random() * (bBot * 0.95 + zc * 0.95));
    pebble.castShadow = true;
    pebble.receiveShadow = true;
    scene.add(pebble);
  }

  // --- Cây thủy sinh dưới đáy ao và cỏ trên bờ: lá mềm đung đưa ---
  const leafGeo = makeBladeGeometry(0.14, 0x173d22, 0x3f8a40);
  const leafMat = new THREE.MeshStandardMaterial({ vertexColors: true, side: THREE.DoubleSide, roughness: 0.65 });
  patchMaterial(leafMat, { underwater: true, caustics: false, sway: true });
  const clusters = [[-0.75, -0.3], [0.7, 0.2], [0.45, -0.6], [-0.4, 0.5]];
  makeBlades(
    scene, leafGeo, leafMat, 150, H * 0.22, H * 0.42,
    () => {
      const [fx, fz] = clusters[Math.floor(Math.random() * clusters.length)];
      return { x: fx * aBot + (Math.random() - 0.5) * 0.05, y: 0.003, z: fz * bBot + (Math.random() - 0.5) * 0.05 };
    },
    [0x2f8f4e, 0x3da35d, 0x24733d, 0x4aa860]
  );

  const grassGeo = makeBladeGeometry(0.1, 0x2f5a22, 0x93c24f);
  const grassMat = new THREE.MeshStandardMaterial({ vertexColors: true, side: THREE.DoubleSide, roughness: 0.8 });
  patchMaterial(grassMat, { sway: true });
  makeBlades(
    scene, grassGeo, grassMat, 1000, 0.01, 0.024,
    () => {
      for (let tries = 0; tries < 20; tries++) {
        const x = (Math.random() - 0.5) * Lg;
        const z = -Wg / 2 + Math.random() * (zc + Wg / 2);
        if (distIntoPond(x, z) < -0.03) return { x, y: G, z };
      }
      return { x: -Lg / 2 + 0.01, y: G, z: -Wg / 2 + 0.01 };
    },
    [0x6aa045, 0x86b552, 0x5a9040, 0x9bc25f]
  );

  // =====================================================================
  // NƯỚC
  // =====================================================================
  // Mặt trước của khối nước (mặt phẳng cắt): trong hơn ở gần mặt, đậm dần xuống sâu
  const frontMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { uColor: { value: waterCurrent }, uSurfaceY: UW.uSurfaceY, uTime: UW.uTime },
    vertexShader: `
      varying float vY;
      void main() {
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vY = wp.y;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: `
      uniform vec3 uColor;
      uniform float uSurfaceY, uTime;
      varying float vY;
      void main() {
        float d = clamp((uSurfaceY - vY) / max(uSurfaceY, 0.001), 0.0, 1.0);
        vec3 col = uColor * mix(1.3, 0.55, d);
        float a = mix(0.1, 0.5, d) + 0.025 * sin(vY * 140.0 + uTime * 1.3);
        gl_FragColor = vec4(col, a);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  const waterGeo = new THREE.BoxGeometry(1, 1, 1);
  waterGeo.setDrawRange(24, 6); // chỉ vẽ mặt +z (mặt trước); các mặt khác nằm sau đất
  const wp = waterGeo.attributes.position;
  // Nhớ đỉnh nào ở mặt trên/dưới và ở góc nào của hộp gốc trước khi bóp méo nó
  const wTop = [], wSx = [], wSz = [];
  for (let i = 0; i < wp.count; i++) {
    wTop.push(wp.getY(i) > 0);
    wSx.push(Math.sign(wp.getX(i)));
    wSz.push(Math.sign(wp.getZ(i)));
  }
  const water = new THREE.Mesh(waterGeo, frontMat);
  water.renderOrder = 1;
  water.frustumCulled = false;
  scene.add(water);

  // Mặt nước: sóng gợn, phản chiếu bầu trời (càng nhìn chếch càng phản chiếu), vệt nắng, gợn tròn khi bơm/sục chạy
  const skyTop = new THREE.Color().setRGB(0.42, 0.62, 0.88, THREE.SRGBColorSpace);
  const skyHorizon = new THREE.Color().setRGB(0.85, 0.92, 0.97, THREE.SRGBColorSpace);
  const surfaceMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      uTime: UW.uTime,
      uColor: { value: waterCurrent },
      uSkyTop: { value: skyTop },
      uSkyHorizon: { value: skyHorizon },
      uRipple1: { value: new THREE.Vector3(0, 0, 0) }, // x, z, độ mạnh (gợn tròn do ống bơm)
      uRipple2: { value: new THREE.Vector3(0, 0, 0) }, // gợn do bọt khí nổi lên
    },
    vertexShader: `
      varying vec3 vPos;
      void main() {
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vPos = wp.xyz;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: `
      uniform float uTime;
      uniform vec3 uColor, uSkyTop, uSkyHorizon, uRipple1, uRipple2;
      varying vec3 vPos;
      float height(vec2 p) {
        float h = sin(p.x * 22.0 + uTime * 1.3) * 0.5 + sin(p.y * 27.0 - uTime * 1.7) * 0.5
                + sin((p.x + p.y) * 37.0 + uTime * 2.1) * 0.3 + sin((p.x - p.y) * 49.0 - uTime * 1.1) * 0.2;
        float r1 = length(p - uRipple1.xy);
        float r2 = length(p - uRipple2.xy);
        h += uRipple1.z * 2.0 * sin(r1 * 170.0 - uTime * 9.0) * exp(-r1 * 20.0);
        h += uRipple2.z * 1.5 * sin(r2 * 150.0 - uTime * 7.0) * exp(-r2 * 24.0);
        return h;
      }
      void main() {
        vec2 p = vPos.xz;
        float e = 0.003;
        float hx = height(p + vec2(e, 0.0)) - height(p - vec2(e, 0.0));
        float hz = height(p + vec2(0.0, e)) - height(p - vec2(0.0, e));
        vec3 n = normalize(vec3(-hx * 0.35, 1.0, -hz * 0.35));
        vec3 V = normalize(cameraPosition - vPos);
        float fres = pow(1.0 - max(dot(n, V), 0.0), 3.0);
        vec3 R = reflect(-V, n);
        vec3 sky = mix(uSkyHorizon, uSkyTop, clamp(R.y * 1.5, 0.0, 1.0));
        vec3 col = mix(uColor * 0.9, sky, clamp(fres * 0.6 + 0.03, 0.0, 1.0));
        vec3 Ldir = normalize(vec3(0.55, 1.4, 1.0));
        col += vec3(1.0, 0.95, 0.85) * pow(max(dot(reflect(-Ldir, n), V), 0.0), 110.0) * 1.3;
        gl_FragColor = vec4(col, mix(0.16, 0.7, clamp(fres * 1.1, 0.0, 1.0)));
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  const surface = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), surfaceMat);
  surface.rotation.x = -Math.PI / 2;
  surface.renderOrder = 2;
  surface.frustumCulled = false;
  scene.add(surface);

  let surfaceY = 0.001;                    // độ cao mặt nước
  let waterInset = run;                    // mép nước cách mép miệng ao bao xa (nước thấp thì xa hơn)
  let waterTarget = new THREE.Color(WATER_COLORS.unknown);

  // ratio = tỉ lệ so với độ sâu ao (0 → 1)
  function setWaterLevel(ratio) {
    const yw = Math.min(Math.max(ratio, 0.05), 1) * H;
    surfaceY = yw;
    UW.uSurfaceY.value = yw;
    waterInset = run * (1 - yw / G);
    const out = 0.003; // nước nở ra 3 mm vào trong đất để không hở khe (phần chìm trong đất bị đất che)
    for (let i = 0; i < wp.count; i++) {
      const inset = wTop[i] ? waterInset : run;
      const z = wSz[i] > 0 ? zc : -(W / 2 - inset + out); // mặt trước dừng ở mặt phẳng cắt
      wp.setXYZ(i, wSx[i] * (L / 2 - inset + out), wTop[i] ? yw : 0.001, z);
    }
    wp.needsUpdate = true;
    waterGeo.computeVertexNormals();
    const zBack = -(W / 2 - waterInset + out);
    surface.position.set(0, yw + 0.0005, (zc + zBack) / 2);
    surface.scale.set(L - 2 * waterInset + 2 * out, zc - zBack, 1);
  }
  // Chưa có cảm biến mực nước thì dùng water_level_fixed_m trong config (mét tính từ đáy ao), nếu null thì mặc định để vẽ
  const fixed = geometry.water_level_fixed_m;
  setWaterLevel(typeof fixed === "number" ? fixed / H : WATER_FILL_DEFAULT);

  // =====================================================================
  // MÁY SỤC KHÍ: dây khí đi từ ngoài bờ trái, trượt dọc mái dốc xuống viên đá sục dưới đáy
  // =====================================================================
  const stoneX = -L / 2 + run + 0.05;
  const stoneMat = new THREE.MeshStandardMaterial({ color: 0x555555, roughness: 0.9, bumpMap: rockDetail.bump, bumpScale: 2 });
  patchMaterial(stoneMat, { underwater: true, caustics: false });
  const stone = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.016, 0.014, 20), stoneMat);
  stone.position.set(stoneX, 0.009, 0);
  stone.castShadow = true;
  scene.add(stone);

  const slopeY = (x) => G * (1 - (x + L / 2) / run); // độ cao mái dốc trái tại vị trí x
  const airline = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-Lg / 2, G + 0.004, 0),
    new THREE.Vector3(-L / 2 - 0.015, G + 0.012, 0),
    new THREE.Vector3(-L / 2 + 0.25 * run, slopeY(-L / 2 + 0.25 * run) + 0.012, 0),
    new THREE.Vector3(-L / 2 + 0.65 * run, slopeY(-L / 2 + 0.65 * run) + 0.012, 0),
    new THREE.Vector3(stoneX, 0.02, 0),
  ], false, "centripetal");
  const airMat = new THREE.MeshStandardMaterial({ color: 0x1f1f1f, roughness: 0.4 });
  patchMaterial(airMat, { underwater: true, caustics: false });
  const airTube = new THREE.Mesh(new THREE.TubeGeometry(airline, 64, 0.003, 8), airMat);
  airTube.castShadow = true;
  scene.add(airTube);

  const bubbleTexture = makeBubbleTexture();
  const BUBBLES = 40;
  const bubblePos = new Float32Array(BUBBLES * 3);
  const bubbleSpeed = new Float32Array(BUBBLES);
  const bubblePhase = new Float32Array(BUBBLES);
  function resetBubble(i, anyHeight) {
    bubblePos[i * 3] = stoneX + (Math.random() - 0.5) * 0.02;
    bubblePos[i * 3 + 1] = anyHeight ? 0.02 + Math.random() * (surfaceY - 0.02) : 0.02;
    bubblePos[i * 3 + 2] = (Math.random() - 0.5) * 0.02;
    bubbleSpeed[i] = 0.08 + Math.random() * 0.12; // mét/giây
    bubblePhase[i] = Math.random() * 6.28;
  }
  for (let i = 0; i < BUBBLES; i++) resetBubble(i, true);
  const bubbleGeo = new THREE.BufferGeometry();
  bubbleGeo.setAttribute("position", new THREE.BufferAttribute(bubblePos, 3));
  const bubbles = new THREE.Points(
    bubbleGeo,
    new THREE.PointsMaterial({ color: 0xffffff, size: 0.018, map: bubbleTexture, transparent: true, depthWrite: false })
  );
  bubbles.frustumCulled = false;
  bubbles.visible = false; // chỉ hiện khi máy sục BẬT
  bubbles.renderOrder = 3;
  scene.add(bubbles);

  // =====================================================================
  // BƠM LÀM MÁT: ống từ ngoài bờ phải vắt qua mái dốc, nước chảy xuống ao
  // =====================================================================
  const pipeY = G + 0.07;
  const outletX = L / 2 - waterInset - 0.06; // đầu ống nằm phía trên mặt nước
  const pipeMat = new THREE.MeshStandardMaterial({ color: 0xc9ced3, roughness: 0.35, metalness: 0.1, envMap: envTex, envMapIntensity: 0.6 });
  const pipeLen = Lg / 2 - outletX;
  const pipe = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, pipeLen, 20), pipeMat);
  pipe.rotation.z = Math.PI / 2; // xoay ống nằm ngang
  pipe.position.set((Lg / 2 + outletX) / 2, pipeY, 0);
  pipe.castShadow = true;
  scene.add(pipe);
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.005, 0.005, pipeY - G, 12), pipeMat); // trụ đỡ ống
  post.position.set(L / 2 + 0.5 * B, (G + pipeY) / 2, 0);
  post.castShadow = true;
  scene.add(post);

  const DROPS = 30;
  const dropPos = new Float32Array(DROPS * 3);
  function resetDrop(i, anyHeight) {
    dropPos[i * 3] = outletX + (Math.random() - 0.5) * 0.008;
    dropPos[i * 3 + 1] = anyHeight ? surfaceY + Math.random() * (pipeY - surfaceY) : pipeY;
    dropPos[i * 3 + 2] = (Math.random() - 0.5) * 0.008;
  }
  for (let i = 0; i < DROPS; i++) resetDrop(i, true);
  const dropGeo = new THREE.BufferGeometry();
  dropGeo.setAttribute("position", new THREE.BufferAttribute(dropPos, 3));
  const drops = new THREE.Points(
    dropGeo,
    new THREE.PointsMaterial({ color: 0xbfe3ff, size: 0.016, map: makeDropTexture(), transparent: true, depthWrite: false })
  );
  drops.frustumCulled = false;
  drops.visible = false; // chỉ hiện khi bơm BẬT
  drops.renderOrder = 3;
  scene.add(drops);

  // Vị trí các gợn tròn trên mặt nước (nơi nước rơi xuống, nơi bọt khí nổi lên)
  surfaceMat.uniforms.uRipple1.value.set(outletX, 0, 0);
  surfaceMat.uniforms.uRipple2.value.set(stoneX, 0, 0);
  let ripple1 = 0, ripple2 = 0;

  // --- Các hàm cho bên ngoài gọi ---
  function setLevel(level) {
    waterTarget = new THREE.Color(WATER_COLORS[level] ?? WATER_COLORS.unknown);
  }
  function setAerator(on) {
    bubbles.visible = on;
    stoneMat.color.set(on ? 0x2f9e44 : 0x555555); // viên đá sục xanh lá khi máy chạy
  }
  function setPump(on) {
    drops.visible = on;
    pipeMat.color.set(on ? 0x2f86e0 : 0xc9ced3); // ống bơm xanh dương khi bơm chạy
  }

  // Gọi mỗi khung hình: nước đổi màu, bọt khí nổi, giọt nước rơi, cây đung đưa
  let time = 0;
  function update(dt) {
    time += dt;
    UW.uTime.value = time;
    waterCurrent.lerp(waterTarget, 0.08);

    if (bubbles.visible) {
      for (let i = 0; i < BUBBLES; i++) {
        bubblePos[i * 3 + 1] += bubbleSpeed[i] * dt;
        bubblePos[i * 3] += Math.sin(time * 6 + bubblePhase[i]) * 0.0005; // bọt lắc lư khi nổi
        if (bubblePos[i * 3 + 1] > surfaceY) resetBubble(i, false);
      }
      bubbleGeo.attributes.position.needsUpdate = true;
    }
    if (drops.visible) {
      for (let i = 0; i < DROPS; i++) {
        dropPos[i * 3 + 1] -= 0.7 * dt; // rơi 0.7 m/s
        if (dropPos[i * 3 + 1] < surfaceY) resetDrop(i, false);
      }
      dropGeo.attributes.position.needsUpdate = true;
    }
    // Gợn nước hiện dần khi thiết bị chạy, tắt dần khi dừng
    ripple1 += ((drops.visible ? 1 : 0) - ripple1) * Math.min(dt * 3, 1);
    ripple2 += ((bubbles.visible ? 1 : 0) - ripple2) * Math.min(dt * 3, 1);
    surfaceMat.uniforms.uRipple1.value.z = ripple1;
    surfaceMat.uniforms.uRipple2.value.z = ripple2;

    renderer.render(scene, camera);
  }

  // Khung vẽ co giãn theo kích thước ô chứa; camera tự canh lại cho vừa khung
  function resize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    frameCamera();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  return { setWaterLevel, setLevel, setAerator, setPump, update };
}

function startScene() {
  const g = config.pond_geometry_m;
  if (!g || !g.length || !g.width || !g.height) {
    showNotice("Config thiếu pond_geometry_m nên không vẽ được mô hình 3D.", true);
    return;
  }
  try {
    model = createPondModel($("scene"), g);
  } catch (err) {
    console.error(err);
    showNotice("Trình duyệt không bật được WebGL nên mô hình 3D bị tắt. Phần còn lại vẫn dùng bình thường.", true);
    return;
  }
  const clock = new THREE.Clock();
  (function loop() {
    requestAnimationFrame(loop);
    model.update(Math.min(clock.getDelta(), 0.1));
  })();
}

// =====================================================================
// 7. BIỂU ĐỒ (Chart.js)
// =====================================================================
// Mỗi số đo một biểu đồ riêng (cùng thang thời gian), vì các số đo khác đơn vị và độ lớn
// (TDS hàng trăm, pH vài đơn vị): vẽ chung một trục sẽ làm pH và nhiệt độ thành đường thẳng.

// Plugin nhỏ: kẻ đường nét đứt ngang tại các ngưỡng (lấy từ config) cho dễ so sánh
const thresholdPlugin = {
  id: "thresholdLines",
  afterDatasetsDraw(chart, args, opts) {
    const { ctx, chartArea, scales } = chart;
    ctx.save();
    ctx.strokeStyle = "#898781";
    ctx.fillStyle = "#6b6a65";
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.font = "11px system-ui, sans-serif";
    for (const line of opts.lines || []) {
      if (line.value < scales.y.min || line.value > scales.y.max) continue;
      const y = scales.y.getPixelForValue(line.value);
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.fillText(`${line.label} ${line.value}`, chartArea.left + 4, y - 3);
    }
    ctx.restore();
  },
};

function thresholdLinesFor(item) {
  if (item.id === "do") {
    const d = config.do_model;
    return [
      { value: d.aerator_on_below, label: "bật sục khí" },
      { value: d.aerator_off_above, label: "tắt sục khí" },
    ];
  }
  return [
    { value: item.warnRange[0], label: "ngưỡng dưới" },
    { value: item.warnRange[1], label: "ngưỡng trên" },
  ];
}

function buildCharts() {
  const wrap = $("charts");
  wrap.innerHTML = "";
  charts = [];

  for (const item of items) {
    const box = document.createElement("div");
    box.className = "chart-box";
    const title = document.createElement("h3");
    title.textContent = item.unit ? `${item.label} (${item.unit})` : item.label;
    const canvasWrap = document.createElement("div");
    canvasWrap.className = "chart-wrap";
    const canvas = document.createElement("canvas");
    canvasWrap.appendChild(canvas);
    box.append(title, canvasWrap);
    wrap.appendChild(box);

    const lines = thresholdLinesFor(item);
    const values = lines.map((l) => l.value);

    const chart = new Chart(canvas, {
      type: "line",
      data: {
        datasets: [{
          label: item.label,
          data: [],                       // các điểm {x: mili giây, y: giá trị}
          borderColor: "#2a78d6",
          backgroundColor: "#2a78d6",
          borderWidth: 2,
          pointRadius: 0,                 // không vẽ chấm; chỉ hiện chấm khi rê chuột
          pointHoverRadius: 4,
          tension: 0,
        }],
      },
      options: {
        parsing: false,                   // dữ liệu đã ở dạng {x, y}
        animation: false,                 // cập nhật mỗi 5 giây, không cần hiệu ứng
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            type: "linear",               // trục thời gian dạng số (mili giây), tự format ở dưới
            ticks: { maxTicksLimit: 6, color: "#6b6a65", callback: (v) => fmtTime(v, false) },
            grid: { color: "#ecebe7" },
          },
          y: {
            suggestedMin: Math.min(...values),   // luôn đủ chỗ để thấy đường ngưỡng
            suggestedMax: Math.max(...values),
            ticks: { color: "#6b6a65" },
            grid: { color: "#ecebe7" },
          },
        },
        plugins: {
          legend: { display: false },     // 1 đường/biểu đồ, tiêu đề đã nói rõ
          thresholdLines: { lines },
          tooltip: {
            callbacks: {
              title: (ctxs) => fmtTime(ctxs[0].parsed.x, true),
              label: (ctx) => `${fmt(ctx.parsed.y)} ${item.unit}`.trim(),
            },
          },
        },
      },
      plugins: [thresholdPlugin],
    });
    charts.push({ item, chart });
  }
}

// Trượt cửa sổ thời gian: hiện [bây giờ - 60 phút, bây giờ], bỏ điểm cũ hơn.
// (Giả định đồng hồ trình duyệt ≈ đồng hồ server, đúng khi mở trên chính laptop chạy server.)
function refreshWindow() {
  const now = Date.now();
  const min = now - HISTORY_MINUTES * 60 * 1000;
  for (const { chart } of charts) {
    const data = chart.data.datasets[0].data;
    while (data.length && data[0].x < min) data.shift();
    chart.options.scales.x.min = min;
    chart.options.scales.x.max = now;
    chart.update("none");
  }
}

// Tải 60 phút lịch sử từ server, thay toàn bộ dữ liệu biểu đồ
async function loadHistory() {
  try {
    const res = await fetch(`/api/history?minutes=${HISTORY_MINUTES}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    for (const { item, chart } of charts) {
      chart.data.datasets[0].data = body.readings.map((row) => ({
        x: row.ts * 1000,                 // ts server là giây, Chart.js dùng mili giây
        y: valueFromRow(item, row),
      }));
    }
    refreshWindow();
  } catch (err) {
    console.error(err);
    showNotice("Không tải được lịch sử 60 phút từ server. Biểu đồ vẫn cập nhật khi có số đo mới.", true);
  }
}

// Mỗi tin pond1/twin mới = một điểm mới trên mọi biểu đồ.
// Dùng ts của twin (giờ server) để cùng thang giờ với lịch sử trong database.
function addLivePoint() {
  const twin = live.twin;
  if (!twin || typeof twin.ts !== "number") return;
  const x = twin.ts * 1000;
  for (const { item, chart } of charts) {
    const data = chart.data.datasets[0].data;
    if (data.length && x <= data[data.length - 1].x) continue; // đã có (tin gửi lại khi mới nối WebSocket)
    const y = item.id === "do"
      ? cleanValue(item, twin.do)
      : cleanValue(item, live.sensors && live.sensors[item.id]);
    data.push({ x, y });
  }
  refreshWindow();
}

// =====================================================================
// 8. TRẠNG THÁI THIẾT BỊ + NÚT ĐIỀU KHIỂN
// =====================================================================
const DEVICE_UI = {
  aerator: { stateEl: "aerator-state", btnEl: "btn-aerator", name: "Máy sục khí" },
  pump: { stateEl: "pump-state", btnEl: "btn-pump", name: "Bơm làm mát" },
};

// Vẽ lại dòng trạng thái và nút theo: trạng thái THẬT (live.state), đang chờ hay không, WebSocket sống hay chết
function renderDevices() {
  for (const device of Object.keys(DEVICE_UI)) {
    const ui = DEVICE_UI[device];
    const stateEl = $(ui.stateEl);
    const btn = $(ui.btnEl);
    const known = live.state && typeof live.state[device] === "boolean";
    const on = known && live.state[device];

    if (pending[device]) {
      stateEl.textContent = "Đang chờ ESP32...";
      stateEl.className = "state-pending";
    } else if (!known) {
      stateEl.textContent = "Chưa có trạng thái từ ESP32";
      stateEl.className = "muted";
    } else {
      stateEl.textContent = on ? "Đang BẬT" : "Đang TẮT";
      stateEl.className = on ? "state-on" : "muted";
      const left = autoPause[device] || 0;
      if (left > 0) stateEl.textContent += ` (điều khiển tay, tự động tạm dừng còn ${left} s)`;
    }

    // Cả hai thiết bị là nút bật/tắt. Bơm bật tay chạy liên tục tới khi bấm tắt
    // (server gia hạn mỗi lần có số đo; mất kết nối thì ESP32 tự tắt sau pump.renew_s giây).
    const words = device === "aerator" ? "sục khí" : "bơm";
    btn.textContent = on ? `Tắt ${words}` : `Bật ${words}`;
    btn.disabled = !known || !!pending[device] || !wsConnected;
  }

  // Mô hình 3D chỉ theo trạng thái THẬT
  if (model && live.state) {
    model.setAerator(live.state.aerator === true);
    model.setPump(live.state.pump === true);
  }
}

function clearPending(device) {
  if (pending[device]) clearTimeout(pending[device].timer);
  pending[device] = null;
}

async function explainError(res) {
  let detail = null;
  try { detail = (await res.json()).detail; } catch (e) { /* không có nội dung JSON */ }
  if (res.status === 422) {
    // detail là chữ khi lỗi do quy tắc của server; là danh sách khi body gửi lên sai dạng
    return typeof detail === "string"
      ? `Server không nhận lệnh: ${detail}`
      : "Server không nhận lệnh vì dữ liệu gửi lên sai dạng (422).";
  }
  if (res.status === 404) return "Server không có thiết bị này (404).";
  if (res.status === 503) {
    return "Server chưa nối được tới broker MQTT nên chưa gửi được lệnh (503). Hãy kiểm tra Mosquitto đã chạy chưa rồi thử lại.";
  }
  return `Server báo lỗi không rõ (mã ${res.status}).`;
}

// Gửi lệnh tay. wantState = trạng thái ta MONG ĐỢI ESP32 báo lại (để biết khi nào hết chờ).
async function sendCommand(device, body, wantState) {
  if (pending[device]) return;

  // Từ lúc này nút bị khoá và hiện "đang chờ ESP32...". KHÔNG đổi trạng thái hiển thị.
  pending[device] = {
    want: wantState,
    timer: setTimeout(() => {
      clearPending(device);
      renderDevices();
      showNotice(
        `ESP32 chưa báo lại sau ${PENDING_TIMEOUT_MS / 1000} giây. Kiểm tra ESP32 có đang bật và nối WiFi/MQTT không.`,
        true
      );
    }, PENDING_TIMEOUT_MS),
  };
  renderDevices();

  try {
    const res = await fetch(`/api/cmd/${device}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      clearPending(device);
      renderDevices();
      showNotice(await explainError(res), true);
    }
    // res.ok: server trả {"sent": true} = chỉ mới gửi lệnh đi. Cứ chờ pond1/state.
  } catch (err) {
    clearPending(device);
    renderDevices();
    showNotice("Không kết nối được tới server nên chưa gửi được lệnh.", true);
  }
}

function setupButtons() {
  $("btn-aerator").addEventListener("click", () => {
    const current = live.state && live.state.aerator;
    if (typeof current !== "boolean") return;
    const want = !current; // đang bật thì gửi lệnh tắt, và ngược lại
    sendCommand("aerator", { on: want }, want);
  });

  $("btn-pump").addEventListener("click", () => {
    const current = live.state && live.state.pump;
    if (typeof current !== "boolean") return;
    const want = !current;
    // Server tự kèm seconds = pump.renew_s và gia hạn, nên ở đây chỉ gửi on/off
    sendCommand("pump", { on: want }, want);
  });
}

// =====================================================================
// 9. WEBSOCKET
// =====================================================================
function setConnected(ok) {
  wsConnected = ok;
  const badge = $("conn-status");
  badge.className = ok ? "badge conn-ok" : "badge conn-bad";
  badge.textContent = ok ? "Đã kết nối" : "Mất kết nối, đang thử nối lại...";
  renderDevices(); // khoá/mở nút theo kết nối
}

// Mỗi tin có dạng {"topic": "pond1/sensors", "data": {...}}. Chỉ cần phần cuối của topic.
function handleMessage(msg) {
  const kind = String(msg.topic).split("/").pop();
  const data = msg.data;
  if (!data || typeof data !== "object") return;

  if (kind === "sensors") {
    live.sensors = data;
    updateCards();
  } else if (kind === "twin") {
    live.twin = data;
    updateCards();
    updateOverallLevel();
    addLivePoint();
  } else if (kind === "state") {
    live.state = data;
    // ESP32 báo trạng thái đúng như mong đợi -> hết chờ
    for (const device of Object.keys(pending)) {
      if (pending[device] && data[device] === pending[device].want) clearPending(device);
    }
    renderDevices();
  }
}

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    setConnected(true);
    loadHistory(); // nối lại sau khi mất kết nối thì tải lại để lấp khoảng trống trên biểu đồ
  };
  ws.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (err) {
      console.error("Tin WebSocket lỗi:", err);
    }
  };
  ws.onclose = () => {
    setConnected(false);
    setTimeout(connectWebSocket, RECONNECT_MS); // tự nối lại
  };
  ws.onerror = () => ws.close(); // lỗi thì đóng, onclose sẽ lo việc nối lại
}

// Hỏi server còn tạm dừng tự động bao lâu (chỉ để HIỂN THỊ, không đổi trạng thái thiết bị)
async function pollAutoPause() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const body = await res.json();
    if (body.auto_pause_s && typeof body.auto_pause_s === "object") {
      autoPause = body.auto_pause_s;
      renderDevices();
    }
  } catch (err) {
    /* server tắt: WebSocket đã báo mất kết nối, ở đây im lặng */
  }
}

// =====================================================================
// 10. KHỞI ĐỘNG
// =====================================================================
async function main() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    config = await res.json();
  } catch (err) {
    console.error(err);
    showNotice("Không tải được cấu hình từ server (/api/config). Hãy kiểm tra server đang chạy rồi tải lại trang.", true);
    return;
  }

  items = buildItems(config);
  buildCards();
  buildCharts();
  startScene();
  setupButtons();
  updateCards();
  updateOverallLevel();
  renderDevices();

  setInterval(refreshWindow, 5000); // cửa sổ thời gian vẫn trôi dù không có số đo mới
  setInterval(pollAutoPause, PAUSE_POLL_MS);
  connectWebSocket();
}

main();
