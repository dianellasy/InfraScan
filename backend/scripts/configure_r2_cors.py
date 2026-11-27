"""Allow browsers on the given origins to PUT uploads straight to the R2 bucket.

Direct uploads (POST /api/v1/inference/uploads) hand the browser a signed PUT
URL on the bucket's own domain, so the bucket needs a CORS rule for the
frontend's origin. Run once per bucket, and again when an origin changes:

    python scripts/configure_r2_cors.py https://infrascan.vercel.app http://localhost:5173

Reads R2_BUCKET, R2_ENDPOINT, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY from
the environment (or a .env file). The token needs Admin Read & Write on the
bucket to change its CORS policy; the app itself only needs Object Read & Write.
"""

import os
import sys

import boto3
from dotenv import load_dotenv


def main(origins: list[str]) -> int:
    if not origins or any(origin == "*" or origin.endswith("/") for origin in origins):
        print("Pass exact origins such as https://infrascan.vercel.app (no '*', no trailing '/').")
        return 2
    load_dotenv()
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    rules = {
        "CORSRules": [
            {
                "AllowedOrigins": origins,
                "AllowedMethods": ["PUT"],
                "AllowedHeaders": ["Content-Type"],
                "MaxAgeSeconds": 3600,
            }
        ]
    }
    s3.put_bucket_cors(Bucket=os.environ["R2_BUCKET"], CORSConfiguration=rules)
    print(f"CORS on {os.environ['R2_BUCKET']} now allows PUT from: {', '.join(origins)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
