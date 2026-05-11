from fastapi import FastAPI
from app.db.database import Base, engine
from app.routes.auth import router as auth_router

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Auth API",
    description="Production-level authentication and authorization API",
    version="1.0.0"
)

app.include_router(auth_router)

@app.get("/")
def root():
    return {"message": "Auth API is running!"}