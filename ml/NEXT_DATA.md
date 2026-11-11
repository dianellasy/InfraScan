# Next data: candidate datasets for the PRD classes RDD2022 does not cover

Checked on 11 Nov 2026 through the Kaggle CLI (dataset metadata, description
and file list). Only datasets whose Kaggle page we actually read are listed.
Sizes are the Kaggle download size. "Good enough?" is a first view from the
page, not from training on it. Licences are what the Kaggle page says; for
merged or re-uploaded data the original source licence may differ, so check
before shipping a model trained on it.

## Best single option

**Custom Multitask Indian Road Dataset (CMIRD)**
- Link: https://www.kaggle.com/datasets/ammanaveen/custom-multitask-indian-road-dataset-cmird
- Size: about 1.4 GB, 3,350 images at 1920x1080 (2,750 train, 600 val)
- Labels: boxes for pothole, crack, manhole, speed bump, waterlogging (plus car,
  bike, auto-rickshaw, bus, person). Also drivable area and lane masks.
- Licence: CC BY-NC-SA 4.0 (non-commercial only)
- Good enough? The best match to the PRD we found: 4 of the 6 PRD issue types
  (pothole, manhole, speed bump, water clogging) in one dataset, on Indian
  roads. The vehicle and person boxes could help with "encroachment (parked
  vehicles)". Small (3,350 images), so per-class counts need checking. The
  non-commercial licence is fine for a student project but not for a product.

## Open or unmaintained manholes

| Dataset | Link | Size | Labels | Licence | Good enough? |
| --- | --- | --- | --- | --- | --- |
| Road Damage Dataset: Potholes, Cracks and Manholes | https://www.kaggle.com/datasets/lorenzoarcioni/road-damage-dataset-potholes-cracks-and-manholes | 194 MB | YOLO: pothole, crack, manhole. 640x360, Italy, GoPro on a car and phone | MIT | Yes for "manhole present". It does not say open vs closed, so "open or unmaintained" still needs our own labels. |
| Manhole for YOLOv8 | https://www.kaggle.com/datasets/cubeai/manhole-for-yolov8 | 24 MB | YOLO, manhole (file names like close14.jpg suggest close-up shots) | CC0 | Small. Useful as extra examples only. |
| CMIRD | see above | | manhole | CC BY-NC-SA 4.0 | Yes, dashcam view on Indian roads. |

## Poorly designed speed breakers

| Dataset | Link | Size | Labels | Licence | Good enough? |
| --- | --- | --- | --- | --- | --- |
| Speed Bump Dataset | https://www.kaggle.com/datasets/ziya07/speed-bump-dataset | 1.9 GB | Image classes: bump, crack, potholes, road | CC0 | Classification, not boxes. Could pre-train or be boxed by hand. |
| yolov5 speed breaker detection | https://www.kaggle.com/datasets/saktheeswaranswan/yolov5-speed-breaker-detection-driver-alert-system | 914 MB | speed breaker boxes (YOLOv5) | not checked | Not opened in detail yet. |
| pothole-and-speed-breaker-dataset | https://www.kaggle.com/datasets/sannyshankaranml/pothole-and-speed-breaker-dataset | 504 MB | Roboflow export, pothole and speed breaker | unknown | Licence unknown, so risky. |
| CMIRD | see above | | speed bump | CC BY-NC-SA 4.0 | Yes. |

Note: every dataset here says "speed breaker present". None labels "poorly
designed". That needs a rule (for example missing paint markings) or our own labels.

## Water clogging

| Dataset | Link | Size | Labels | Licence | Good enough? |
| --- | --- | --- | --- | --- | --- |
| CMIRD | see above | | waterlogging boxes | CC BY-NC-SA 4.0 | The only box-labelled source we found. Probably few examples. |

No other road waterlogging detection dataset came up in Kaggle search
("waterlogging road").

## Broken footpaths and missing walkways

Nothing found on Kaggle (search "footpath sidewalk damage" returned no
results). Next places to look: sidewalk datasets on Roboflow Universe and
academic sidewalk-condition datasets. Not checked yet.

## Encroachments (shops, parked vehicles, debris)

| Dataset | Link | Size | Labels | Licence | Good enough? |
| --- | --- | --- | --- | --- | --- |
| Road Damage 6-Class YOLO Dataset | https://www.kaggle.com/datasets/rahul1nexus/road-damage-6-class-yolo-dataset | 3.0 GB | YOLO: pothole, crack, debris, manhole, speed breaker, road barricade. 52,334 images, merged from several sources | CC0 on Kaggle (merged data, source licences not listed) | Big and covers debris, manhole and speed breaker. Merged from other datasets, so it may contain RDD2022 images (risk of leakage into our val/test) and mixed label quality. Check overlap before using. |
| CMIRD | see above | | car, bike, bus, auto-rickshaw, person | CC BY-NC-SA 4.0 | Vehicle boxes only. "Parked on the footpath" needs a rule on top. |

Shops encroaching on the road: nothing found.

## Night and low-light potholes

| Dataset | Link | Size | Labels | Licence | Good enough? |
| --- | --- | --- | --- | --- | --- |
| Nighttime Pothole Detection | https://www.kaggle.com/datasets/phoenixz14/nighttime-pothole-detection | 517 MB | YOLO pothole. Folder is named `night_synthetic`, so images look like darkened daytime images | Apache 2.0 | Synthetic night. Fine for augmentation, but not a fair test of real night images. |
| Night Potholes YOLOv8 format | https://www.kaggle.com/datasets/tavishisharma09/night-potholes-yolov8-format | 111 MB | YOLO pothole, Roboflow export (several augmented copies of each image) | MIT | Small. Augmented copies must be grouped so they do not leak across splits. |

## Extra daytime pothole data (for the pothole specialist)

| Dataset | Link | Size | Labels | Licence |
| --- | --- | --- | --- | --- |
| Pothole Detection (andrewmvd) | https://www.kaggle.com/datasets/andrewmvd/pothole-detection | 351 MB | 665 images, VOC boxes, pothole | "other" |
| Potholes-Detection-YOLOv8 | https://www.kaggle.com/datasets/anggadwisunarto/potholes-detection-yolov8 | 753 MB | 1,581 images, YOLO pothole | CC0 |
| Pothole Image Segmentation (farzadnekouei) | https://www.kaggle.com/datasets/farzadnekouei/pothole-image-segmentation-dataset | about 50 MB | 780 images (720 train = about 240 photos x 3 augmented copies, 60 val), YOLOv8 pothole polygons, stretched to 640x640, plus a sample video | Apache 2.0 |
| RDD2022 India Pothole D40 | https://www.kaggle.com/datasets/vidishbijalwan/rdd2022-india-pothole-d40 | 84 MB | RDD2022 India subset (same data we already use) | not checked |

Any extra pothole data is judged only on the same RDD2022 val split, so we can
see if it really helps.

Note on the farzadnekouei set: it is small and the train images are
augmented copies (file names like `pic-10-_jpg.rf.<hash>.jpg`), so group by
the `pic-N` part to avoid leakage. Its masks give pothole area, which could
later replace the box-area severity heuristic. The sample video is handy for
testing the video stretch goal.
