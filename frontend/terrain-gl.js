/* WebGL terrain renderer for the Wayanad Risk Observatory.
 *
 * Draws the real SRTM elevation grid as a high-resolution mesh with per-pixel hillshading, the Sentinel-2 satellite
 * composite and the selected hazard layer as smooth textures, and a proper depth buffer. The camera maths is the
 * SAME oblique orthographic projection used by app.js `project()` (yaw / pitch / elevation exaggeration / scale /
 * pan), so routes, markers, boundaries and labels drawn on the 2D overlay canvas line up with the terrain exactly.
 *
 *   world x : west -> east, [-1, 1]            world z : north -> south, [-aspect, aspect]
 *   height  : metres / widthM * 2  (isotropic with x and z), multiplied by the exaggeration
 *
 * Falls back (constructor throws) when WebGL is unavailable; app.js then keeps using its Canvas 2D renderer.
 */
(function () {
  'use strict';

  const VS = `
    precision highp float;
    attribute vec2 aXZ;      // world x, z
    attribute float aH;      // height in metres
    attribute vec2 aGrad;    // dh/dx, dh/dz (dimensionless slopes)
    uniform vec2 uView;      // canvas size in CSS pixels
    uniform float uYaw, uPitch, uScale, uElev, uFlat, uCx, uCz, uWidthM, uAspect;
    varying vec2 vUV;
    varying vec2 vGrad;
    varying float vH;
    void main() {
      float x = aXZ.x - uCx, z = aXZ.y - uCz;
      float cy = cos(uYaw), sy = sin(uYaw);
      float rx = x * cy - z * sy, rz = x * sy + z * cy;
      float p = uFlat > 0.5 ? 0.0 : uPitch;
      float e = uFlat > 0.5 ? 0.0 : (aH / uWidthM * 2.0) * uElev;
      float sx = uView.x * 0.47 + rx * uScale;
      float sy2 = uView.y * 0.57 + (rz * cos(p) - e * sin(p)) * uScale;
      float depth = rz * sin(p) + e * cos(p);
      gl_Position = vec4(sx / uView.x * 2.0 - 1.0, 1.0 - sy2 / uView.y * 2.0, -depth / 6.0, 1.0);
      vUV = vec2((aXZ.x + 1.0) * 0.5, (aXZ.y / uAspect + 1.0) * 0.5);
      vGrad = aGrad;
      vH = aH;
    }`;

  const FS = `
    #ifdef GL_FRAGMENT_PRECISION_HIGH
    precision highp float;
    #else
    precision mediump float;
    #endif
    uniform sampler2D uSat, uOver, uMask;
    uniform float uOpacity, uSatOn, uElev, uFlat, uHMin, uHMax, uShade;
    varying vec2 vUV;
    varying vec2 vGrad;
    varying float vH;
    vec3 hypso(float t) {
      vec3 a = vec3(0.24, 0.42, 0.27), b = vec3(0.52, 0.60, 0.34), c = vec3(0.72, 0.62, 0.40),
           d = vec3(0.61, 0.52, 0.45), e = vec3(0.93, 0.91, 0.86);
      if (t < 0.25) return mix(a, b, t / 0.25);
      if (t < 0.5) return mix(b, c, (t - 0.25) / 0.25);
      if (t < 0.8) return mix(c, d, (t - 0.5) / 0.3);
      return mix(d, e, (t - 0.8) / 0.2);
    }
    void main() {
      if (texture2D(uMask, vUV).r < 0.5) discard;
      vec3 base;
      if (uSatOn > 0.5) {
        vec3 s = texture2D(uSat, vUV).rgb;
        base = pow(s, vec3(0.74)) * 1.12;           // the dry-season composite is dark: lift mid-tones
      } else {
        base = hypso(clamp((vH - uHMin) / (uHMax - uHMin), 0.0, 1.0));
      }
      vec4 o = texture2D(uOver, vUV);
      vec3 col = mix(base, o.rgb, o.a * uOpacity);
      // hillshade: light from the north-west, 40 degrees above the horizon
      vec3 n = uFlat > 0.5 ? vec3(0.0, 1.0, 0.0) : normalize(vec3(-vGrad.x * uElev, 1.0, -vGrad.y * uElev));
      vec3 l = normalize(vec3(-0.62, 0.66, -0.62));
      float diff = max(dot(n, l), 0.0);
      float shade = mix(1.0, 0.30 + 0.95 * diff, uShade);
      float height = clamp((vH - uHMin) / (uHMax - uHMin), 0.0, 1.0);
      col *= shade * (0.92 + 0.10 * height);        // higher ground reads slightly brighter (atmospheric depth cue)
      gl_FragColor = vec4(col, 1.0);
    }`;

  const LINE_FS = `
    #ifdef GL_FRAGMENT_PRECISION_HIGH
    precision highp float;
    #else
    precision mediump float;
    #endif
    void main() { gl_FragColor = vec4(0.85, 0.92, 0.78, 0.18); }`;

  function compile(gl, type, src) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, src);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error('Shader error: ' + gl.getShaderInfoLog(shader));
    return shader;
  }
  function program(gl, fs) {
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('Program link error: ' + gl.getProgramInfoLog(p));
    return p;
  }

  class TerrainGL {
    constructor(canvas) {
      const opts = {antialias: true, alpha: true, premultipliedAlpha: false, preserveDrawingBuffer: true};
      const gl = canvas.getContext('webgl2', opts) || canvas.getContext('webgl', opts);
      if (!gl) throw new Error('WebGL is not available');
      this.gl = gl;
      this.canvas = canvas;
      this.isGL2 = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext;
      this.uint32 = this.isGL2 || !!gl.getExtension('OES_element_index_uint');
      this.aniso = gl.getExtension('EXT_texture_filter_anisotropic');
      this.prog = program(gl, FS);
      this.lineProg = program(gl, LINE_FS);
      this.u = {};
      for (const name of ['uView', 'uYaw', 'uPitch', 'uScale', 'uElev', 'uFlat', 'uCx', 'uCz', 'uWidthM', 'uAspect', 'uSat',
        'uOver', 'uMask', 'uOpacity', 'uSatOn', 'uHMin', 'uHMax', 'uShade']) this.u[name] = gl.getUniformLocation(this.prog, name);
      this.lu = {};
      for (const name of ['uView', 'uYaw', 'uPitch', 'uScale', 'uElev', 'uFlat', 'uCx', 'uCz', 'uWidthM', 'uAspect']) this.lu[name] = gl.getUniformLocation(this.lineProg, name);
      this.tex = {};
      this.ready = false;
    }

    texture(name, width, height, data, format, filter, mips) {
      const gl = this.gl;
      let t = this.tex[name];
      if (!t) t = this.tex[name] = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      if (data instanceof HTMLImageElement || data instanceof HTMLCanvasElement) gl.texImage2D(gl.TEXTURE_2D, 0, format, format, gl.UNSIGNED_BYTE, data);
      else gl.texImage2D(gl.TEXTURE_2D, 0, format, width, height, 0, format, gl.UNSIGNED_BYTE, data);
      const useMips = mips && this.isGL2;
      if (useMips) gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, useMips ? gl.LINEAR_MIPMAP_LINEAR : filter);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      if (useMips && this.aniso) gl.texParameterf(gl.TEXTURE_2D, this.aniso.TEXTURE_MAX_ANISOTROPY_EXT, 8);
      return t;
    }

    /** elevation: Int16Array (metres, row-major), classes: Uint8Array (0 = outside the district). */
    setTerrain({elevation, cols, rows, widthM, aspect, classes}) {
      const gl = this.gl;
      Object.assign(this, {cols, rows, widthM, aspect});
      // light 3x3 smoothing removes SRTM speckle before the mesh and normals are derived
      const h = new Float32Array(cols * rows);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        let sum = 0, n = 0;
        for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
          const rr = r + dr, cc = c + dc;
          if (rr < 0 || rr >= rows || cc < 0 || cc >= cols) continue;
          const v = elevation[rr * cols + cc];
          if (v === -32768) continue;
          sum += v; n++;
        }
        h[r * cols + c] = n ? sum / n : 0;
      }
      let hMin = Infinity, hMax = -Infinity;
      for (let i = 0; i < h.length; i++) if (classes[i]) { hMin = Math.min(hMin, h[i]); hMax = Math.max(hMax, h[i]); }
      this.hMin = hMin; this.hMax = hMax;
      const step = this.uint32 ? 2 : 3;
      const xs = [], ys = [];
      for (let c = 0; c < cols; c += step) xs.push(c);
      if (xs[xs.length - 1] !== cols - 1) xs.push(cols - 1);
      for (let r = 0; r < rows; r += step) ys.push(r);
      if (ys[ys.length - 1] !== rows - 1) ys.push(rows - 1);
      const nx = xs.length, ny = ys.length;
      const dxm = widthM / cols, dzm = widthM * aspect / rows;
      const verts = new Float32Array(nx * ny * 5);
      const at = (r, c) => h[Math.min(rows - 1, Math.max(0, r)) * cols + Math.min(cols - 1, Math.max(0, c))];
      for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
        const c = xs[i], r = ys[j], k = (j * nx + i) * 5;
        verts[k] = (c + 0.5) / cols * 2 - 1;
        verts[k + 1] = ((r + 0.5) / rows * 2 - 1) * aspect;
        verts[k + 2] = h[r * cols + c];
        verts[k + 3] = (at(r, c + 1) - at(r, c - 1)) / (2 * dxm);
        verts[k + 4] = (at(r + 1, c) - at(r - 1, c)) / (2 * dzm);
      }
      const idx = new (this.uint32 ? Uint32Array : Uint16Array)((nx - 1) * (ny - 1) * 6);
      let p = 0;
      for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
        idx[p++] = a; idx[p++] = c; idx[p++] = b; idx[p++] = b; idx[p++] = c; idx[p++] = d;
      }
      this.vbo = this.vbo || gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
      gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
      this.ibo = this.ibo || gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.ibo);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
      this.indexCount = idx.length;
      this.indexType = this.uint32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
      this.meshInfo = {vertices: nx * ny, triangles: idx.length / 3, step};
      this.idx = idx;
      this.lineIbo = null;
      const mask = new Uint8Array(cols * rows);
      for (let i = 0; i < mask.length; i++) mask[i] = classes[i] ? 255 : 0;
      this.texture('mask', cols, rows, mask, gl.LUMINANCE, gl.LINEAR, false);
      this.ready = true;
    }

    setSatellite(image) { this.texture('sat', 0, 0, image, this.gl.RGB, this.gl.LINEAR, true); this.hasSat = true; }
    /** rgba: Uint8Array cols*rows*4 (straight alpha). */
    setOverlay(rgba) { this.texture('over', this.cols, this.rows, rgba, this.gl.RGBA, this.gl.LINEAR, false); }

    buildWire() {
      const gl = this.gl, idx = this.idx, lines = new (this.uint32 ? Uint32Array : Uint16Array)(idx.length * 2);
      let p = 0;
      for (let t = 0; t < idx.length; t += 3) {
        lines[p++] = idx[t]; lines[p++] = idx[t + 1]; lines[p++] = idx[t + 1]; lines[p++] = idx[t + 2]; lines[p++] = idx[t + 2]; lines[p++] = idx[t];
      }
      this.lineIbo = gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.lineIbo);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, lines, gl.STATIC_DRAW);
      this.lineCount = lines.length;
    }

    resize(width, height, dpr) {
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
    }

    setCamera(prog, uniforms, cam) {
      const gl = this.gl, u = uniforms;
      gl.uniform2f(u.uView, cam.width, cam.height);
      gl.uniform1f(u.uYaw, cam.yaw); gl.uniform1f(u.uPitch, cam.pitch); gl.uniform1f(u.uScale, cam.scale);
      gl.uniform1f(u.uElev, cam.elev); gl.uniform1f(u.uFlat, cam.flat ? 1 : 0);
      gl.uniform1f(u.uCx, cam.cx); gl.uniform1f(u.uCz, cam.cz);
      gl.uniform1f(u.uWidthM, this.widthM); gl.uniform1f(u.uAspect, this.aspect);
    }

    bindMesh(prog) {
      const gl = this.gl, stride = 20;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
      const a = (name, size, offset) => { const loc = gl.getAttribLocation(prog, name); if (loc < 0) return; gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, offset); };
      a('aXZ', 2, 0); a('aH', 1, 8); a('aGrad', 2, 12);
    }

    /** cam: {width,height,yaw,pitch,scale,elev,flat,cx,cz,opacity,satellite,wire,shade} */
    draw(cam) {
      if (!this.ready) return;
      const gl = this.gl;
      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LESS); gl.disable(gl.BLEND);
      gl.useProgram(this.prog);
      this.setCamera(this.prog, this.u, cam);
      gl.uniform1f(this.u.uOpacity, cam.opacity); gl.uniform1f(this.u.uSatOn, cam.satellite && this.hasSat ? 1 : 0);
      gl.uniform1f(this.u.uHMin, this.hMin); gl.uniform1f(this.u.uHMax, this.hMax); gl.uniform1f(this.u.uShade, cam.shade);
      const bind = (unit, name, uniform) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, this.tex[name] || null); gl.uniform1i(uniform, unit); };
      bind(0, 'sat', this.u.uSat); bind(1, 'over', this.u.uOver); bind(2, 'mask', this.u.uMask);
      this.bindMesh(this.prog);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.ibo);
      gl.drawElements(gl.TRIANGLES, this.indexCount, this.indexType, 0);
      if (cam.wire) {
        if (!this.lineIbo) this.buildWire();
        gl.useProgram(this.lineProg);
        this.setCamera(this.lineProg, this.lu, cam);
        this.bindMesh(this.lineProg);
        gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.depthFunc(gl.LEQUAL);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.lineIbo);
        gl.drawElements(gl.LINES, this.lineCount, this.indexType, 0);
        gl.disable(gl.BLEND);
      }
    }
  }

  window.TerrainGL = TerrainGL;
})();
