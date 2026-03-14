# app.py
import os
import time
import pickle
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# --- Load Environment Variables ---
load_dotenv()

# --- Imports from your src folder ---
from src.embeddings import get_embeddings
from src.vectorstore import retrieve, build_vectorstore
from src.rag import generate_response

# --- Global State ---
ml_context = {}

# --- Lifespan Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup. Loads data and 'hydrates' the vector store.
    """
    artifact_path = "data/nutrisense_data.pkl"
    print("📂 Loading pre-computed artifacts...")
    
    if os.path.exists(artifact_path):
        try:
            with open(artifact_path, "rb") as f:
                data = pickle.load(f)
                
            # 1. Load DataFrame
            ml_context["df"] = data["df"]
            embeddings = data["embeddings"]
            
            # 2. Rebuild Vector Store Instantly
            # Since we already have embeddings, this is extremely fast (milliseconds)
            print("⚡ Hydrating vector store from pre-computed embeddings...")
            build_vectorstore(ml_context["df"], embeddings)
            
            print("✅ System ready. RAM usage stable.")
        except Exception as e:
            print(f"❌ Error loading pickle file: {e}")
    else:
        print(f"❌ CRITICAL: {artifact_path} not found!")

    yield
    print("🛑 Shutting down...")

# --- Initialize FastAPI ---
app = FastAPI(title="NutriSense API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RecipeRequest(BaseModel):
    query: str
    dietary: str = "None"
    health: str = "None"
    allergens: str = ""
    calories: float = 0
    protein: float = 0
    fat: float = 0

# --- Core Logic ---
def personalized_recipe(query, dietary, health, allergens, calories, protein, fat):
    if "df" not in ml_context:
         # Fallback if file load failed
        return {"error": "Server data not loaded yet."}

    if not query:
        return "⚠️ Please enter a recipe name or description."

    exclude_allergens = [a.strip() for a in allergens.split(",")] if allergens else None
    start_time = time.time()
    
    # Embed the user query
    query_emb = get_embeddings([query.lower()])[0]

    # Retrieve
    retrieved = retrieve(
        query_embedding=query_emb,
        k=6,
        dietary_filter=dietary,
        exclude_allergens=exclude_allergens,
        health_condition=health
    )

    # Generate Response
    answer = generate_response(
        query,
        retrieved_metadatas=retrieved,
        dietary_preference=dietary,
        exclude_allergens=exclude_allergens,
        health_condition=health,
        calorie_goal=calories,
        protein_goal=protein,
        fat_goal=fat
    )

    latency = round(time.time() - start_time, 2)
    return {"latency": latency, "recommendation": answer}

@app.post("/get_recipe")
def get_recipe(request: RecipeRequest):
    return personalized_recipe(
        request.query,
        request.dietary,
        request.health,
        request.allergens,
        request.calories,
        request.protein,
        request.fat
    )

@app.get("/")
def read_root():
    # Health check for Render
    status = "ready" if "df" in ml_context else "loading"
    return {"status": status}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("app:app", host="0.0.0.0", port=port)