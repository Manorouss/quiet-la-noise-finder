export function parsePilotViewHash(hash, model, periods) {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const parseId = (key) => {
    if (!params.has(key)) return null;
    const value = Number(params.get(key));
    return Number.isSafeInteger(value) ? value : null;
  };
  let camera = null;
  const cameraKeys = ['lng', 'lat', 'z'];
  if (cameraKeys.every((key) => params.has(key))) {
    const raw = cameraKeys.map((key) => params.get(key)?.trim() ?? '');
    if (raw.every((value) => value !== '')) {
      const [lng, lat, zoom] = raw.map(Number);
      if (Number.isFinite(lng) && lng >= -180 && lng <= 180
        && Number.isFinite(lat) && lat >= -90 && lat <= 90
        && Number.isFinite(zoom) && zoom >= 11 && zoom <= 19) {
        camera = { lng, lat, zoom };
      }
    }
  }
  const requestedPeriod = params.get('period');
  return {
    period: periods.includes(requestedPeriod) ? requestedPeriod : 'D',
    buildingPk: parseId('building'),
    receiverId: parseId('receiver'),
    mode3d: params.get('mode') === '3d',
    camera,
    modelMismatch: Boolean(params.get('model') && params.get('model') !== model),
  };
}
