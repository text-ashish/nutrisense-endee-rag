# src/rag.py
import os

import google.generativeai as genai
from google.api_core.exceptions import GoogleAPICallError, ResourceExhausted

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Please set GEMINI_API_KEY environment variable (or use .env)")

genai.configure(api_key=GEMINI_API_KEY)

# Default to flash model; override with GEMINI_MODEL in .env if needed.
_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
model = genai.GenerativeModel(_MODEL_NAME)


def _build_prompt(user_query, retrieved_metadatas, user_profile):
    """
    Build an instruction prompt that asks the LLM to:
    - Provide a personalized recipe (modify for health condition or goals)
    - Suggest substitutions for allergens
    - Provide nutrition-per-serving summary and explain why chosen
    """
    intro = (
        "You are a nutrition-aware assistant. Use the provided retrieved recipes and metadata to produce "
        "a personalized recommendation for the user. The output must be structured and concise.\n\n"
    )
    profile = f"User profile: {user_profile}\n\n"
    context = "Retrieved recipes (metadata):\n"
    for i, m in enumerate(retrieved_metadatas):
        context += f"--- Recipe {i+1} ---\n"
        context += f"Name: {m.get('recipe_name')}\n"
        context += f"Ingredients: {m.get('ingredients')}\n"
        context += f"Directions: {m.get('directions')}\n"
        context += f"Nutrition: {m.get('nutrition_normalized')}\n"
        context += f"Dietary labels: {m.get('dietary_labels')}\n"
        context += f"Allergens: {m.get('allergens')}\n"
        context += f"Substitutions: {m.get('substitutions')}\n"
        context += f"Health tags: {m.get('health_tags')}\n\n"

    instructions = (
        "Instructions:\n"
        "1) If a retrieved recipe exactly matches the user's query name, produce an adapted recipe for the user's health condition and goals.\n"
        "2) If recipe contains excluded allergens, propose specific substitutions and show the updated ingredients list.\n"
        "3) For health conditions (e.g., Diabetes, Hypertension, Heart-Friendly), reduce offending nutrients (e.g., sugar, sodium) and explain changes.\n"
        "4) Provide a short nutrition summary (calories, protein_g, fat_g, carbs_g, sugar_g) per serving after modifications.\n"
        "5) If no good candidate exists, provide a short, safe recommendation or explain why.\n\n"
        "Format the answer with headings: 'Selected Recipe', 'Adapted Ingredients', 'Instructions (short)', 'Nutrition per serving', 'Why chosen', 'Substitutions'.\n\n"
    )

    prompt = intro + profile + context + instructions + f"User query: {user_query}\n"
    return prompt


def generate_response(
    user_query,
    retrieved_metadatas,
    dietary_preference=None,
    exclude_allergens=None,
    health_condition=None,
    calorie_goal=0,
    protein_goal=0,
    fat_goal=0,
):
    user_profile = {
        "dietary_preference": dietary_preference,
        "exclude_allergens": exclude_allergens,
        "health_condition": health_condition,
        "calorie_goal": calorie_goal,
        "protein_goal": protein_goal,
        "fat_goal": fat_goal,
    }

    if not retrieved_metadatas:
        return "No suitable recipes were found for your filters. Try a broader query or relax constraints."

    prompt = _build_prompt(user_query, retrieved_metadatas, user_profile)

    try:
        response = model.generate_content(prompt)
        text = response.text if hasattr(response, "text") else (getattr(response, "content", None) or str(response))
        return text or "I could not generate a recommendation right now."
    except ResourceExhausted:
        return (
            "Recipe retrieval succeeded, but Gemini quota/rate limit was exceeded. "
            "Please try again shortly or switch to a model with available quota via GEMINI_MODEL."
        )
    except GoogleAPICallError:
        return "Recipe retrieval succeeded, but the LLM call failed temporarily. Please try again."
    except Exception:
        return "Recipe retrieval succeeded, but response generation failed. Please try again."
