class R2MediaStorageAdapter:
    def put(
      self, data: bytes, object_key: str, content_type: str
    ) -> None:
        """Store the image to R2 location using object_key """
        
    def get(self, key: str) -> bytes:
        """Using the key, find and return the image"""
        ...

    def delete(self, key: str) -> None:
        """Using the key, find and delete the image"""
        ...