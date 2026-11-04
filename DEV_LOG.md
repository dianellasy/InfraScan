# BACKEND LOGS
| Dev | Date | Title |
---------------
Huy | Nov 4. | [component_setup](#1-docker-and-component-set-up)
Huy | Nov 4. | [sample_endpoint](#2-smoke-testing-endpoint)
Huy | Nov 4. | [verified_testing](#3-verified-docker-container-running-and-smoke-test-endpoint-operational)


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