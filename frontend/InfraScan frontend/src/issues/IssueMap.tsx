import { memo, useEffect, useMemo } from "react";
import type {
  LatLngBoundsExpression,
  LatLngTuple,
  LeafletEvent,
  Map as LeafletMap,
} from "leaflet";
import { MapContainer, Marker, TileLayer, useMapEvents } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import "leaflet/dist/leaflet.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.Default.css";
import { normalizeViewport } from "./api.ts";
import { OSM_ATTRIBUTION, OSM_TILE_URL } from "./constants.ts";
import { createIssueIcon, issueMarkerTitle } from "./markerIcon.ts";
import type { IssueFeature, LonLat, ViewportBounds } from "./types.ts";

type LocatedIssueFeature = IssueFeature & {
  geometry: { type: "Point"; coordinates: LonLat };
};

function isLocated(feature: IssueFeature): feature is LocatedIssueFeature {
  return feature.geometry?.type === "Point" && feature.geometry.coordinates.length === 2;
}

/** GeoJSON is [longitude, latitude]; Leaflet expects [latitude, longitude]. */
function featureLatLng(feature: LocatedIssueFeature): LatLngTuple {
  const [longitude, latitude] = feature.geometry.coordinates;
  return [latitude, longitude];
}

function viewportOf(map: LeafletMap): ViewportBounds {
  const bounds = map.getBounds();
  return normalizeViewport({
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  });
}

/** `onChange` should be stable; it is called for the initial view and after every move or zoom. */
function ViewportReporter({ onChange }: { onChange: (bounds: ViewportBounds) => void }) {
  const handlers = useMemo(
    () => ({
      moveend: (event: LeafletEvent) => onChange(viewportOf(event.target as LeafletMap)),
      zoomend: (event: LeafletEvent) => onChange(viewportOf(event.target as LeafletMap)),
    }),
    [onChange],
  );
  const map = useMapEvents(handlers);
  useEffect(() => onChange(viewportOf(map)), [map, onChange]);
  return null;
}

type IssueMarkerProps = {
  feature: LocatedIssueFeature;
  selected: boolean;
  onSelect: (issueId: string) => void;
};

const IssueMarker = memo(function IssueMarker({ feature, selected, onSelect }: IssueMarkerProps) {
  const { properties } = feature;
  const icon = useMemo(() => createIssueIcon(properties, selected), [properties, selected]);
  const eventHandlers = useMemo(() => ({ click: () => onSelect(feature.id) }), [feature.id, onSelect]);
  return (
    <Marker
      position={featureLatLng(feature)}
      icon={icon}
      title={issueMarkerTitle(properties)}
      alt={issueMarkerTitle(properties)}
      zIndexOffset={selected ? 1000 : 0}
      eventHandlers={eventHandlers}
    />
  );
});

export type IssueMapProps = {
  features: IssueFeature[];
  selectedIssueId: string | null;
  initialBounds: LatLngBoundsExpression;
  onSelectIssue: (issueId: string) => void;
  onViewportChange: (bounds: ViewportBounds) => void;
};

export function IssueMap({
  features,
  selectedIssueId,
  initialBounds,
  onSelectIssue,
  onViewportChange,
}: IssueMapProps) {
  const located = useMemo(() => features.filter(isLocated), [features]);
  return (
    <MapContainer
      className="issue-map"
      bounds={initialBounds}
      boundsOptions={{ padding: [36, 36] }}
      scrollWheelZoom
    >
      <TileLayer url={OSM_TILE_URL} attribution={OSM_ATTRIBUTION} maxZoom={19} />
      <ViewportReporter onChange={onViewportChange} />
      <MarkerClusterGroup
        chunkedLoading
        showCoverageOnHover={false}
        maxClusterRadius={45}
        disableClusteringAtZoom={17}
      >
        {located.map((feature) => (
          <IssueMarker
            key={feature.id}
            feature={feature}
            selected={feature.id === selectedIssueId}
            onSelect={onSelectIssue}
          />
        ))}
      </MarkerClusterGroup>
    </MapContainer>
  );
}
