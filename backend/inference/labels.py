"""Class vocabulary of the shipped v2 model (infrascan-yolo26m-rdd2022).

Kept free of ML dependencies so the API process can import it without
ultralytics/torch. The order must match the training labels in
ml/data/prepare_rdd2022.py --manhole (0 D00, 1 D10, 2 D20, 3 D40, 4 D50 from RDD2022).

`DetectionRecord.issue_type` currently stores these model class IDs
provisionally. They are not permanent domain identifiers: a later issue-types
migration must define the mapping from model class IDs to issue types and
convert any stored values.
"""

CLASS_LABELS: dict[int, str] = {
    0: "Longitudinal crack",
    1: "Transverse crack",
    2: "Alligator crack",
    3: "Pothole",
    4: "Manhole cover",
}


def class_label(class_id: int) -> str:
    return CLASS_LABELS.get(class_id, f"class_{class_id}")
