export function tileIntersectsBounds(tileBounds, bounds) {
  if (!Array.isArray(tileBounds) || tileBounds.length !== 4 || !bounds) return false;
  const west = bounds.getWest?.() ?? bounds.west;
  const south = bounds.getSouth?.() ?? bounds.south;
  const east = bounds.getEast?.() ?? bounds.east;
  const north = bounds.getNorth?.() ?? bounds.north;
  return tileBounds[0] <= east && tileBounds[2] >= west && tileBounds[1] <= north && tileBounds[3] >= south;
}

export function expectedBuildingTileIds(building, tiles, defaultTileId) {
  const sourceId = String(building?.properties?.source_bld_id ?? '');
  const sharedTileIds = tiles
    .filter((tile) => (tile.provenance?.cross_tile_duplicate_building_source_ids ?? []).map(String).includes(sourceId))
    .map((tile) => tile.tile_id);
  return [...new Set([...(sharedTileIds.length ? [defaultTileId] : []), ...sharedTileIds, ...(building?.properties?.source_tile_ids ?? [])])].sort();
}

export function savedBuildingRecord(building, model) {
  return {
    buildingPk: building.properties.building_pk,
    buildingKey: building.properties.building_key,
    sourceBldId: String(building.properties.source_bld_id),
    sourceTileIds: [...building.properties.source_tile_ids],
    model,
  };
}

export function normalizeSavedBuilding(value, defaultTileId, allowedTileIds) {
  if (!value || !Number.isSafeInteger(value.buildingPk)) return null;
  const sourceBldId = value.sourceBldId === null || value.sourceBldId === undefined ? '' : String(value.sourceBldId);
  const buildingKey = typeof value.buildingKey === 'string' && value.buildingKey
    ? value.buildingKey
    : sourceBldId ? `lariac:${sourceBldId}` : `${defaultTileId}:${value.buildingPk}`;
  const keyTile = buildingKey.includes(':') ? buildingKey.split(':', 1)[0] : defaultTileId;
  const savedTiles = Array.isArray(value.sourceTileIds) ? value.sourceTileIds.filter((tileId) => allowedTileIds.includes(tileId)) : [];
  const sourceTileIds = [...new Set(savedTiles.length ? savedTiles : [allowedTileIds.includes(keyTile) ? keyTile : defaultTileId])].sort();
  return { ...value, buildingPk: value.buildingPk, buildingKey, sourceBldId, sourceTileIds };
}

function stableBuildingKey(feature) {
  const explicit = feature.properties?.building_key;
  if (typeof explicit === 'string' && explicit) return explicit;
  const sourceId = feature.properties?.source_bld_id;
  if (sourceId !== null && sourceId !== undefined && String(sourceId)) return `lariac:${sourceId}`;
  throw new Error('pilot building has no stable SOURCE_BLD_ID key');
}

function stableReceiverKey(feature, tileId) {
  const explicit = feature.properties?.receiver_key;
  if (typeof explicit === 'string' && explicit) return explicit;
  const id = feature.properties?.id;
  if (!Number.isSafeInteger(Number(id))) throw new Error(`pilot receiver in ${tileId} has no stable numeric source key`);
  return `${tileId}:${id}`;
}

