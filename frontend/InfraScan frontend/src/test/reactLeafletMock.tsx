/* eslint-disable react-refresh/only-export-components */
import type { ReactNode } from "react";
import type { DivIcon, LatLngTuple, LeafletEventHandlerFnMap } from "leaflet";
import type { ViewportBounds } from "../issues/types.ts";

/** Stand-in for the Leaflet map used by `useMapEvents` in tests. */
export const fakeMap = {
  bounds: { south: 33.79, west: -118.0, north: 33.84, east: -117.9 } as ViewportBounds,
  handlers: {} as LeafletEventHandlerFnMap,
  getBounds() {
    const { south, west, north, east } = fakeMap.bounds;
    return {
      getSouth: () => south,
      getWest: () => west,
      getNorth: () => north,
      getEast: () => east,
    };
  },
};

export function resetFakeMap(bounds: ViewportBounds) {
  fakeMap.bounds = bounds;
  fakeMap.handlers = {};
}

/** Simulate the user panning or zooming the map. */
export function moveFakeMap(bounds: ViewportBounds) {
  fakeMap.bounds = bounds;
  fakeMap.handlers.moveend?.({ target: fakeMap } as never);
}

export function MapContainer({ children, bounds }: { children: ReactNode; bounds?: unknown }) {
  return (
    <div data-testid="map" data-bounds={JSON.stringify(bounds)}>
      {children}
    </div>
  );
}

export function TileLayer({ url, attribution }: { url: string; attribution: string }) {
  return <div data-testid="tile-layer" data-url={url} data-attribution={attribution} />;
}

type MarkerProps = {
  position: LatLngTuple;
  icon: DivIcon;
  title: string;
  eventHandlers?: { click?: () => void };
};

export function Marker({ position, icon, title, eventHandlers }: MarkerProps) {
  return (
    <button
      type="button"
      data-testid="issue-marker"
      data-lat={position[0]}
      data-lng={position[1]}
      aria-label={title}
      onClick={() => eventHandlers?.click?.()}
      dangerouslySetInnerHTML={{ __html: String(icon.options.html) }}
    />
  );
}

export function useMapEvents(handlers: LeafletEventHandlerFnMap) {
  fakeMap.handlers = handlers;
  return fakeMap;
}
