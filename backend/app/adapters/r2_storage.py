import boto3
import os
import dotenv
from contextlib import closing
from uuid_extensions import uuid7

dotenv.load_dotenv()
class R2MediaStorageAdapter:
    def __init__(self) -> None:
        self._bucket = os.environ["R2_BUCKET"]
        self._s3 = boto3.client(
            service_name="s3",
            endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    def put(
      self, data: bytes, object_key: str, content_type: str
    ) -> None:
        """Store the image to R2 location using object_key """
        response = self._s3.put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
        print(f"Uploaded successfully. ETag: {response['ETag']}")

    def get(self, key: str) -> bytes:
        """Using the key, find and return the image"""
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        with closing(response["Body"]) as body:
            return body.read()

    def delete(self, key: str) -> None:
        """Using the key, find and delete the image"""
        self._s3.delete_object(Bucket=self._bucket, Key=key)



def test():
    # need to call put to test out the put function
    # so essentially we need to open an image as a file and then run file.read() to get the bytes
    with open("/home/cornelius/Desktop/InfraScan/backend/inference/data/uploads/multi.jpg", "rb") as file:
        data = file.read()
        object_key = f"images/input/{uuid7()}.jpg"
        r2 = R2MediaStorageAdapter()
        r2.put(data, object_key, "image/jpeg")

if __name__ == "__main__":
    test()