export function normalizePilotTile(tileId, receiversDoc, buildingsDoc) {
  const receiverFeatures = receiversDoc?.features;
  const buildingFeatures = buildingsDoc?.features;
  if (!Array.isArray(receiverFeatures) || !Array.isArray(buildingFeatures)) throw new Error(`tile ${tileId} is not a valid GeoJSON package`);
  const buildingsByPk = new Map();
  const buildings = buildingFeatures.map((feature) => {
    const key = stableBuildingKey(feature);
    const pk = Number(feature.properties?.building_pk);
    if (!Number.isSafeInteger(pk) || buildingsByPk.has(pk)) throw new Error(`tile ${tileId} has invalid/duplicate building PK ${pk}`);
    const receiverKeys = Array.isArray(feature.properties?.receiver_keys)
      ? feature.properties.receiver_keys
      : (feature.properties?.receiver_ids ?? []).map((id) => `${tileId}:${id}`);
    const normalized = {
      ...feature,
      properties: {
        ...feature.properties,
        id: key,
        building_key: key,
        source_tile_ids: [tileId],
        receiver_keys: [...receiverKeys],
      },
    };
    buildingsByPk.set(pk, normalized);
    return normalized;
  });
  const receivers = receiverFeatures.map((feature) => {
    const props = feature.properties ?? {};
    const id = Number(props.id);
    const key = stableReceiverKey(feature, tileId);
    const rawBuildingPk = props.building_pk === null || props.building_pk === undefined ? null : Number(props.building_pk);
    const linked = rawBuildingPk === null ? undefined : buildingsByPk.get(rawBuildingPk);
    return {
      ...feature,
      id: key,
      properties: {
        ...props,
        id,
        receiver_key: key,
        source_tile: tileId,
        building_key: props.building_key ?? linked?.properties?.building_key ?? null,
      },
    };
  });
  return { tileId, receivers, buildings, manifest: receiversDoc.metadata ?? {} };
}

export function mergePilotTiles(tileEntries) {
  const receiversByKey = new Map();
  const buildingsByKey = new Map();
  const masks = new Set();
  const loadedTileIds = [];
  for (const tile of tileEntries) {
    if (!tile || loadedTileIds.includes(tile.tileId)) continue;
    loadedTileIds.push(tile.tileId);
    for (const receiver of tile.receivers) {
      const key = receiver.properties.receiver_key;
      if (receiversByKey.has(key)) throw new Error(`duplicate stable receiver key across tiles: ${key}`);
      receiversByKey.set(key, receiver);
      if (receiver.properties.masked) masks.add(key);
    }
    for (const building of tile.buildings) {
      const key = building.properties.building_key;
      const prior = buildingsByKey.get(key);
      if (!prior) {
        buildingsByKey.set(key, building);
        continue;
      }
      if (JSON.stringify(prior.geometry) !== JSON.stringify(building.geometry)) throw new Error(`building ${key} has conflicting footprint geometry across tiles`);
      const sourceTileIds = [...new Set([...prior.properties.source_tile_ids, ...building.properties.source_tile_ids])].sort();
      const receiverKeys = [...new Set([...prior.properties.receiver_keys, ...building.properties.receiver_keys])].sort();
      buildingsByKey.set(key, {
        ...prior,
        properties: {
          ...prior.properties,
          source_tile_ids: sourceTileIds,
          receiver_keys: receiverKeys,
          receiver_ids: receiverKeys.map((receiverKey) => receiversByKey.get(receiverKey)?.properties.id).filter(Number.isSafeInteger),
        },
      });
    }
  }
  for (const [key, building] of buildingsByKey) {
    const samples = building.properties.receiver_keys.map((receiverKey) => receiversByKey.get(receiverKey)).filter(Boolean);
    const periods = {};
    for (const period of ['D', 'E', 'N']) {
      const values = samples.map((receiver) => receiver.properties?.[period]?.laeq).filter((value) => Number.isFinite(value) && value !== -99);
      periods[period] = {
        min: values.length ? Math.min(...values) : null,
        max: values.length ? Math.max(...values) : null,
        receiver_count: samples.length,
        unavailable_count: samples.length - values.length,
      };
    }
    buildingsByKey.set(key, { ...building, properties: { ...building.properties, receiver_count: samples.length, periods } });
  }
  const receivers = [...receiversByKey.values()].sort((a, b) => a.properties.receiver_key.localeCompare(b.properties.receiver_key));
  const buildings = [...buildingsByKey.values()].sort((a, b) => a.properties.building_key.localeCompare(b.properties.building_key));
  return {
    receivers,
    buildings,
    loadedTileIds: loadedTileIds.sort(),
    maskedReceiverKeys: [...masks].sort(),
  };
}
