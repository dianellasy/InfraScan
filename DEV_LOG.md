# BACKEND LOGS
| Dev | Date | Title |
---------------
Huy | Nov 4. | [component_setup](#1-docker-and-component-set-up)
Huy | Nov 4. | [sample_endpoint](#2-smoke-testing-endpoint)


## 1. docker and component set up 
- added to dianella's docker file and compose
- added rabbitMQ, celery, and fastAPI as services
- Celery: only loads the CPU version of ultralytics for now
    - once we start inference dev we can play with different model size and version
- All containers look at OS environmental variables for connections. There are defaults though.

## 2. smoke testing endpoint
- current testing flow → `POST /api/v1/inference/smoke-test` → load `inference/data/uploads/multi.png` (or a generated 640×640 gray PNG if missing) → `_dispatch_and_await()` base64-encodes the image and `send_task("infrascan.inference", ...)` → task goes to RabbitMQ → worker picks it up → decode image bytes → sleep 4s → `inference_service()` (weights are preloaded per worker process via `worker_process_init` / `get_model` cache) → YOLO predict → save annotated image on the **worker** at `inference/data/outputs/annotated.jpg` → return `{status, result}` through the Celery result backend → FastAPI `async_result.get(timeout=120)`, prints the payload, and returns it in the HTTP response. This endpoint is blocking: the API waits until the worker finishes.