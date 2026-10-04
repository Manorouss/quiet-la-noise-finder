import type { CustomLayerInterface, CustomRenderMethodInput, Map as MapLibreMap } from 'maplibre-gl';

export type SplatScene = { url: string; origin: [number, number]; originZ: number; label: string };

// A MapLibre custom layer that draws a Gaussian-splat scene (Spark on three.js) in the map's own
// WebGL context. Scene coordinates are metres: x east, y north, z up, relative to `origin`.
// MapLibre gives the pure projection P and, in defaultProjectionData.mainMatrix, the full MVP for
// mercator [0..1] coordinates (modelViewProjectionMatrix is in world pixels). The camera's view
// matrix is V = P^-1 * MVP, so Spark gets a real perspective camera (correct splat footprints)
// and a real camera position for its radial depth sort.
export async function createSplatLayer(map: MapLibreMap, scene: SplatScene, onLoad?: () => void): Promise<CustomLayerInterface> {
  const [THREE, { SparkRenderer, SplatMesh }, maplibre] = await Promise.all([import('three'), import('@sparkjsdev/spark'), import('maplibre-gl')]);
  let renderer: InstanceType<typeof THREE.WebGLRenderer> | null = null;
  const three = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera();
  // The map supplies the camera every frame; stop three.js from rebuilding it from position/rotation.
  camera.matrixAutoUpdate = false;
  camera.matrixWorldAutoUpdate = false;
  const P = new THREE.Matrix4();
  const MVP = new THREE.Matrix4();
  const model = new THREE.Matrix4();
  const view = new THREE.Matrix4();
  return {
    id: 'splat-scene',
    type: 'custom',
    renderingMode: '3d',
    onAdd(_map, gl) {
      renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl as WebGL2RenderingContext, antialias: true });
      renderer.autoClear = false;
      const spark = new SparkRenderer({ renderer, sortRadial: true });
      three.add(spark);
      const mesh = new SplatMesh({ url: scene.url, onLoad: () => { map.triggerRepaint(); onLoad?.(); } });
      three.add(mesh);
      (window as unknown as { __quietSplat?: unknown }).__quietSplat = { mesh, spark, three };
      Promise.resolve((mesh as unknown as { initialized?: Promise<unknown> }).initialized).catch((error) => console.error('splat scene failed to load', error));
    },
    render(_gl, args: CustomRenderMethodInput) {
      if (!renderer) return;
      const terrainZ = map.terrain ? (map.queryTerrainElevation(scene.origin) ?? 0) : 0;
      const anchor = maplibre.MercatorCoordinate.fromLngLat(scene.origin, map.terrain ? terrainZ : 0);
      const s = anchor.meterInMercatorCoordinateUnits();
      model.makeTranslation(anchor.x, anchor.y, anchor.z).multiply(new THREE.Matrix4().makeScale(s, -s, s));
      P.fromArray(args.projectionMatrix as unknown as number[]);
      MVP.fromArray(Array.from(args.defaultProjectionData.mainMatrix as ArrayLike<number>));
      view.copy(P).invert().multiply(MVP).multiply(model);
      camera.projectionMatrix.copy(P);
      camera.projectionMatrixInverse.copy(P).invert();
      camera.matrixWorldInverse.copy(view);
      camera.matrixWorld.copy(view).invert();
      camera.matrix.copy(camera.matrixWorld);
      camera.matrix.decompose(camera.position, camera.quaternion, camera.scale);
      renderer.resetState();
      renderer.render(three, camera);
      map.triggerRepaint();
    },
    onRemove() {
      renderer?.dispose();
      renderer = null;
    },
  };
}
