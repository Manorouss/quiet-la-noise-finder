export function resolvePilotSelection(receiverId, requestedBuildingPk, receiversById, buildingsById) {
  const receiver = receiverId === null ? undefined : receiversById.get(receiverId);
  if (receiver) {
    const linkedBuildingPk = receiver.properties.building_pk;
    const buildingPk = Number.isInteger(linkedBuildingPk) && buildingsById.has(linkedBuildingPk)
      ? linkedBuildingPk
      : null;
    return { receiverId, buildingPk };
  }

  return {
    receiverId: null,
    buildingPk: requestedBuildingPk !== null && buildingsById.has(requestedBuildingPk)
      ? requestedBuildingPk
      : null,
  };
}
