# Endee Integration (NutriSense RAG)

## Mandatory repository steps (completed)
- Starred official repo: `https://github.com/endee-io/endee`
- Fork created: `https://github.com/text-ashish/endee`
- Fork checkout added locally at: `/Users/ashish/Nervesparks/endee`
- Upstream remote set to official Endee repo.

## 1. Start Endee server
From the forked Endee checkout:

```bash
cd /Users/ashish/Nervesparks/endee
docker compose up -d
```

Endee API should be available at `http://localhost:8080`.

## 2. Configure ai_service
Add these values to `/Users/ashish/Nervesparks/ai_service/.env`:

```bash
VECTOR_DB_BACKEND=endee
ENDEE_URL=http://localhost:8080
ENDEE_INDEX_NAME=recipes
ENDEE_SPACE_TYPE=cosine
ENDEE_PRECISION=int16
ENDEE_REBUILD_INDEX=true
# Optional only if auth enabled on Endee:
# ENDEE_AUTH_TOKEN=your_token
```

## 3. Install dependencies

```bash
cd /Users/ashish/Nervesparks/ai_service
pip install -r requirements.txt
```

## 4. Run service

```bash
python app.py
```

On startup, `build_vectorstore()` will create/recreate the Endee index and insert your recipe embeddings.
