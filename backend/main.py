from fastapi import FastAPI

app = FastAPI()

@app.get("/test")
def firstEndpoint():
    return "Hello InfraScan"