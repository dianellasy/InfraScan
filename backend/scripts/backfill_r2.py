"""Copy legacy database media to R2, verify bytes, and retain the originals.

Run from backend: python -m scripts.backfill_r2 [--apply]
The default reports counts without reading payloads or contacting R2.
"""
import argparse
import hashlib
import sqlalchemy as sa
from app.db import create_db_engine, create_session_factory
from app.models import InputMedia, EvidenceMedia
from app.adapters.r2_storage import R2MediaStorageAdapter


def backfill(session_factory, storage):
    counts = {}
    for model in (InputMedia, EvidenceMedia):
        count = 0
        last_id = None
        while True:
            # Never keep a database transaction open during an object-store call.
            with session_factory() as session:
                query = sa.select(model).where(model.data.is_not(None), model.object_storage_key.is_(None))
                if last_id is not None:
                    query = query.where(model.media_id > last_id)
                row = session.scalar(query.order_by(model.media_id).limit(1))
                if row is None:
                    break
                media_id, payload, content_type = row.media_id, row.data, row.content_type
            key = f"legacy/{model.__tablename__}/{media_id}"
            storage.put(payload, key, content_type)
            if hashlib.sha256(storage.get(key)).digest() != hashlib.sha256(payload).digest():
                raise RuntimeError(f"Object verification failed for {media_id}")
            with session_factory() as session, session.begin():
                updated = session.execute(sa.update(model).where(
                    model.media_id == media_id, model.object_storage_key.is_(None), model.data == payload
                ).values(object_storage_key=key)).rowcount
                count += updated
            last_id = media_id
        counts[model.__tablename__] = count
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = create_db_engine()
    factory = create_session_factory(engine)
    try:
        if args.apply:
            print(backfill(factory, R2MediaStorageAdapter()))
        else:
            with factory() as session:
                print({m.__tablename__: session.scalar(sa.select(sa.func.count()).select_from(m).where(
                    m.data.is_not(None), m.object_storage_key.is_(None))) for m in (InputMedia, EvidenceMedia)})
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
