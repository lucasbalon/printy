/**
 * Fast mesh simplification by vertex clustering, for the 3D preview.
 *
 * Vertices are snapped to a 3D grid and merged per cell; triangles that collapse
 * are dropped. Linear in the number of vertices, so a 2-million-triangle lamp
 * becomes a light preview in well under a second. The shape is kept, fine
 * surface detail is not: good enough to recognise a model, not to inspect it.
 */

export function triangleCount(positions: ArrayLike<number>, index: ArrayLike<number> | null): number {
  return index ? index.length / 3 : positions.length / 9;
}

export function simplify(
  positions: Float32Array,
  index: ArrayLike<number> | null,
  target: number,
): Float32Array {
  const vertexCount = positions.length / 3;
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i], y = positions[i + 1], z = positions[i + 2];
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    if (z > maxZ) maxZ = z;
  }
  const extent = Math.max(maxX - minX, maxY - minY, maxZ - minZ) || 1;
  const triangles = triangleCount(positions, index);
  const corner = (t: number, k: number) => (index ? index[t * 3 + k] : t * 3 + k);

  let res = Math.min(320, Math.max(24, Math.round(Math.sqrt(target) * 1.1)));
  let best: Float32Array | null = null;
  for (let attempt = 0; attempt < 4; attempt++) {
    const scale = (res - 1) / extent;
    const cellOf = new Int32Array(vertexCount);
    const cells = new Map<number, number>();
    const sums: number[] = [];
    for (let v = 0; v < vertexCount; v++) {
      const x = positions[v * 3], y = positions[v * 3 + 1], z = positions[v * 3 + 2];
      const key =
        Math.floor((x - minX) * scale) + res * (Math.floor((y - minY) * scale) + res * Math.floor((z - minZ) * scale));
      let id = cells.get(key);
      if (id === undefined) {
        id = cells.size;
        cells.set(key, id);
        sums.push(0, 0, 0, 0);
      }
      cellOf[v] = id;
      sums[id * 4] += x;
      sums[id * 4 + 1] += y;
      sums[id * 4 + 2] += z;
      sums[id * 4 + 3] += 1;
    }
    const out: number[] = [];
    for (let t = 0; t < triangles; t++) {
      const a = cellOf[corner(t, 0)], b = cellOf[corner(t, 1)], c = cellOf[corner(t, 2)];
      if (a === b || b === c || a === c) continue;
      for (const id of [a, b, c]) {
        const n = sums[id * 4 + 3];
        out.push(sums[id * 4] / n, sums[id * 4 + 1] / n, sums[id * 4 + 2] / n);
      }
    }
    best = new Float32Array(out);
    if (out.length / 9 <= target * 1.3) break;
    res = Math.max(24, Math.floor(res * 0.75));
  }
  return best ?? positions;
}
