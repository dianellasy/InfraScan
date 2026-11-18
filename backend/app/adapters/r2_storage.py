import boto3
import os
import dotenv
from uuid_extensions import uuid7

dotenv.load_dotenv()
class R2MediaStorageAdapter:
    def put(
      self, data: bytes, object_key: str, content_type: str
    ) -> None:
        """Store the image to R2 location using object_key """
        s3 = boto3.client(
        service_name="s3",
        endpoint_url=os.getenv("R2_ENDPOINT"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        )
        
        response = s3.put_object(
        Bucket=os.getenv("R2_BUCKET"),
        Key=f"input/upload/{object_key}.jpg",
        Body=data,
        ContentType=content_type,
        )
        print(f"Uploaded successfully. ETag: {response['ETag']}")


        
    def get(self, key: str) -> bytes:
        """Using the key, find and return the image"""
        ...

    def delete(self, key: str) -> None:
        """Using the key, find and delete the image"""
        ...



def test():
    # need to call put to test out the put function
    # so essentially we need to open an image as a file and then run file.read() to get the bytes
    with open("/home/cornelius/Desktop/InfraScan/backend/inference/data/uploads/multi.jpg", "rb") as file:
        data = file.read()
        object_key_id = uuid7()
        r2 = R2MediaStorageAdapter()
        r2.put(data, object_key_id, "image/jpeg")

if __name__ == "__main__":
    test()