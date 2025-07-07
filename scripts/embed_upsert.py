import os
import json
from tqdm import tqdm
from pinecone import Pinecone, ServerlessSpec
import openai

# === CONFIG ===
CHUNKS_PATH = r"C:\Maximos2\data\chunks"
openai.api_key = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
INDEX_NAME = "ordinance"

# === INIT Pinecone ===
pc = Pinecone(api_key=PINECONE_API_KEY)

if not pc.has_index(INDEX_NAME):
    pc.create_index(
        name=INDEX_NAME,
        dimension=1536,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1")
    )

index = pc.Index(INDEX_NAME)

# === LOAD + PREPARE CHUNKED DATA ===
data = []
for filename in tqdm(os.listdir(CHUNKS_PATH), desc="Loading chunks"):
    if filename.endswith(".json"):
        full_path = os.path.join(CHUNKS_PATH, filename)
        with open(full_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)
            for i, item in enumerate(chunks):
                data.append({
                    "id": f"{filename}_{i}",
                    "text": item["text"],
                    "metadata": {
                        "filename": filename,
                        "chunk_id": i,
                        "source": item.get("source", "unknown")
                    }
                })

print(f"Loaded {len(data)} chunks.")

# === EMBED TEXTS ===
def embed(texts: list[str]):
    response = openai.embeddings.create(
        input=texts,
        model="text-embedding-ada-002"
    )
    return [r.embedding for r in response.data]


# === CHUNKED UPSERT TO PINECONE ===
BATCH_SIZE = 100
for i in tqdm(range(0, len(data), BATCH_SIZE), desc="Embedding & upserting"):
    batch = data[i:i + BATCH_SIZE]
    texts = [d["text"] for d in batch]
    embeddings = embed(texts)

    vectors = []
    for d, e, text in zip(batch, embeddings, texts):
        metadata = d.get("metadata", {})
        metadata["text"] = text  # Ensure 'text' key exists

        vectors.append({
            "id": d["id"],
            "values": e,
            "metadata": metadata
        })

    index.upsert(vectors=vectors)

print("🎉 All chunks embedded and upserted!")

# === OPTIONAL QUERY ===
query = "Do I need to mow my lawn?"
query_embed = embed([query])[0]

results = index.query(
    namespace="",
    vector=query_embed,
    top_k=3,
    include_metadata=True
)

print("\nTop results:")
for match in results["matches"]:
    print(f"Score: {match['score']:.4f}")
    print(f"Metadata: {match['metadata']}\n")
