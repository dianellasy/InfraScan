import exifr from "exifr";

export type PhotoMetadata = {
  /** `datetime-local` value (local time, minutes precision), when the photo has one. */
  capturedAt?: string;
  latitude?: number;
  longitude?: number;
};

const pad = (value: number) => String(value).padStart(2, "0");

/** Format a Date as a `datetime-local` input value in the browser's time zone. */
export function toDateTimeLocal(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/**
 * Read capture time and GPS from a JPEG's EXIF block. Returns an empty object
 * when the photo has no EXIF, or it cannot be parsed; never throws.
 */
export async function readPhotoMetadata(file: Blob): Promise<PhotoMetadata> {
  try {
    const tags = await exifr.parse(file, {
      gps: true,
      pick: ["DateTimeOriginal", "CreateDate", "GPSLatitude", "GPSLongitude",
        "GPSLatitudeRef", "GPSLongitudeRef"],
    });
    if (!tags) return {};
    const result: PhotoMetadata = {};
    const taken = tags.DateTimeOriginal ?? tags.CreateDate;
    if (taken instanceof Date && !Number.isNaN(taken.getTime())) {
      result.capturedAt = toDateTimeLocal(taken);
    }
    if (Number.isFinite(tags.latitude) && Number.isFinite(tags.longitude)) {
      result.latitude = tags.latitude;
      result.longitude = tags.longitude;
    }
    return result;
  } catch {
    return {};
  }
}
