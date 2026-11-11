# BACKEND LOGS
| Dev | Date | Title |
---------------
Huy | Nov 4 | [component_setup](#1-docker-and-component-set-up)
Huy | Nov 4 | [sample_endpoint](#2-smoke-testing-endpoint)
Huy | Nov 4 | [verified_testing](#3-verified-docker-container-running-and-smoke-test-endpoint-operational)
Huy | Nov 10| 


## 1. docker and component set up 
- added to dianella's docker file and compose
- added rabbitMQ, celery, and fastAPI as services
- Celery: only loads the CPU version of ultralytics for now
    - once we start inference dev we can play with different model size and version
- All containers look at OS environmental variables for connections. There are defaults though.

## 2. smoke testing endpoint
- current testing flow → `POST /api/v1/inference/smoke-test` → load `inference/data/uploads/multi.png` (or a generated 640×640 gray PNG if missing) → `_dispatch_and_await()` base64-encodes the image and `send_task("infrascan.inference", ...)` → task goes to RabbitMQ → worker picks it up → decode image bytes → sleep 4s → `inference_service()` (weights are preloaded per worker process via `worker_process_init` / `get_model` cache) → YOLO predict → save annotated image on the **worker** at `inference/data/outputs/annotated.jpg` → return `{status, result}` through the Celery result backend → FastAPI `async_result.get(timeout=120)`, prints the payload, and returns it in the HTTP response. This endpoint is blocking: the API waits until the worker finishes. TLDR: no other request to /smoke-test endpoint will be handled until the inference process is finished. For developing process in the future, we will make the api request non-blocking to inference by returning a job_id and a message that task is being queued.

## 3. Verified docker container running and smoke-test endpoint operational 
verified all container are started and running. Also, added the outputs folder to the docker volume. For testing purposes before the s3 bucket, input image and annotated results are in the outputs folder. Make sure no other services are running on the ports specified by the compose file. Maybe we can add the frontend into the docker compose file as well but we can just host the frontend on an s3 bucket as well. idk yet.

## 4. Integrating Geolocation method
- current method: Inverse Projection Mapping
- assumptions: 
            - Camera intrinsics and calibration is for my camera, galaxy flip 7 main lens
            - each image will have a source gps
            - detection model works and ready to go (using my nano model for now, slot in the small one when avail.)
- decision:
            - for now right, I will work on the pipeline intaking a single image. Once thats finished, video inference will be done by extracting some number of frames per second from a video and process them as group of images (maybe need a separate function that will reuse this single image processing function)
            - according to the schema, we need to do pre-processing, inference, post-processing, and persistence
                as of the minimum viable product, we will assume the happy path, meaning that all input images are clear, nothing in the pipeline breaks, device is fully supported. 

Current stats from geolocation method:

    Huy's Phone and Config
    - across 9 trials from a distance of 2-5.4 meters, mean diff is about -0.04 meters
    Dianella's Phone and Config
    - across 5 trials from a distance of 3-4.5 meters, mean diff is about -0.16 meters
    Esther's Phone and Config
    - across 5 trials from a distance of 3-4.5 meters, mean diff is about -0.08 meters
    Eileen's Phone and Config
    - across 5 trials from a distance of 3.4.5 meters, mean diff is about 0.41 meters

    Weighted mean of difference from 24 trials: .05 meters or about 1.9 inches

IMPORTANT NOTICE: The trials with Dianella, Esther, and Eileen were not done with complete control.

